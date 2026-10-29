"""Compose a labelled contact sheet from six actual mesh renders."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renders", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = ROOT / args.output
    report = json.loads(args.renders.read_text())
    assert len(report["renders"]) == 6
    assert args.output.resolve().is_relative_to((ROOT / ".local/evidence/world").resolve())
    assert not args.output.exists()
    width, height, header, footer = 1440, 1160, 170, 100
    sheet = Image.new("RGB", (width * 3, height * 2 + header + footer), "#efeee8")
    draw = ImageDraw.Draw(sheet)
    font = str(ROOT / "web/public/fonts/dm-sans.ttf")
    title = ImageFont.truetype(font, 64)
    label = ImageFont.truetype(font, 38)
    note = ImageFont.truetype(font, 34)
    draw.text((40, 24), args.title, fill="#203b34", font=title)
    draw.text(
        (42, 108),
        "Actual generated mesh | Blender Cycles preview | Original geometry and imported materials",
        fill="#52635c",
        font=note,
    )
    validation = []
    for index, frame in enumerate(report["renders"]):
        path = ROOT / frame["path"]
        assert digest(path) == frame["sha256"]
        with Image.open(path) as source:
            image = source.convert("RGB")
        assert image.size == (width, 1080)
        stats = ImageStat.Stat(image)
        assert max(stats.stddev) > 5, (path, stats.stddev)
        x, y = index % 3 * width, index // 3 * height + header
        sheet.paste(image, (x, y))
        label_text = (
            f"Perspective {index + 1}"
            if frame["view"].startswith("orbit-")
            else {"side-positive-y": "Straight-on view", "overhead": "Overhead"}[frame["view"]]
        )
        draw.text((x + 30, y + 1100), label_text, fill="#203b34", font=label)
        validation.append({"path": frame["path"], "meanRgb": stats.mean, "stddevRgb": stats.stddev})
    draw.text(
        (40, sheet.height - 76),
        f"{report['triangles']:,} triangles | Uniform framing scale | Unreal lighting assessment pending",
        fill="#52635c",
        font=note,
    )
    sheet.save(args.output)
    preview = args.output.with_name(args.output.stem + "-preview.jpg")
    assert not preview.exists()
    sheet.thumbnail((2160, 2160), Image.Resampling.LANCZOS)
    sheet.save(preview, quality=95, subsampling=0)
    receipt = {
        "image": str(args.output.relative_to(ROOT)),
        "sha256": digest(args.output),
        "preview": str(preview.relative_to(ROOT)),
        "rendersSha256": digest(args.renders),
        "scriptSha256": digest(Path(__file__)),
        "pixelValidation": validation,
        "sources": report["renders"],
    }
    args.output.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
