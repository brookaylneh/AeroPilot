"""Compare editor collision traces against public heightfield samples."""

import json
import os
import sys
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env
from world.cosys.terrain import WORLD, terrain_height

load_env()
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "landscape-orientation.json"
assert not report_path.exists(), "Archive previous orientation evidence before a new run"
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
lo, hi = WORLD["searchBoundsCm"]["min"], WORLD["searchBoundsCm"]["max"]
points = []
for east_fraction in (0, 0.17, 0.47, 0.73, 1):
    for north_fraction in (0, 0.11, 0.33, 0.61, 0.89, 1):
        x = lo["x"] + north_fraction * (hi["x"] - lo["x"])
        y = lo["y"] + east_fraction * (hi["y"] - lo["y"])
        hit = unreal.ScenarioLandscapeTools.trace_landscape(
            unreal.Vector(x, y, hi["z"]), unreal.Vector(x, y, lo["z"])
        )
        expected_cm = terrain_height(x / 100, y / 100) * 100
        points.append(
            {
                "northCm": x,
                "eastCm": y,
                "heightfieldCm": expected_cm,
                "landscapeTraceCm": hit.z,
                "differenceCm": abs(hit.z - expected_cm),
            }
        )
assert len(points) >= 24
maximum = max(p["differenceCm"] for p in points)
report = {
    "environmentAssetVersion": WORLD["environmentAssetVersion"],
    "heightfieldSha256": WORLD["heightfieldSha256"],
    "coordinateSystem": WORLD["coordinateSystem"],
    "sampleCount": len(points),
    "maximumDifferenceCm": maximum,
    "points": points,
}
report_path.write_text(json.dumps(report, indent=2) + "\n")
assert maximum < 1, f"Landscape collision and heightfield disagree by {maximum} cm"
unreal.log("SNR_LANDSCAPE_ORIENTATION " + json.dumps(report))
