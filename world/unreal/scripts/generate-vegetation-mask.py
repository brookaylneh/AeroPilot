"""Deterministic meadow plant patches at constant physical spacing."""

import hashlib
import json

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, zoom

from mission_planner.environment import load_env
from world.cosys.terrain import FIELD, ROOT, WORLD


def generate():
    load_env()
    source = (ROOT / FIELD["path"]).parent
    output = source / "vegetation-mask.png"
    evidence = ROOT / ".local/evidence/world/vegetation-mask.json"
    assert not output.exists() and not evidence.exists()
    ground = np.asarray(Image.open(source / "ground-mask.png")).astype(float) / 255
    rows, cols, channels = ground.shape
    assert channels == 4
    rng = np.random.default_rng(74028)
    channels = []
    for cells, threshold in [(48, .49), (80, .53), (32, .51)]:
        shape = (round(cells * rows / cols), cells)
        noise = gaussian_filter(rng.random(shape), .65, mode="reflect")
        noise = zoom(noise, (rows / shape[0], cols / shape[1]), order=1)
        patch = np.clip((noise - threshold) * 6, 0, 1)
        channels.append(patch * ground[:, :, 2])
    channels.append(ground[:, :, 2])
    pixels = np.rint(np.dstack(channels) * 255).astype("uint8")
    assert pixels.shape == (rows, cols, 4)
    Image.fromarray(pixels).save(output)
    report = {
        "seed": 74028, "rows": rows, "columns": cols,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "channels": {"r": "Meadow Flowers patches A", "g": "Meadow Flowers patches B",
                     "b": "Taller grass patches", "a": "Base coverage excluding footpath"},
        "heightmapSha256": WORLD["heightfieldSha256"],
    }
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    generate()
