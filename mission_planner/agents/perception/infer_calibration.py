"""Infer saved calibration frames once; persist raw responses before validating them."""

import argparse
import hashlib
import io
import json
import math
import random
import signal
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.agents.models import Glimmer, Sam
from mission_planner.agents.perception.calibrate import ALONG, CONDITIONS, OFFSETS
from mission_planner.agents.perception.geometry import Camera
from mission_planner.agents.perception.glimmer_calibration import annotate
from mission_planner.environment import ROOT


def jpeg(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95, subsampling=0)
    return buffer.getvalue()


def label_frame(frame, preset, situation):
    camera = Camera(frame)
    point = frame["observation"]["groundWorldM"]
    pixel = camera.project(point)
    height = preset["heightCm"] / 100
    radius = height / 1.7 if situation == "injured" else preset["capsuleRadiusCm"] / 100
    corners = np.array(
        [
            camera.project(dict(x=point["x"] + x, y=point["y"] + y, z=point["z"] + z))
            for x in (-radius, radius)
            for y in (-radius, radius)
            for z in (0, height)
        ]
    )
    extent = np.ptp(corners, axis=0) / 2
    size = np.array([camera.width, camera.height])
    contains = bool(((pixel >= extent) & (pixel <= size - extent)).all())
    empty = bool(((corners.max(axis=0) < 0) | (corners.min(axis=0) > size)).any())
    # Project the specified 0.75 m horizontal margin in both world axes.
    margin = max(
        np.linalg.norm(camera.project({**point, axis: point[axis] + sign * 0.75}) - pixel)
        for axis in "xy"
        for sign in (-1, 1)
    )
    return (
        camera,
        point,
        pixel,
        margin,
        dict(
            contains=contains,
            empty=empty,
            personPixel=pixel.tolist(),
            personBoxPx=np.concatenate((corners.min(axis=0), corners.max(axis=0))).tolist(),
            halfExtentPx=extent.tolist(),
        ),
    )


def run(directory, sam, glimmer):
    output = directory / "inference.json"
    assert not output.exists(), "Inference outputs are immutable; archive explicitly"
    condition = json.loads((directory / "condition.json").read_text())
    assert json.loads((directory / "complete.json").read_text())["frames"] == 49
    paths = sorted(directory.glob("frame-*.json"))
    assert len(paths) == 49
    frames, crops = [], []
    for path in paths:
        frame = json.loads(path.read_text())
        image_path = directory / frame["file"]
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == frame["sha256"]
        camera, point, pixel, margin, labels = label_frame(
            frame, condition["preset"], condition["condition"][3]
        )
        with Image.open(image_path) as image:
            scale = image.width / 1920
            found = sam.detect(
                jpeg(image.resize((1920, 1080), Image.Resampling.LANCZOS)), ["person"], 0.05
            )["person"]
        detections = []
        for detection in found:
            box = np.array(detection.box_xyxy) * scale

            def inside(m):
                return bool(((pixel >= box[:2] - m) & (pixel <= box[2:] + m)).all())

            center = (box[:2] + box[2:]) / 2
            ground = camera.ground(center)
            detections.append(
                dict(
                    **asdict(detection),
                    true=inside(margin),
                    undilated=inside(0),
                    errorM=math.hypot(ground["x"] - point["x"], ground["y"] - point["y"]),
                )
            )
            crops.append(
                dict(
                    file=frame["file"],
                    center=center.tolist(),
                    kind="detection",
                    offsetM=frame["offsetM"],
                )
            )
        frames.append(
            dict(
                file=frame["file"],
                offsetM=frame["offsetM"],
                alongM=frame["alongM"],
                actualOffsetM=frame["actualOffsetM"],
                actualAlongM=frame["actualAlongM"],
                roundTripErrorPx=frame["roundTripErrorPx"],
                **labels,
                detections=detections,
            )
        )
    assert {(f["offsetM"], f["alongM"]) for f in frames} == {(x, y) for x in OFFSETS for y in ALONG}
    empty = [f for f in frames if f["empty"]]
    assert empty, "No fully empty frames for grass controls"
    rng = random.Random(74021)
    height = condition["condition"][5]
    width, half = height * 16 // 9, 256 * height / 2160
    for _ in range(len(crops)):
        frame = rng.choice(empty)
        crops.append(
            dict(
                file=frame["file"],
                center=[rng.uniform(half, width - half), rng.uniform(half, height - half)],
                kind="grass",
                offsetM=frame["offsetM"],
            )
        )
    for crop in crops:
        x, y = crop["center"]
        box = tuple(round(v) for v in (x - half, y - half, x + half, y + half))
        with Image.open(directory / crop["file"]) as image:
            payload = jpeg(image.crop(box).resize((512, 512), Image.Resampling.LANCZOS))
        crop.update(box=box, **glimmer.decide([payload], "Does this image show a person?"))
    result = dict(
        schemaVersion=2,
        condition=condition["condition"],
        frames=frames,
        crops=crops,
        samThreshold=0.05,
        optionTokenIds=glimmer.option_token_ids,
        extentMethod="Projected catalog height/capsule envelope; seated support radius from ScenarioManager",
    )
    output.write_text(json.dumps(annotate(result), indent=2, allow_nan=False) + "\n")


def main():
    def terminate(signum, frame):
        raise SystemExit(f"Signal {signum} in {frame.f_code.co_name}; inference interrupted")

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--condition", choices=[c[0] for c in CONDITIONS] + ["all"], nargs="+", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = [c[0] for c in CONDITIONS] if args.condition == ["all"] else args.condition
    assert "all" not in names and len(names) == len(set(names))
    selected = [ROOT / ".local/data/calibration" / name for name in names]
    assert all(
        (p / "complete.json").exists() and not (p / "inference.json").exists() for p in selected
    )
    args.output.mkdir(parents=True, exist_ok=False)
    sam, glimmer = Sam(), None
    try:

        def record(response):
            response.read()
            with (args.output / "responses.jsonl").open("a") as handle:
                handle.write(
                    json.dumps(
                        dict(
                            status=response.status_code,
                            seconds=response.elapsed.total_seconds(),
                            body=response.json(),
                        )
                    )
                    + "\n"
                )

        glimmer = Glimmer()
        for model in (sam, glimmer):
            model.http.event_hooks["response"].append(record)
        for directory in selected:
            run(directory, sam, glimmer)
            print(directory.name, "inference complete", flush=True)

    finally:
        sam.http.close()
        if glimmer is not None:
            glimmer.http.close()


if __name__ == "__main__":
    main()
