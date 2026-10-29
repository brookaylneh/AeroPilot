"""Fit studio cameras to an asset's approved references and compare renders with them.

python -m world.unreal.blender.reference_match fit <family> <asset> --input <build dir> --output <dir>
python -m world.unreal.blender.reference_match compare <family> <asset> --input <render dir> --output <dir>

`fit` writes cameras.json: the master camera solved from model-to-pixel correspondences, an
orthographic camera per turnaround panel scaled to the panel silhouette, the 30 m drone nadir
cropped to the asset and a level eye-height view. `compare` composites each render over its
reference background and writes compare.json and a contact sheet of at most 1600 px.
"""

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from scipy import ndimage
from scipy.optimize import least_squares

from world.unreal.blender.spec import ROOT, SIZE, VIEWS, digest, load, output_dir

REFERENCES = ROOT / ".local/references"
PANEL = SIZE // 2
DRONE = {"resolution": [3840, 2160], "hfovDegrees": 70, "altitudeM": 30}
EYE = {"resolution": [1920, 1080], "heightM": 1.7, "bearingDegrees": 35}
TILE = 400
# Median border sRGB of the 60 approved reference sheets, behind assets without a reference.
STUDIO = np.array([202.0, 203.5, 201.5])


def references(spec):
    """The master and each turnaround panel as sRGB arrays, checked against the reference index."""
    index = {a["id"]: a for a in json.loads((REFERENCES / "index.json").read_text())["assets"]}
    files = index[spec["reference"]]["files"]
    sheets = {}
    for kind in ("master", "turnaround"):
        path = REFERENCES / files[kind]["file"]
        assert digest(path) == files[kind]["sha256"], path
        sheet = Image.open(path).convert("RGB")
        assert sheet.size == tuple(files[kind]["size"]) and sheet.width == sheet.height, path
        # Larger masters (the 4096 px fire-lookout) are compared at the turnaround resolution.
        sheets[kind] = np.asarray(sheet.resize((SIZE, SIZE), Image.Resampling.LANCZOS))
    views = {"master": sheets["master"]}
    for view, (column, row) in spec["panels"].items():
        cell = np.s_[row * PANEL : (row + 1) * PANEL, column * PANEL : (column + 1) * PANEL]
        views[view] = sheets["turnaround"][cell]
    return views


def basis(yaw, pitch):
    """Camera right, up and forward unit vectors for a heading and pitch in radians."""
    forward = np.array([np.sin(yaw) * np.cos(pitch), np.cos(yaw) * np.cos(pitch), np.sin(pitch)])
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.0])
    return right, np.cross(right, forward), forward


def project(points, camera, size):
    """Pixels of a level pinhole (x, y, z, yaw, focal, vertical shift) on a square image."""
    right, up, forward = basis(camera[3], 0)
    d = np.asarray(points) - camera[:3]
    x, y, depth = d @ right, d @ up, d @ forward
    return np.stack(
        [size / 2 + camera[4] * x / depth, size / 2 + camera[5] - camera[4] * y / depth], 1
    )


def fit_master(correspondences, size):
    model = np.array([c["modelM"] for c in correspondences])
    pixels = np.array([c["pixel"] for c in correspondences], dtype=float)
    best = None
    for yaw in np.radians(np.arange(-180, 180, 15)):
        for distance in (10, 20, 40, 80):
            start = model.mean(axis=0) - basis(yaw, 0)[2] * distance
            result = least_squares(
                lambda q: (project(model, q, size) - pixels).ravel(),
                [start[0], start[1], 1.7, yaw, distance * 200, 0],
                x_scale=[5, 5, 1, 0.3, 1000, 100],
            )
            depth = (model - result.x[:3]) @ basis(result.x[3], 0)[2]
            if (depth > 0).all() and (best is None or result.cost < best.cost):
                best = result
    assert best is not None
    residual = project(model, best.x, size) - pixels
    return {
        "projection": "perspective",
        "positionM": best.x[:3].tolist(),
        "yawDegrees": float((np.degrees(best.x[3]) + 180) % 360 - 180),
        "pitchDegrees": 0.0,
        "resolution": [size, size],
        "focalPixels": float(best.x[4]),
        "shiftYPixels": float(best.x[5]),
        "rmsPixels": float(np.sqrt((residual**2).sum(axis=1).mean())),
        "maxPixels": float(np.abs(residual).max()),
        "residualPixels": {
            c["name"]: r.round(1).tolist() for c, r in zip(correspondences, residual)
        },
    }


def corners(bounds):
    return np.array(list(itertools.product(*np.transpose(bounds))))


