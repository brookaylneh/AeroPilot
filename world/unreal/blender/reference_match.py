"""Match an authored asset to its approved reference image.

`fit` solves the reference camera from model-to-pixel correspondences. `compare` composites a
studio render over the reference background and reports silhouette overlap and region colours.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.optimize import least_squares

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]
SENSOR_MM = 36.0


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(spec):
    path = ROOT / spec["reference"]["path"]
    assert digest(path) == spec["reference"]["sha256"], path
    image = Image.open(path).convert("RGB")
    assert list(image.size) == spec["reference"]["size"]
    return image


def project(points, camera, size):
    x, y, z, yaw, focal, shift = camera
    forward = np.array([np.sin(yaw), np.cos(yaw), 0.0])
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.0])
    d = np.asarray(points) - np.array([x, y, z])
    depth = d @ forward
    assert (depth > 0).all()
    return np.stack(
        [size / 2 + focal * (d @ right) / depth, size / 2 + shift - focal * d[:, 2] / depth], axis=1
    )


def fit(spec, output):
    size = spec["reference"]["size"][0]
    model = np.array([c["modelM"] for c in spec["correspondences"]])
    pixels = np.array([c["pixel"] for c in spec["correspondences"]], dtype=float)
    centre = model.mean(axis=0)
    best = None
    for yaw in np.radians(np.arange(-80, 81, 10)):
        for distance in (10, 20, 40, 80):
            forward = np.array([np.sin(yaw), np.cos(yaw), 0])
            start = centre - forward * distance
            guess = [start[0], start[1], 1.7, yaw, distance * 200, 0]
            try:
                result = least_squares(
                    lambda q: (project(model, q, size) - pixels).ravel(),
                    guess,
                    x_scale=[5, 5, 1, 0.3, 1000, 100],
                )
            except AssertionError:
                continue
            depth = (model - result.x[:3]) @ np.array([np.sin(result.x[3]), np.cos(result.x[3]), 0])
            if (depth > 0).all() and (best is None or result.cost < best.cost):
                best = result
    assert best is not None
    x, y, z, yaw, focal, shift = best.x
    residual = project(model, best.x, size) - pixels
    camera = {
        "positionM": [x, y, z],
        "yawDegrees": float(np.degrees(yaw)),
        "forward": [float(np.sin(yaw)), float(np.cos(yaw)), 0.0],
        "focalPixels": focal,
        "lensMm": focal / size * SENSOR_MM,
        "sensorWidthMm": SENSOR_MM,
        "shiftY": shift / size,
        "imageSize": [size, size],
        "rmsPixels": float(np.sqrt((residual**2).sum(axis=1).mean())),
        "maxPixels": float(np.abs(residual).max()),
        "residualPixels": {
            c["name"]: r.round(1).tolist() for c, r in zip(spec["correspondences"], residual)
        },
    }
    output.mkdir(parents=True)
    (output / "camera.json").write_text(json.dumps(camera, indent=2) + "\n")
    print(json.dumps({k: camera[k] for k in ("positionM", "yawDegrees", "lensMm", "rmsPixels")}))


def to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def region_mean(image, boxes):
    return np.concatenate([image[y0:y1, x0:x1].reshape(-1, 3) for x0, y0, x1, y1 in boxes]).mean(
        axis=0
    )


def foreground(rgb, background):
    distance = np.linalg.norm(rgb.astype(float) - background, axis=2)
    return ndimage.binary_fill_holes(ndimage.binary_opening(distance > 30, iterations=2))


def compare(spec, render_dir, output):
    record = json.loads((render_dir / "render.json").read_text())
    rgba_path = ROOT / record["image"]["path"]
    assert digest(rgba_path) == record["image"]["sha256"]
    ref = reference(spec)
    rgba = np.asarray(Image.open(rgba_path).convert("RGBA")).astype(float) / 255
    assert rgba.shape[:2] == (ref.height, ref.width)
    background = np.array(spec["studioBackgroundSrgb"], dtype=float)
    alpha = rgba[..., 3:]
    # Straight-alpha render over the reference studio colour, blended in linear light.
    lin = to_linear(rgba[..., :3]) * alpha + to_linear(background / 255) * (1 - alpha)
    composite = (np.clip(to_srgb(lin), 0, 1) * 255).round().astype(np.uint8)
    output.mkdir(parents=True)
    Image.fromarray(composite).save(output / "render.png")
    ref_rgb = np.asarray(ref)
    ref_mask = foreground(ref_rgb, background)
    mask = foreground(composite, background)
    iou = float((ref_mask & mask).sum() / (ref_mask | mask).sum())
    regions = {}
    for name, boxes in spec["colourRegions"].items():
        r, m = region_mean(ref_rgb, boxes), region_mean(composite, boxes)
        regions[name] = {
            "referenceSrgb": r.round(1).tolist(),
            "renderSrgb": m.round(1).tolist(),
            "difference": (m - r).round(1).tolist(),
        }
    overlay = (0.5 * ref_rgb + 0.5 * composite).astype(np.uint8)
    edge = mask ^ ndimage.binary_erosion(mask, iterations=3)
    ref_edge = ref_mask ^ ndimage.binary_erosion(ref_mask, iterations=3)
    overlay[ref_edge] = (40, 200, 255)
    overlay[edge] = (255, 60, 40)
    tile = 960
    font = ImageFont.truetype(str(ROOT / "web/public/fonts/dm-sans.ttf"), 30)
    board = Image.new("RGB", (3 * tile, tile + 60), "#eeeae1")
    draw = ImageDraw.Draw(board)
    for i, (label, image) in enumerate(
        (
            ("Approved reference", ref_rgb),
            ("Blender Cycles render", composite),
            ("Outline overlay: reference cyan, render red", overlay),
        )
    ):
        board.paste(
            Image.fromarray(image).resize((tile, tile), Image.Resampling.LANCZOS), (i * tile, 60)
        )
        draw.text((i * tile + 16, 14), label, fill="#253c32", font=font)
    board.save(output / "compare.png")
    report = {
        "reference": spec["reference"],
        "render": record["image"],
        "silhouetteIoU": iou,
        "maskMethod": "sRGB distance from the studio colour above 30, opened twice and hole-filled; floor shadows count as foreground",
        "regions": regions,
        "outputs": {
            p.name: digest(output / p.name) for p in (output / "render.png", output / "compare.png")
        },
    }
    (output / "compare.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"silhouetteIoU": iou, "regions": {k: v["difference"] for k, v in regions.items()}}
        )
    )


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fit", "compare"))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--render", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads((ROOT / args.spec).read_text())
    assert spec["schemaVersion"] == 1
    output = ROOT / args.output
    assert (
        ".." not in args.output.parts
        and output.is_relative_to(ROOT / ".local")
        and not output.exists()
    )
    if args.action == "fit":
        reference(spec)
        fit(spec, output)
    else:
        compare(spec, ROOT / args.render, output)


if __name__ == "__main__":
    main()
