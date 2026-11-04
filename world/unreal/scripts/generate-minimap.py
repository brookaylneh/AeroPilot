"""North-up topographic map of the valley heightfield with its water, roads, trails and forest."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.environment import load_env
from world.cosys.terrain import (
    BOUNDS,
    COLUMNS,
    FIELD,
    ROOT,
    ROWS,
    TERRAIN_DIR,
    WORLD,
    terrain_height,
)

OUTPUT = ROOT / "web/public/maps/valley-topographic.png"
IMAGE_HEIGHT = 2048
CONTOUR_M = 5
# Terrain mask, colour and opacity, drawn in this order over the shaded relief.
LAYERS = (
    ("needle-floor", (64, 92, 56), 0.45),
    ("granite", (168, 166, 158), 0.6),
    ("cobble-bed", (78, 132, 184), 0.85),
    ("pond-silt", (78, 132, 184), 0.95),
    ("gravel-road", (176, 160, 136), 0.9),
    ("trail-tread", (227, 213, 162), 0.85),
)


def display(samples, size):
    """Heightfield layout (rows +Y, columns +X) to north-up display (rows -X, columns +Y)."""
    return np.asarray(Image.fromarray(np.flipud(samples.T)).resize(size))


def generate(evidence_dir):
    load_env()
    evidence = evidence_dir / "valley-topographic.json"
    assert not evidence.exists() and not OUTPUT.exists(), "Map outputs must be new"
    source = ROOT / FIELD["path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == WORLD["heightfieldSha256"]
    terrain = json.loads((TERRAIN_DIR / "terrain.json").read_text())
    assert terrain["heightfieldSha256"] == WORLD["heightfieldSha256"]
    height = np.fromfile(source, dtype="<u2").reshape(ROWS, COLUMNS).astype("float32")
    height = (height - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    north_m, east_m = [(BOUNDS["max"][a] - BOUNDS["min"][a]) / 100 for a in "xy"]
    size = (round(IMAGE_HEIGHT * east_m / north_m), IMAGE_HEIGHT)
    shown = display(height, size)
    dy, dx = np.gradient(shown, north_m / (size[1] - 1), east_m / (size[0] - 1))
    shade = np.clip((1 + 0.9 * dx - 0.8 * dy) / np.sqrt(1 + dx * dx + dy * dy), 0.6, 1.3)
    level = (shown - shown.min()) / (shown.max() - shown.min())
    rgb = np.array([114, 133, 82]) + level[..., None] * np.array([70, 50, 30])
    rgb = rgb * shade[..., None]
    band = np.floor(shown / CONTOUR_M)
    contour = np.zeros(band.shape, dtype=bool)
    contour[1:] |= band[1:] != band[:-1]
    contour[:, 1:] |= band[:, 1:] != band[:, :-1]
    rgb[contour] *= 0.72
    masks = {}
    for name, colour, opacity in LAYERS:
        path = TERRAIN_DIR / f"mask-{name}.png"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == terrain["masks"]["files"][name]["sha256"], path
        masks[name] = digest
        weight = display(np.asarray(Image.open(path)), size)[..., None] / 255 * opacity
        rgb = rgb * (1 - weight) + np.array(colour) * weight
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype("uint8")).save(OUTPUT, optimize=True)

    # Pixel (row, column) at fractions (v, u) of the map is north v from the top, east u from the
    # left. A mirrored or flipped image must disagree with the heightfield by far more.
    lo, hi = BOUNDS["min"], BOUNDS["max"]
    errors = {"northUp": [], "mirrored": [], "flipped": []}
    for v in np.linspace(0.02, 0.98, 13):
        for u in np.linspace(0.02, 0.98, 13):
            row, column = int(v * size[1]), int(u * size[0])
            expected = terrain_height(
                (hi["x"] - v * (hi["x"] - lo["x"])) / 100, (lo["y"] + u * (hi["y"] - lo["y"])) / 100
            )
            for key, (r, c) in (
                ("northUp", (row, column)),
                ("mirrored", (row, size[0] - 1 - column)),
                ("flipped", (size[1] - 1 - row, column)),
            ):
                errors[key].append(abs(float(shown[r, c]) - expected))
    proof = {key: {"samples": len(e), "maximumErrorM": max(e)} for key, e in errors.items()}
    assert proof["northUp"]["maximumErrorM"] < 1, proof
    assert min(proof["mirrored"]["maximumErrorM"], proof["flipped"]["maximumErrorM"]) > 10, proof
    report = {
        "source": "Valley heightfield and terrain masks",
        "environmentAssetVersion": WORLD["environmentAssetVersion"],
        "heightfieldSha256": WORLD["heightfieldSha256"],
        "masks": masks,
        "top": "Unreal +X (north)",
        "right": "Unreal +Y (east)",
        "boundsCm": BOUNDS,
        "widthMetres": east_m,
        "heightMetres": north_m,
        "metresPerPixel": north_m / size[1],
        "contourIntervalM": CONTOUR_M,
        "resolution": list(size),
        "orientationProof": proof,
        "output": str(OUTPUT.relative_to(ROOT)),
        "outputSha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "bytes": OUTPUT.stat().st_size,
        "containsPrivateTarget": False,
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    generate(parser.parse_args().evidence_dir)
