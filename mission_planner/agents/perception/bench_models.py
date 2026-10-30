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

from mission_planner.agents.models import Glimmer, Sam, Wonder
from mission_planner.environment import ROOT

OPTIONS = ["yes", "no", "unclear"]
QUESTION = "Does this image show a person?"
BRIEF = (
    "Review the supplied drone crops for evidence of a missing person in open meadow. "
    "The aircraft searches at thirty metres above local terrain with a downward camera. "
    "Separate visible people from grass, shadows, rocks, and equipment. Do not infer identity "
    "from appearance alone. Use unclear when the evidence is too small or ambiguous. "
    "Consider all four crops together; they may show the same candidate. Report a verdict "
    "and confidence based only on visible evidence, followed by a one-to-two-sentence explanation. "
) * 3
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": OPTIONS},
        "explanation": {"type": "string"},
    },
    "required": ["verdict", "explanation"],
    "additionalProperties": False,
}
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
    "glimmer_fast_1",
    "glimmer_fast_4",
    "glimmer_slow",
    "wonder_low",
    "wonder_medium",
    "wonder_tool_roundtrip",
)


def jpeg(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90, subsampling=0)
    return buffer.getvalue()


def save(path, report):
    text = json.dumps(report, allow_nan=False) + "\n"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Use a new output file; accepted measurements are immutable."
    raw, samples, index = [], [], 0

    def record(response):
        response.read()
        raw.append(response.json())

    def attach(model):
        model.http.event_hooks["response"].append(record)
        return model

    if args.case.startswith("wonder"):
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
        elif args.case.startswith("glimmer"):
            model = attach(Glimmer())
            crops = [
                (ROOT / f".local/evidence/models/{name}-crop.jpg").read_bytes()
                for name in ("target", "grass")
            ]

            def operation():
                nonlocal index
                if args.case == "glimmer_slow":
                    return {"verdict": model.review(crops * 2, BRIEF, SCHEMA)}
                count = int(args.case[-1])
                offset = index * count + (32 if count == 4 else 0)
                index += 1
                images = [
                    jpeg(image.crop((2040 + offset + j, 1092, 2552 + offset + j, 1604)))
                    for j in range(count)
                ]
                return model.decide(images, QUESTION)
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
