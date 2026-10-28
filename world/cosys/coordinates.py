"""Compare live Cosys poses with independent native Unreal instrumentation."""

import fcntl
import json
import math
import os
import time
from pathlib import Path

import cosysairsim as airsim

from world.cosys.client import (
    DATA,
    NAMES,
    ROOT,
    WORLD,
    connect,
    distance,
    entry_axis,
    native,
    origin,
    quat,
    set_view,
    state,
    to_world,
    xyz,
)


def run():
    c = connect()
    r = native()
    report = {"schemaVersion": 1, "passed": False, "vehicles": {}}
    assert r["worldToMeters"] == 100
    assert r["worldOriginOffsetCm"] == dict(x=0, y=0, z=0)
    assert distance(r["globalNedOriginUeCm"], WORLD["playerStartUeCm"]) < 0.01
    for axis in "xy":
        assert (
            abs(
                (WORLD["searchBoundsCm"]["max"][axis] - WORLD["searchBoundsCm"]["min"][axis]) / 100
                - 1609.344
            )
            < 1e-6
        )
    port = WORLD["port"]
    axis = entry_axis()
    gap = WORLD["searchBoundsCm"]["min"][axis] / 100 - (
        port["centerUeCm"][axis] / 100 + port["dimensionsMetres"][0 if axis == "x" else 1] / 2
    )
    assert abs(gap - 5) < 1e-6
    report.update(
        globalNedOriginUeCm=r["globalNedOriginUeCm"],
        portBoundary=port["boundary"],
        portGapMetres=gap,
        infrastructureVersion=WORLD["infrastructureVersion"],
    )
    for name in NAMES:
        s = state(c, name)
        assert s["landed"], "Coordinate audit runs with parked aircraft"
        item = {"localNedOriginUeCm": origin(name), "views": []}
        for pitch in [-90, -45]:
            set_view(c, name, pitch)
            time.sleep(0.65)
            info = c.simGetCameraInfo("front_center", vehicle_name=name)
            physical = next(v for v in native()["vehicles"] if v["id"] == name)
            actual = to_world(name, xyz(info.pose.position))
            assert actual["z"] > WORLD["port"]["centerUeCm"]["z"] / 100 + 0.1, (
                "Camera must remain above concrete when landed"
            )
            delta = distance(
                actual, {k: v / 100 for k, v in physical["cameraPositionUeCm"].items()}
            )
            assert delta < 0.03, (name, delta)
            body = airsim.Quaternionr(
                s["orientationNed"]["x"],
                s["orientationNed"]["y"],
                s["orientationNed"]["z"],
                s["orientationNed"]["w"],
            )
            expected = body * airsim.euler_to_quaternion(0, math.radians(pitch), 0)
            a = quat(expected)
            b = quat(info.pose.orientation)
            angle = math.degrees(2 * math.acos(min(1, abs(sum(a[k] * b[k] for k in "wxyz")))))
            assert angle < 0.2, (name, angle)
            assert abs(info.fov - 70) < 0.01
            item["views"].append(
                {
                    "pitchDegrees": pitch,
                    "nativeCameraPositionErrorM": delta,
                    "orientationErrorDegrees": angle,
                    "fovDegrees": info.fov,
                    "landedCameraClearanceAboveConcreteM": actual["z"]
                    - WORLD["port"]["centerUeCm"]["z"] / 100,
                }
            )
        report["vehicles"][name] = item
    report["passed"] = True
    Path(
        os.environ.get(
            "SNR_COORDINATE_REPORT", str(ROOT / ".local/evidence/simulator-coordinates.json")
        )
    ).write_text(json.dumps(report, indent=2) + "\n")
    print("Native Unreal and Cosys coordinate/camera agreement verified for all four aircraft.")
    return report


if __name__ == "__main__":
    with open(DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
