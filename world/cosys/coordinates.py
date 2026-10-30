"""Compare live Cosys poses with independent native Unreal instrumentation."""

import fcntl
import json
import math
import os
from pathlib import Path

from world.cosys.capture import LIMITS, nadir_error
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
    assert r["lumenGlobalIllumination"] and r["lumenReflections"]
    ev100 = json.loads((ROOT / "world/unreal/valley-lighting.json").read_text())["exposureEv100"]
    field = WORLD["heightfield"]
    assert (field["rowAxis"], field["columnAxis"]) == ("+Y", "+X")
    for axis, count in (("x", field["columns"]), ("y", field["rows"])):
        assert (
            abs(
                (WORLD["searchBoundsCm"]["max"][axis] - WORLD["searchBoundsCm"]["min"][axis]) / 100
                - (count - 1) * field["sampleSpacingMetres"]
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
        spawn = {k: WORLD["vehicles"][name]["spawnUeCm"][k] / 100 for k in "xyz"}
        assert distance(s["positionWorldM"], spawn) < 0.5
        assert math.sqrt(sum(v * v for v in s["velocityLocalNedMps"].values())) < 0.05, (
            "Coordinate audit requires aircraft at rest at their pads"
        )
        info = c.simGetCameraInfo("front_center", vehicle_name=name)
        physical = next(v for v in native()["vehicles"] if v["id"] == name)
        actual = to_world(name, xyz(info.pose.position))
        clearance = actual["z"] - WORLD["port"]["centerUeCm"]["z"] / 100
        assert clearance > 0.1, "Camera must remain above concrete at the pad"
        delta = distance(actual, {k: v / 100 for k, v in physical["cameraPositionUeCm"].items()})
        assert delta < 0.03, (name, delta)
        angle = nadir_error(quat(info.pose.orientation))
        assert angle <= LIMITS["nadirDegrees"], (name, angle)
        assert abs(info.fov - 70) < 0.01
        assert physical["lighting"] == "lumen" and physical["exposureCompensation"] == -ev100
        assert (physical["whiteBalanceKelvin"], physical["whiteBalanceTint"]) == (6500, 0)
        item = {
            "localNedOriginUeCm": origin(name),
            "nativeCameraPositionErrorM": delta,
            "nadirErrorDegrees": angle,
            "fovDegrees": info.fov,
            "cameraClearanceAboveConcreteM": clearance,
            "lighting": physical["lighting"],
            "exposureCompensation": physical["exposureCompensation"],
            "whiteBalanceKelvin": physical["whiteBalanceKelvin"],
        }
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