def fit_panel(mask, view, bounds):
    """Orthographic camera along `view` whose projected bounds best fill the silhouette box."""
    right, up, forward = basis(*np.radians(VIEWS[view]))
    points = corners(bounds)
    low = np.array([(points @ right).min(), (points @ up).min()])
    extent = np.array([np.ptp(points @ right), np.ptp(points @ up)])
    ys, xs = np.nonzero(mask)
    box = np.array([xs.min(), ys.min(), xs.max() + 1, ys.max() + 1])
    size = box[2:] - box[:2]
    scale = size @ extent / (extent @ extent)
    # Image y runs down while `up` runs up.
    offset = ((box[:2] + box[2:]) / 2 - PANEL / 2) / scale * [1, -1]
    centre = low + extent / 2 - offset
    position = right * centre[0] + up * centre[1] + forward * ((points @ forward).min() - 100)
    return {
        "projection": "orthographic",
        "positionM": position.tolist(),
        "yawDegrees": float(VIEWS[view][0]),
        "pitchDegrees": float(VIEWS[view][1]),
        "resolution": [PANEL, PANEL],
        "metresPerPixel": float(1 / scale),
        "shiftYPixels": 0.0,
        "silhouetteBoxPixels": box.tolist(),
        "boxResidualPixels": (size - scale * extent).round(1).tolist(),
    }


def nadir(bounds):
    """The drone camera over the bounds centre, cropped to the asset with a 10 % margin."""
    width, height = DRONE["resolution"]
    focal = width / 2 / np.tan(np.radians(DRONE["hfovDegrees"]) / 2)
    points = corners(bounds)
    assert (points[:, 2] < DRONE["altitudeM"] - 1).all(), bounds
    centre = bounds.mean(axis=0)
    right, up, _ = basis(np.radians(90), np.radians(-90))
    offsets = np.abs(np.stack([(points - centre) @ right, (points - centre) @ up], axis=1))
    half = (focal * offsets / (DRONE["altitudeM"] - points[:, 2:])).max(axis=0)
    crop = 2 * np.ceil(1.1 * half).astype(int)
    assert crop[0] <= width and crop[1] <= height, crop
    return {
        "projection": "perspective",
        "positionM": [centre[0], centre[1], DRONE["altitudeM"]],
        "yawDegrees": 90.0,
        "pitchDegrees": -90.0,
        "resolution": crop.tolist(),
        "focalPixels": float(focal),
        "shiftYPixels": 0.0,
        "groundMetresPerPixel": float(DRONE["altitudeM"] / focal),
    }


def eye(bounds):
    """Level eye-height view from the front-left quarter with the bounds in 90 % of the frame."""
    width, height = EYE["resolution"]
    focal = width / 2 / np.tan(np.radians(DRONE["hfovDegrees"]) / 2)
    bearing = np.radians(EYE["bearingDegrees"])
    yaw = np.arctan2(-np.cos(bearing), -np.sin(bearing))
    right, _, forward = basis(yaw, 0)
    centre = bounds.mean(axis=0) * [1, 1, 0]
    points = corners(bounds) - centre
    reach = np.maximum(
        np.abs(points @ right) / width, np.abs(points[:, 2] - EYE["heightM"]) / height
    )
    distance = (focal * reach / 0.45 - points @ forward).max()
    return {
        "projection": "perspective",
        "positionM": [*(centre - forward * distance)[:2], EYE["heightM"]],
        "yawDegrees": float(np.degrees(yaw)),
        "pitchDegrees": 0.0,
        "resolution": [width, height],
        "focalPixels": float(focal),
        "shiftYPixels": 0.0,
    }


