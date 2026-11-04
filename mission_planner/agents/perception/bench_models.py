"""Explicit benchmark cases; raw responses stay in ignored evidence files."""

import argparse
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.agents import review
from mission_planner.agents.models import Glimmer, Sam, Wonder
from mission_planner.environment import ROOT, load_env

CASES = (
    "jpeg_full",
    "jpeg_half",
    "resize_half",
    "crop_jpeg",
    "sam_tile_sync",
    "sam_frame_full_1",
    "sam_frame_full_3",
    "sam_frame_half_1",
    "sam_frame_half_3",
    "sam_load",
    "wonder_low",
    "wonder_medium",
    "wonder_tool_roundtrip",
    "glimmer_frame_2160",
)
EVIDENCE_SAMPLE = ".local/data/calibration/adult-kelvin-forest/frame-00-+00-Drone1.jpg"
LOOK_FOR = {
    "objective": "Find the missing person.",
    "target": "a person",
    "attributes": [],
    "ignore": [],
}


def jpeg(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90, subsampling=0)
    return buffer.getvalue()


def evidence():
    frame = (ROOT / EVIDENCE_SAMPLE).read_bytes()
    with Image.open(io.BytesIO(frame)) as native:
        assert native.size == (3840, 2160) and native.format == "JPEG"
    sam = Sam()
    try:
        detections = sam.detect(frame, ["person"], 0.5)["person"]
    finally:
        sam.http.close()
    assert detections, "The evidence sample must contain a person detection"
    best = max(detections, key=lambda d: d.confidence)
    return frame, {"track_id": 1, "box_xyxy": list(best.box_xyxy)}


def save(path, report):
    text = json.dumps(report, allow_nan=False) + "\n"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Use a new output file; accepted measurements are immutable."
    raw, samples = [], []

    def record(response):
        response.read()
        raw.append(response.json())

    def attach(model):
        model.http.event_hooks["response"].append(record)
        return model

    if args.case == "glimmer_frame_2160":
        frame, source = evidence()
        model = attach(Glimmer())
        text = review.instructions(
            LOOK_FOR, "person", {"drone_id": "Drone1", "captured_at_ms": 0}, source
        )

        def operation():
            timing = {}
            result = model.report(
                [frame], review.SYSTEM, text, review.schema(LOOK_FOR), review.MAX_TOKENS, timing
            )
            return {
                "frame_bytes": len(frame),
                "source": source,
                "request_bytes": timing["request_bytes"],
                "decision": result["report"]["decision"],
                "reasoning_tokens": result["reasoning_tokens"],
                "usage": result["usage"],
                "timings": result["timings"],
            }
    elif args.case.startswith("wonder"):
        model = attach(Wonder())
        map_image = (ROOT / "web/public/maps/valley-topographic.png").read_bytes()
        text = (
            "The rescue team searches a meadow with four drones. Each drone captures a downward "
            "image and reports its position, altitude, battery, and observed candidates. "
            "The commander must preserve uncertainty and request reviewer evidence before escalating. "
        ) * 112
        tool = dict(
            type="function",
            name="fleet_status",
            description="Get current drone count.",
            parameters=dict(type="object", properties={}, required=[], additionalProperties=False),
            strict=True,
        )

        def operation():
            if args.case != "wonder_tool_roundtrip":
                response = model.respond(
                    text + " Summarize next actions in two sentences.",
                    [map_image],
                    args.case.split("_")[1],
                    [],
                )
                return {"usage": response["usage"]}
            first = model.respond("Call fleet_status once.", [], "low", [tool])
            call = next(o for o in first["output"] if o["type"] == "function_call")
            assert call["name"] == "fleet_status" and json.loads(call["arguments"]) == {}
            output = dict(
                type="function_call_output", call_id=call["call_id"], output='{"drone_count":4}'
            )
            second = model.respond(
                "Report the drone count in one sentence.", [], "low", [tool], first["id"], (output,)
            )
            return {"first_usage": first["usage"], "second_usage": second["usage"]}
    else:
        image = Image.open(ROOT / ".local/evidence/models/target-Drone1.png").convert("RGB")
        assert image.size == (3840, 2160)
        half = image.resize((1920, 1080), Image.Resampling.LANCZOS)
        if args.case.startswith("sam"):
            model = attach(Sam())
            source = image if "full" in args.case else half
            source = image.crop((0, 0, 1008, 1008)) if args.case == "sam_tile_sync" else source
            payload = jpeg(source)
            prompts = ["person", "backpack", "jacket"] if args.case.endswith("_3") else ["person"]

            def operation():
                found = model.detect(payload, prompts, 0.5)
                return {"detections": {p: [asdict(d) for d in ds] for p, ds in found.items()}}
        else:

            def operation():
                if args.case == "resize_half":
                    return {"size": image.resize((1920, 1080), Image.Resampling.LANCZOS).size}
                source = image if args.case == "jpeg_full" else half
                if args.case == "crop_jpeg":
                    source = image.crop((2040, 1092, 2552, 1604))
                return {"bytes": len(jpeg(source))}

    def sample():
        started = time.perf_counter()
        result = operation()
        if raw and "usage" in raw[-1]:
            result["usage"] = raw[-1]["usage"]
        return {"seconds": time.perf_counter() - started, **result}

    try:
        operation()
        if args.case == "sam_load":
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = []
                for i in range(102):
                    time.sleep(max(0, start + i / 1.7 - time.perf_counter()))
                    futures.append(pool.submit(sample))
            for future in futures:
                error = future.exception()
                samples.append({"error": repr(error)} if error is not None else future.result())
        else:
            for _ in range(20):
                samples.append(sample())
    finally:
        seconds = [s["seconds"] for s in samples if "seconds" in s]
        result = dict(n=len(samples), sample_seconds=seconds, results=samples)
        if seconds:
            result.update(
                p50=float(np.percentile(seconds, 50)), p90=float(np.percentile(seconds, 90))
            )
        result["errors"] = sum("error" in s for s in samples)
        save(args.output, {args.case: result})
        raw_path = ROOT / ".local/evidence/models" / (args.output.stem + "-raw.json")
        raw_path.write_text(json.dumps(raw, indent=2) + "\n")


if __name__ == "__main__":
    main()
