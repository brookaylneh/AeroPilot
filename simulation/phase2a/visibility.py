"""Controlled, PRIVATE evaluator. This is not a perception or mission controller."""

import fcntl
import json
import math
import time

import cosysairsim as airsim
from client import (
    CAMERA_DOWN,
    CAMERA_FORWARD,
    DATA,
    ROOT,
    WORLD,
    capture,
    configure_capture,
    connect,
    home,
    land,
    move,
    set_view,
    state,
    takeoff,
)


def run(manage_flight=True):
    private = DATA / "evaluation"
    condition = json.loads((private / "condition.json").read_text())
    target = {k: v / 100 for k, v in condition["privateGroundTruthUeCm"].items()}
    out = ROOT / "evidence/phase2a/visibility"
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "schemaVersion": 1,
        "method": "Controlled ground-truth placement and visual review. No AI detections.",
        "frames": [],
        "emptyFrames": [],
        "resolutionComparison": [],
        "passed": False,
    }
    c = connect()
    name = "Drone1"
    configure_capture(2160)
    heading = math.radians(WORLD["vehicles"][name]["yawDegrees"])
    forward = {"x": math.cos(heading), "y": math.sin(heading)}
    if state(c, name)["landed"]:
        takeoff(name)
    try:
        # Transit over local terrain at a conservative common elevation, using
        # regular flight APIs. No teleporting aircraft to observation locations.
        move(name, {**home(name), "z": 65}, 8)
        move(name, {"x": target["x"] - forward["x"], "y": target["y"] - forward["y"], "z": 65}, 8)
        for altitude in [10, 20, 30, 40]:
            for view, pitch in [("down", -90), ("oblique", -45)]:
                camera_height = altitude - CAMERA_DOWN
                # Aim near the upper torso using the configured aircraft heading.
                setback = CAMERA_FORWARD if view == "down" else camera_height - 1 + CAMERA_FORWARD
                point = {
                    "x": target["x"] - setback * forward["x"],
                    "y": target["y"] - setback * forward["y"],
                    "z": target["z"] + altitude,
                }
                move(name, point, 4)
                set_view(c, name, pitch)
                time.sleep(1.5)
                for i in range(2):
                    capture(name, out / "warmup", f"{altitude}-{view}-{i}")
                frame = capture(name, out, f"{altitude}m-{view}", freeze=True)
                assert (frame["width"], frame["height"]) == (3840, 2160)
                frame.update(
                    testHeightMetres=altitude,
                    view=view,
                    actualCameraHeightAboveTargetGroundM=frame["cameraPositionWorldM"]["z"]
                    - target["z"],
                )
                # Native object bounds are simulator evaluation ground truth,
                # never labelled as a model detection or exposed to mission inputs.
                c.simClearDetectionMeshNames(
                    "front_center", airsim.ImageType.Scene, vehicle_name=name
                )
                c.simAddDetectionFilterMeshName(
                    "front_center",
                    airsim.ImageType.Scene,
                    "BP_Person_adult_kelvin*",
                    vehicle_name=name,
                )
                c.simSetDetectionFilterRadius(
                    "front_center", airsim.ImageType.Scene, 20000, vehicle_name=name
                )
                detections = c.simGetDetections(
                    "front_center", airsim.ImageType.Scene, vehicle_name=name
                )
                boxes = [
                    {
                        "name": d.name,
                        "min": {"x": d.box2D.min.x_val, "y": d.box2D.min.y_val},
                        "max": {"x": d.box2D.max.x_val, "y": d.box2D.max.y_val},
                    }
                    for d in detections
                ]
                frame["evaluationOnlyProjectedActorBoxes"] = boxes
                result["frames"].append(frame)
                if altitude == 20 and view == "oblique":
                    result["resolutionComparison"].append(frame)
                    for height in [1080, 4320]:
                        configure_capture(height)
                        for warm in range(2):
                            capture(name, out / "warmup", f"20m-oblique-{height}p-{warm}")
                        comparison = capture(name, out, f"20m-oblique-{height}p", freeze=True)
                        assert (comparison["width"], comparison["height"]) == (
                            height * 16 // 9,
                            height,
                        )
                        result["resolutionComparison"].append(comparison)
                    configure_capture(2160)
                (private / "visibility-evaluation.json").write_text(json.dumps(result, indent=2))
                print(
                    altitude,
                    view,
                    frame["width"],
                    frame["height"],
                    "projected actors",
                    len(boxes),
                    flush=True,
                )
        # Empty control is independently chosen well outside the target FOV.
        move(name, {"x": target["x"] + 160, "y": target["y"], "z": target["z"] + 40}, 6)
        for view, pitch in [("down", -90), ("oblique", -45)]:
            set_view(c, name, pitch)
            capture(name, out / "warmup", f"empty-{view}-warmup")
            result["emptyFrames"].append(capture(name, out, f"empty-{view}", freeze=True))
        result["passed"] = True
    finally:
        c.simClearDetectionMeshNames("front_center", airsim.ImageType.Scene, vehicle_name=name)
        if manage_flight:
            move(name, {**state(c, name)["positionWorldM"], "z": 65}, 6)
            move(name, {**home(name), "z": 65}, 8)
            land(name)
        (private / "visibility-evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    with open(DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
