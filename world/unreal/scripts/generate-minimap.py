"""North-up map from the exact rectangular heightfield and trail mask."""

import hashlib
import json

import numpy as np
from PIL import Image

from mission_planner.environment import load_env
from world.cosys.terrain import BOUNDS, COLUMNS, FIELD, ROOT, ROWS, WORLD


def generate():
    load_env()
    source = ROOT / FIELD["path"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == WORLD["heightfieldSha256"]
    height = np.fromfile(source, dtype="<u2").reshape(ROWS, COLUMNS).astype(float)
    height = (height - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    north_m, east_m = [(BOUNDS["max"][a] - BOUNDS["min"][a]) / 100 for a in "xy"]
    image_height = 2048
    image_width = round(image_height * east_m / north_m)
    # Input rows are +Y, columns +X; display rows are -X, columns +Y.
    height = np.flipud(height.T)
    height = np.asarray(Image.fromarray(height.astype("float32")).resize((image_width, image_height)))
    dy, dx = np.gradient(height, north_m / (image_height - 1), east_m / (image_width - 1))
    shade = np.clip((1 + .9 * dx - .8 * dy) / np.sqrt(1 + dx * dx + dy * dy), .6, 1.3)
    base = np.array([114, 133, 82]) + (height[..., None] / 46) * np.array([32, 23, 14])
    rgb = base * shade[..., None]
    contour = np.abs((height + .18) % 5 - .18) < .12
    rgb[contour] *= .79
    trail = np.asarray(Image.open(source.parent / "ground-mask.png"))[:, :, 0]
    trail = np.asarray(Image.fromarray(np.flipud(trail.T)).resize((image_width, image_height))).astype(float) / 255
    rgb = rgb * (1 - trail[..., None] * .85) + np.array([227, 213, 162]) * trail[..., None] * .85
    out = ROOT / "web/public/maps/open-meadow-topographic.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype("uint8")).save(out, optimize=True)
    report = {
        "source": "Actual Open Meadow heightfield and trail mask",
        "heightfieldSha256": WORLD["heightfieldSha256"],
        "north": "Unreal +X", "east": "Unreal +Y", "widthMetres": east_m, "heightMetres": north_m,
        "resolution": [image_width, image_height], "output": str(out.relative_to(ROOT)),
        "outputSha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size, "containsPrivateTarget": False,
    }
    evidence = ROOT / ".local/evidence/world/navigation-minimap.json"
    assert not evidence.exists()
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    generate()