def fit(spec, build, output):
    report = json.loads((build / "build.json").read_text())
    assert report["spec"] == spec, "build.json was made from a different spec"
    bounds = np.array(report["boundsM"])
    assert bounds.shape == (2, 3) and (bounds[1] > bounds[0]).all()
    assert np.isclose(bounds[0, 2], -spec["buriedM"], rtol=0, atol=1e-9), bounds
    # References show the asset above grade.
    bounds[0, 2] = 0
    cameras = {}
    if spec["reference"]:
        views = references(spec)
        cameras["master"] = fit_master(spec["correspondences"], SIZE)
        for view in spec["panels"]:
            cameras[view] = fit_panel(foreground(views[view]), view, bounds)
    cameras["nadir"], cameras["eye"] = nadir(bounds), eye(bounds)
    record = {"build": source(build / "build.json"), "views": cameras}
    (output / "cameras.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({v: c["rmsPixels"] for v, c in cameras.items() if "rmsPixels" in c}))


def to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def background(rgb):
    """Median sRGB of the 8 px image border."""
    border = [rgb[:8], rgb[-8:], rgb[:, :8], rgb[:, -8:]]
    return np.median(np.concatenate([b.reshape(-1, 3) for b in border]), axis=0)


def foreground(rgb):
    """Textured pixels: luminance deviation above 4 sRGB levels in 7 px windows, closed and
    hole-filled, then eroded by the window half-width. Smooth backgrounds and soft floor shadows
    stay background, while pale surfaces near the background colour stay foreground."""
    lum = rgb.astype(float).mean(axis=2)
    mean = ndimage.uniform_filter(lum, 7)
    deviation = np.sqrt(np.maximum(ndimage.uniform_filter(lum**2, 7) - mean**2, 0))
    mask = ndimage.binary_fill_holes(ndimage.binary_closing(deviation > 4, iterations=4))
    return ndimage.binary_erosion(mask, np.ones((3, 3)), iterations=3)


def iou(a, b):
    return float((a & b).sum() / (a | b).sum())


def composite(rgba, colour):
    """Straight-alpha render over the studio colour, blended in linear light."""
    assert rgba.dtype == np.uint8 and rgba.shape[2] == 4
    rgba = rgba / 255
    alpha = rgba[..., 3:]
    lin = to_linear(rgba[..., :3]) * alpha + to_linear(colour / 255) * (1 - alpha)
    return (np.clip(to_srgb(lin), 0, 1) * 255).round().astype(np.uint8)


def region_mean(image, boxes):
    return np.concatenate([image[y0:y1, x0:x1].reshape(-1, 3) for x0, y0, x1, y1 in boxes]).mean(0)


def tile(rgb, label, font, outlines=()):
    """A labelled TILE-square thumbnail with mask outlines drawn after downscaling."""
    pad = tuple(int(c) for c in rgb[0, 0])
    small = ImageOps.pad(Image.fromarray(rgb), (TILE, TILE), Image.Resampling.LANCZOS, pad)
    pixels = np.array(small)
    for mask, colour in outlines:
        edge = np.array(ImageOps.pad(Image.fromarray(mask), (TILE, TILE), Image.Resampling.NEAREST))
        pixels[edge ^ ndimage.binary_erosion(edge)] = colour
    image = Image.fromarray(pixels)
    ImageDraw.Draw(image).text((8, 6), label, fill="#253c32", font=font)
    return image


def source(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": digest(path)}


def compare(spec, render, output):
    record = json.loads((render / "render.json").read_text())
    views = references(spec) if spec["reference"] else {}
    font = ImageFont.truetype(str(ROOT / "web/public/fonts/dm-sans.ttf"), 22)
    report, tiles = {}, []
    for view, entry in record["images"].items():
        path = render / f"{view}.png"
        assert digest(path) == entry["sha256"], path
        if view not in views:
            colour = background(views["master"]) if views else STUDIO
            report[view] = {"backgroundSrgb": colour.tolist()}
            tiles.append(tile(composite(np.asarray(Image.open(path)), colour), view, font))
            continue
        reference = views[view]
        colour = background(reference)
        image = composite(np.asarray(Image.open(path)), colour)
        assert image.shape == reference.shape, view
        target, mask = foreground(reference), foreground(image)
        score = iou(target, mask)
        report[view] = {"backgroundSrgb": colour.tolist(), "silhouetteIoU": score}
        outlines = ((target, (40, 200, 255)), (mask, (255, 60, 40)))
        tiles.append(tile(reference, f"{view} reference", font))
        tiles.append(tile(image, f"{view} render, IoU {score:.3f}", font, outlines))
    regions = {}
    if views:
        image = np.asarray(Image.open(render / "master.png"))
        master = composite(image, background(views["master"]))
        for name, boxes in spec["colourRegions"].items():
            r, m = region_mean(views["master"], boxes), region_mean(master, boxes)
            regions[name] = {
                "referenceSrgb": r.round(1).tolist(),
                "renderSrgb": m.round(1).tolist(),
                "differenceSrgb": (m - r).round(1).tolist(),
            }
    sheet = Image.new("RGB", (4 * TILE, -(-len(tiles) // 4) * TILE), "#eeeae1")
    for i, image in enumerate(tiles):
        sheet.paste(image, (i % 4 * TILE, i // 4 * TILE))
    sheet.save(output / "contact.png")
    result = {
        "render": source(render / "render.json"),
        "maskMethod": "luminance deviation above 4 sRGB levels in 7 px windows, closed, hole-filled "
        "and eroded 3 px; soft floor shadows are background. Outlines: reference cyan, render red.",
        "views": report,
        "masterRegions": regions,
        "contactSheet": source(output / "contact.png"),
    }
    (output / "compare.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({v: r["silhouetteIoU"] for v, r in report.items() if "silhouetteIoU" in r}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fit", "compare"))
    parser.add_argument("family")
    parser.add_argument("asset")
    parser.add_argument("--input", type=Path, required=True, help="build (fit) or render directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = load(args.family)[args.asset]
    action = {"fit": fit, "compare": compare}[args.action]
    action(spec, (ROOT / args.input).resolve(), output_dir(args.output))


if __name__ == "__main__":
    main()
