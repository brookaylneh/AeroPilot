"""Apply a recorded strip-layout repair to a reference image without paid generation."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", type=Path, required=True)
    args = parser.parse_args()
    recipe = json.loads(args.recipe.read_text())
    source_path = ROOT / recipe["sourceImage"]
    source_bytes = source_path.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == recipe["sourceSha256"]
    source = Image.open(source_path).convert("RGB")
    width, height = source.size
    left, top, right, bottom = recipe["targetBoundsPx"]
    assert 0 <= left < right <= width and 0 <= top < bottom <= height
    repair = Image.new("RGB", (right - left, bottom - top))
    cursor = 0
    strips = recipe["strips"]
    assert [strip["kind"] for strip in strips] == [
        "plank",
        "gap",
        "plank",
        "gap",
        "plank",
        "gap",
        "plank",
        "gap",
        "plank",
    ]
    for strip in strips:
        x0, y0, x1, y1 = strip["sourceBoundsPx"]
        assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
        target_height = strip["targetHeightPx"]
        assert target_height > 0
        pixels = source.crop((x0, y0, x1, y1)).resize(
            (right - left, target_height), Image.Resampling.LANCZOS
        )
        repair.paste(pixels, (0, cursor))
        cursor += target_height
    assert cursor == bottom - top
    mask = Image.new("L", source.size)
    polygon = recipe["targetPolygonPx"]
    assert len(polygon) >= 4
    assert all(left <= x < right and top <= y < bottom for x, y in polygon)
    ImageDraw.Draw(mask).polygon([tuple(point) for point in polygon], fill=255)
    candidate = source.copy()
    candidate.paste(repair, (left, top))
    output = Image.composite(candidate, source, mask)
    original_pixels = np.asarray(source)
    output_pixels = np.asarray(output)
    edited = np.asarray(mask) != 0
    assert np.array_equal(output_pixels[~edited], original_pixels[~edited])
    output_path = ROOT / recipe["outputImage"]
    assert output_path.resolve().is_relative_to((ROOT / ".local/world/valley").resolve())
    evidence_path = ROOT / recipe["receipt"]
    assert evidence_path.resolve().is_relative_to((ROOT / ".local/evidence/world").resolve())
    with output_path.open("xb") as stream:
        output.save(stream, format="PNG")
    receipt = {
        "method": "Authored five-plank reference layout from existing wood pixels; not AI output or measured geometry",
        "recipe": recipe,
        "recipeSha256": hashlib.sha256(args.recipe.read_bytes()).hexdigest(),
        "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "resolution": [width, height],
        "outsideMaskPixelsChanged": int(
            np.count_nonzero(np.any(output_pixels[~edited] != original_pixels[~edited], axis=1))
        ),
        "insideMaskPixelsChanged": int(
            np.count_nonzero(np.any(output_pixels[edited] != original_pixels[edited], axis=1))
        ),
        "outputSha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
    with evidence_path.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
