"""Asset family specs and the paths shared by the Blender and analysis programs.

A family spec `<family>.json` maps each asset to its approved reference id, seed, bounding
dimensions in metres (x front, y left, z up) with tolerance, texture budget, collision flag,
turnaround panel cells, master-camera correspondences and master colour regions.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEXTURES = ROOT / ".local/vendor/polyhaven"
LOCK = Path(__file__).with_name("polyhaven.lock.json")
SIZE = 2880
# Camera heading (degrees from +Y toward +X) and pitch for each turnaround view.
VIEWS = {"front": (-90, 0), "back": (90, 0), "left": (180, 0), "right": (0, 0), "top": (180, -90)}
CELLS = ([0, 0], [1, 0], [0, 1], [1, 1])
KEYS = {
    "reference",
    "seed",
    "dimensionsM",
    "toleranceM",
    "textureSize",
    "collision",
    "panels",
    "correspondences",
    "colourRegions",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def output_dir(path):
    output = (ROOT / path).resolve()
    assert output.is_relative_to(ROOT / ".local") and not output.exists(), output
    output.mkdir(parents=True)
    return output


def check(family):
    assert family["schemaVersion"] == 1 and set(family) == {"schemaVersion", "assets"}
    assert family["assets"]
    for name, asset in family["assets"].items():
        assert set(asset) == KEYS, (name, set(asset) ^ KEYS)
        assert isinstance(asset["reference"], str) and isinstance(asset["seed"], int), name
        assert len(asset["dimensionsM"]) == 3 and all(0 < d < 100 for d in asset["dimensionsM"])
        assert 0 < asset["toleranceM"] <= 0.1, name
        assert asset["textureSize"] in (512, 1024, 2048, 4096, 8192), name
        assert isinstance(asset["collision"], bool), name
        panels = asset["panels"]
        assert set(panels) <= set(VIEWS) and all(cell in CELLS for cell in panels.values())
        assert len({tuple(cell) for cell in panels.values()}) == len(panels), name
        assert len(asset["correspondences"]) >= 4, name
        for point in asset["correspondences"]:
            assert set(point) == {"name", "modelM", "pixel"} and len(point["modelM"]) == 3
            assert len(point["pixel"]) == 2 and all(0 <= p < SIZE for p in point["pixel"])
        for boxes in asset["colourRegions"].values():
            assert boxes and all(
                0 <= x0 < x1 <= SIZE and 0 <= y0 < y1 <= SIZE for x0, y0, x1, y1 in boxes
            )
    return family["assets"]


def load(family):
    return check(json.loads(Path(__file__).with_name(f"{family}.json").read_text()))
