"""Match body albedo to the supplied head using the same neck UV region."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.environment import load_env


def mean_color(path, region):
    with Image.open(path) as image:
        width, height = image.size
        bounds = [round(value * size) for value, size in zip(region, (width, height) * 2)]
        pixels = np.asarray(image.convert("RGB").crop(bounds), dtype=np.float64) / 255
    linear = np.where(pixels <= 0.04045, pixels / 12.92, ((pixels + 0.055) / 1.055) ** 2.4)
    return linear.mean(axis=(0, 1))


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("reference_head", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    recipe = json.loads(args.recipe.read_text())
    source = recipe["head"]["textures"]["Basecolor"]
    target = Path(source["path"])
    assert hashlib.sha256(target.read_bytes()).hexdigest() == source["sha256"]
    region = recipe["head"]["neck_color_region"]
    assert len(region) == 4 and all(0 <= n <= 1 for n in region)
    assert region[0] < region[2] and region[1] < region[3]
    reference = mean_color(args.reference_head, region)
    supplied = mean_color(target, region)
    assert np.all(reference > 0)
    multiplier = supplied / reference
    assert np.all(np.isfinite(multiplier)) and np.all((multiplier > 0.1) & (multiplier < 4))
    path = recipe["assembly_folder"] + "/" + recipe["asset_name"] + "/Body/Materials/MI_Body_Baked"
    assert path not in recipe["material_parameters"]
    recipe["material_parameters"][path] = {
        "Basecolor Global Multiply Post-Bake": [*multiplier.tolist(), 1]
    }
    report = {
        "neck_uv_region": region,
        "reference_linear_rgb": reference.tolist(),
        "supplied_linear_rgb": supplied.tolist(),
        "body_linear_rgb_multiplier": multiplier.tolist(),
        "reference_sha256": hashlib.sha256(args.reference_head.read_bytes()).hexdigest(),
        "supplied_sha256": source["sha256"],
        "scope": "Albedo color match; rendered seam and shading require visual review",
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    args.recipe.write_text(json.dumps(recipe, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
