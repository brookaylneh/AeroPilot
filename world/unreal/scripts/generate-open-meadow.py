"""Extend the original deterministic meadow; verify its original square before writing."""

import hashlib
import json

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, zoom

from mission_planner.environment import load_env
from world.cosys.terrain import BOUNDS, COLUMNS, FIELD, ROOT, ROWS, WORLD

# The original release is the immutable reference for calibration geometry.
ORIGINAL_WIDTH_M = 1609.344
ORIGINAL_SAMPLES = 2017
ORIGINAL_SHA256 = "a523142668a99568c9bdd22ef0b153a06b8791c085ed41caa6f0e6b16750a4c1"
SEED = 74021


def height(x, y):
    x, y = x * 2, y * 2
    return (
        8 + 3 * np.sin(x / 470) * np.cos(y / 590)
        + 1.3 * np.sin(y / 95 + x / 180)
        + 24 * np.exp(-((x + 1100) / 500)**2 - ((y - 700) / 650)**2)
        + 36 * np.exp(-((x - 1200) / 650)**2 - ((y - 600) / 450)**2)
        + 26 * np.exp(-((x - 600) / 550)**2 - ((y + 1400) / 600)**2)
        + .06 * np.sin(x / 6) * np.sin(y / 7)
    )


def generate():
    load_env()
    out = (ROOT / FIELD["path"]).parent
    assert not out.exists(), f"Terrain destination already exists: {out}"
    evidence = ROOT / ".local/evidence/world/terrain.json"
    assert not evidence.exists(), f"Evidence already exists: {evidence}"
    bounds = {end: [BOUNDS[end][axis] for axis in "xyz"] for end in ("min", "max")}
    north_m, east_m = [(BOUNDS["max"][a] - BOUNDS["min"][a]) / 100 for a in "xy"]
    north = np.linspace(BOUNDS["min"]["x"] / 100, BOUNDS["max"]["x"] / 100, COLUMNS)
    east = np.linspace(BOUNDS["min"]["y"] / 100, BOUNDS["max"]["y"] / 100, ROWS)
    x, y = np.meshgrid(north, east)
    heights = height(x, y)
    samples = np.rint(FIELD["zeroSample"] + heights * FIELD["samplesPerMetre"])
    assert np.isfinite(samples).all() and samples.min() >= 0 and samples.max() <= 65535
    raw = samples.astype("<u2")
    old_path = ROOT / ".local/vendor/open-meadow/height.r16"
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == ORIGINAL_SHA256
    original = np.fromfile(old_path, dtype="<u2").reshape(ORIGINAL_SAMPLES, ORIGINAL_SAMPLES)
    first = [
        round((-ORIGINAL_WIDTH_M / 2 - BOUNDS["min"][a] / 100) / FIELD["sampleSpacingMetres"])
        for a in "xy"
    ]
    assert all(v >= 0 for v in first)
    central = raw[first[1]:first[1] + ORIGINAL_SAMPLES, first[0]:first[0] + ORIGINAL_SAMPLES]
    assert central.shape == original.shape
    different = int(np.count_nonzero(central != original))
    assert different == 0, f"Original square changed at {different} samples"
    dy, dx = np.gradient(
        (raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"],
        FIELD["sampleSpacingMetres"],
    )
    slope = np.degrees(np.arctan(np.hypot(dx, dy)))
    assert float(slope.max()) < 12, "Terrain must remain gently rolling"
    assert float(heights.max() - heights.min()) < 65, "No mountain-sized relief"
    digest = hashlib.sha256(raw.tobytes()).hexdigest()
    assert digest == WORLD["heightfieldSha256"], "Version and record the new terrain hash first"

    cols = 2048
    rows = round(cols * east_m / north_m)
    mx, my = np.meshgrid(
        np.linspace(BOUNDS["min"]["x"] / 100, BOUNDS["max"]["x"] / 100, cols),
        np.linspace(BOUNDS["min"]["y"] / 100, BOUNDS["max"]["y"] / 100, rows),
    )
    rng = np.random.default_rng(SEED)
    macro = np.zeros((rows, cols))
    for cells, amplitude in [(16, 1), (64, .35), (256, .12)]:
        shape = (round(cells * east_m / north_m), cells)
        field = gaussian_filter(rng.random(shape), .9, mode="reflect")
        field = (field - field.min()) / (field.max() - field.min())
        macro += zoom(field, (rows / shape[0], cols / shape[1]), order=1) * amplitude
    macro = (macro - macro.min()) / (macro.max() - macro.min())
    trail_y = 60 * np.sin(mx / 275) + 25 * np.cos(mx / 110)
    trail = np.clip((4.2 - abs(my - trail_y)) / 2, 0, 1)
    cover = (1 - trail) * np.clip(.35 + macro * .75, 0, 1)
    pixels = np.dstack([trail, macro, cover, np.ones_like(macro)])
    assert pixels.shape == (rows, cols, 4) and np.isfinite(pixels).all()
    report = {
        "environmentId": WORLD["worldId"], "assetVersion": WORLD["environmentAssetVersion"],
        "name": "Open Meadow", "world": "/Game/OpenMeadow/OpenMeadow", "seed": SEED,
        "widthMetres": east_m, "heightMetres": north_m,
        "areaSquareKm": east_m * north_m / 1e6,
        "heightmapResolution": {"rows": ROWS, "columns": COLUMNS},
        "sampleSpacingMetres": FIELD["sampleSpacingMetres"],
        "minElevationMetres": float(heights.min()), "maxElevationMetres": float(heights.max()),
        "reliefMetres": float(heights.max() - heights.min()),
        "maxSlopeDegrees": float(slope.max()),
        "coordinateSystem": WORLD["coordinateSystem"], "boundsCm": bounds,
        "source": "Original deterministic terrain extended east and west",
        "heightmapSha256": digest,
        "originalSquare": {"sha256": ORIGINAL_SHA256, "samples": int(original.size),
                           "differentSamples": different, "firstRow": first[1], "firstColumn": first[0]},
        "maskResolution": {"rows": rows, "columns": cols},
        "authored": False,
    }
    out.mkdir(parents=True)
    raw.tofile(out / "height.r16")
    Image.fromarray(np.rint(pixels * 255).astype("uint8")).save(out / "ground-mask.png")
    (out / "terrain.json").write_text(json.dumps(report, indent=2) + "\n")
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    generate()
