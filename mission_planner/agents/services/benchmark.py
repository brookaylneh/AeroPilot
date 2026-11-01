"""Measure the warm model HTTP contract from the mission host without retries."""

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.agents.models import Glimmer, Sam
from mission_planner.agents.perception import analyst
from mission_planner.agents.perception.configuration import crop, load_configuration, prepare_frame
from mission_planner.environment import ROOT, load_env

TOLERANCE = {"box_px": 0.5, "confidence": 0.001, "mask_area_px": 8, "mask_area_fraction": 0.005}


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def percentiles(values):
    assert values and np.isfinite(values).all()
    return {f"p{p}": float(np.percentile(values, p)) for p in (50, 95)}


def prepare(directory):
    config = load_configuration()
    frames = []
    for path in sorted((ROOT / ".local/data/calibration").glob("*/frame-*.jpg")):
        with Image.open(path) as frame:
            if frame.size == (3840, 2160):
                frames.append(path)
    assert len(frames) >= 40
    selected = [frames[i] for i in np.linspace(0, len(frames) - 1, 40, dtype=int)]
    save(
        directory / "protocol.json",
        {
            "sam_threshold": config["sam_threshold"],
            "prompts": [config["person_prompt"]],
            "tolerance": TOLERANCE,
            "equivalence": "Prompt keys and counts identical; one-to-one box matching by minimum maximum-coordinate distance, then all numeric tolerances must pass.",
            "sam_requests_per_stream": 40,
            "glimmer_requests_per_stream": 20,
            "health_requests_per_server": 50,
            "frames": [
                {
                    "path": str(p.relative_to(ROOT)),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in selected
            ],
            "template": analyst.TEMPLATE,
            "sampling": Glimmer.SAMPLING,
        },
    )


def streams(directory, name, concurrency, count, model_type, request):
    output = directory / (name + ".jsonl")
    lock, barrier = threading.Lock(), threading.Barrier(concurrency)
    with output.open("x") as stream, output.with_suffix(".responses.jsonl").open("x") as raw:

        def lane(index):
            model = model_type()

            def record(response):
                response.read()
                with lock:
                    raw.write(
                        json.dumps(
                            {
                                "stream": index,
                                "request": number,
                                "path": response.request.url.path,
                                "status_code": response.status_code,
                                "body": response.text,
                            }
                        )
                        + "\n"
                    )
                    raw.flush()

            model.http.event_hooks["response"].append(record)
            try:
                barrier.wait(timeout=60)
                for number in range(count):
                    row = {"stream": index, "request": number, "started_at_unix_s": time.time()}
                    timing = {}
                    started = time.perf_counter()
                    try:
                        row["result"] = request(model, number, timing, index)
                    finally:
                        error = sys.exc_info()[1]
                        row.update(
                            total_s=time.perf_counter() - started,
                            finished_at_unix_s=time.time(),
                            timing=timing,
                            error=None if error is None else f"{type(error).__name__}: {error}",
                        )
                        with lock:
                            stream.write(json.dumps(row, allow_nan=False) + "\n")
                            stream.flush()
            finally:
                model.http.close()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            pending = [pool.submit(lane, i) for i in range(concurrency)]
            for future in pending:
                future.result()
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == concurrency * count and all(row["error"] is None for row in rows)
    result = {
        "requests": len(rows),
        "concurrency": concurrency,
        "total_s": percentiles([r["total_s"] for r in rows]),
        "failures": [],
        "started_at_unix_s": min(r["started_at_unix_s"] for r in rows),
        "finished_at_unix_s": max(r["finished_at_unix_s"] for r in rows),
    }
    if model_type is Sam:
        result["server_stage_s"] = {
            key: percentiles([r["timing"]["server_timings"][key] for r in rows])
            for key in (
                "decode_s",
                "tiling_s",
                "forward_s",
                "postprocess_s",
                "dispatch_wait_s",
                "handler_total_s",
            )
        }
    else:
        result.update(
            prompt_processing_s=percentiles(
                [r["result"]["timings"]["prompt_ms"] / 1000 for r in rows]
            ),
            decoding_tokens_per_second=percentiles(
                [r["result"]["timings"]["predicted_per_second"] for r in rows]
            ),
            reasoning_tokens=percentiles([r["result"]["reasoning_tokens"] for r in rows]),
            schema_valid=len(rows),
        )
    save(directory / (name + ".json"), result)
    print(json.dumps({"case": name, **result}), flush=True)


def health(directory, model_type):
    model = model_type()
    rows = []
    try:
        for _ in range(50):
            started = time.perf_counter()
            response = model.http.get("/health")
            row = {"total_s": time.perf_counter() - started, "status": response.status_code}
            rows.append(row)
            response.raise_for_status()
            assert response.json()["status"] == "ok"
    finally:
        model.http.close()
        save(
            directory / (model_type.__name__.lower() + "-health.json"),
            {"rows": rows, "latency_s": percentiles([r["total_s"] for r in rows])},
        )


def sam(directory, variant, concurrency):
    protocol = json.loads((directory / "protocol.json").read_text())
    inputs = []
    for frame in protocol["frames"]:
        payload = (ROOT / frame["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == frame["sha256"]
        inputs.append(prepare_frame(payload))

    def request(model, number, timing, index):
        found = model.detect(
            inputs[(number + index * 10) % len(inputs)],
            protocol["prompts"],
            protocol["sam_threshold"],
            timing,
        )
        return {p: [asdict(d) for d in detections] for p, detections in found.items()}

    streams(directory, f"sam-{variant}-{concurrency}", concurrency, 40, Sam, request)


def glimmer(directory, concurrency):
    protocol = json.loads((directory / "protocol.json").read_text())
    rows = [json.loads(s) for s in (directory / "sam-baseline-1.jsonl").read_text().splitlines()]
    inputs, sources = [], []
    for row in rows:
        assert row["error"] is None
        for detection in row["result"]["person"]:
            path = ROOT / protocol["frames"][row["request"]]["path"]
            payload = path.read_bytes()
            record = json.loads(path.with_suffix(".json").read_text())
            close, close_box, _ = crop(payload, detection["box_xyxy"])
            context, context_box, _ = crop(payload, detection["box_xyxy"], 1536)
            scene = analyst.view(record, close_box, context_box)
            inputs.append((close, context, scene))
            sources.append(
                {
                    "frame": str(path.relative_to(ROOT)),
                    "crop_box": close_box,
                    "context_box": context_box,
                    "crop_sha256": hashlib.sha256(close).hexdigest(),
                    "context_sha256": hashlib.sha256(context).hexdigest(),
                }
            )
    assert len(inputs) >= concurrency, "Insufficient SAM crops for independent analyst streams"
    save(directory / f"glimmer-{concurrency}-inputs.json", sources)
    look_for = {
        "objective": "Find the missing person.",
        "target": "a person",
        "attributes": [],
        "ignore": [],
    }

    def request(model, number, timing, index):
        stream_inputs = inputs[index::concurrency]
        close, context, scene = stream_inputs[number % len(stream_inputs)]
        return analyst.analyze(model, look_for, "person", close, context, scene, timing)

    streams(directory, f"glimmer-{concurrency}", concurrency, 20, Glimmer, request)


def equivalence(directory):
    from scipy.optimize import linear_sum_assignment

    base = [json.loads(s) for s in (directory / "sam-baseline-1.jsonl").read_text().splitlines()]
    assert len(base) == 40
    candidates = []
    for concurrency in (1, 4):
        rows = [
            json.loads(s)
            for s in (directory / f"sam-compile-{concurrency}.jsonl").read_text().splitlines()
        ]
        assert len(rows) == concurrency * 40
        candidates.extend(rows)
    failures, differences = [], []
    for after in candidates:
        source = (after["request"] + after["stream"] * 10) % 40
        before = base[source]
        assert before["request"] == source and before["error"] is after["error"] is None
        assert before["result"].keys() == after["result"].keys()
        for prompt, old in before["result"].items():
            new = after["result"][prompt]
            if len(old) != len(new):
                failures.append(
                    {"frame": before["request"], "prompt": prompt, "counts": [len(old), len(new)]}
                )
                continue
            if not old:
                continue
            distances = np.max(
                np.abs(
                    np.array([d["box_xyxy"] for d in old])[:, None, :]
                    - np.array([d["box_xyxy"] for d in new])[None, :, :]
                ),
                axis=2,
            )
            left, right = linear_sum_assignment(distances)
            for i, j in zip(left, right):
                area = abs(old[i]["mask_area_px"] - new[j]["mask_area_px"])
                score = abs(old[i]["confidence"] - new[j]["confidence"])
                difference = {
                    "frame": before["request"],
                    "prompt": prompt,
                    "box_px": float(distances[i, j]),
                    "confidence": score,
                    "mask_area_px": area,
                }
                differences.append(difference)
                if (
                    distances[i, j] > TOLERANCE["box_px"]
                    or score > TOLERANCE["confidence"]
                    or area
                    > max(
                        TOLERANCE["mask_area_px"],
                        old[i]["mask_area_px"] * TOLERANCE["mask_area_fraction"],
                    )
                ):
                    failures.append(difference)
    save(
        directory / "equivalence.json",
        {
            "tolerance": TOLERANCE,
            "accepted": not failures,
            "failures": failures,
            "differences": differences,
        },
    )
    print(json.dumps({"compile_accepted": not failures, "failures": len(failures)}))


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "case", choices=("prepare", "health-sam", "health-glimmer", "sam", "glimmer", "equivalence")
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 4))
    parser.add_argument("--variant", choices=("baseline", "compile"))
    args = parser.parse_args()
    assert args.directory.is_dir()
    if args.case == "prepare":
        prepare(args.directory)
    elif args.case.startswith("health-"):
        health(args.directory, Sam if args.case == "health-sam" else Glimmer)
    elif args.case == "sam":
        assert args.concurrency in (1, 4) and args.variant is not None
        sam(args.directory, args.variant, args.concurrency)
    elif args.case == "glimmer":
        assert args.concurrency is not None
        glimmer(args.directory, args.concurrency)
    else:
        equivalence(args.directory)


if __name__ == "__main__":
    main()
