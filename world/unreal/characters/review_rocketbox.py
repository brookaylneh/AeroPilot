"""Publish Rocketbox renders, source previews and native sensor crops."""

import argparse
import hashlib
import html
import json
import os
import shutil
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("render", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("page")
    args = parser.parse_args()
    assert args.page.startswith("character-") and args.page.replace("-", "").isalnum()
    report = json.loads((args.render / "result.json").read_text())
    manifest = json.loads(args.manifest.read_text())
    assert len(report["images"]) == 5 * len(manifest["characters"])
    destination = ROOT / ".local/dist" / args.page
    args.output.mkdir(parents=True, exist_ok=False)
    destination.mkdir(exist_ok=False)
    records = []
    sections = []
    overview = []
    vendor = ROOT / manifest["vendor"]

    def copy(source, name, caption, native=False):
        path = destination / (name + ".png")
        shutil.copyfile(source, path)
        with Image.open(path) as image:
            width, height = image.size
            image.thumbnail((1000, 1000))
            image.convert("RGB").save(destination / (name + ".jpg"), quality=95)
        records.append(
            {
                "name": name,
                "source": str(source.resolve().relative_to(ROOT)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_px": [width, height],
                "caption": caption,
            }
        )
        css = ' class="native"' if native else ""
        return f'<figure{css}><a href="{name}.png"><img src="{name}.jpg" alt="{html.escape(caption)}"></a><figcaption>{html.escape(caption)}</figcaption></figure>'

    for character in manifest["characters"]:
        identifier = character["id"]
        overview.append(
            f'<a href="#{identifier}"><img src="{identifier}-face.jpg" '
            f'alt="{html.escape(character["label"])}">{html.escape(character["label"])}</a>'
        )
        cards = []
        for view, label in [
            ("face", "Face · Unreal 4096 × 4096"),
            ("front", "Full body · Unreal 4096 × 4096"),
            ("three-quarter", "Three-quarter · Unreal 4096 × 4096"),
        ]:
            name = identifier + "-" + view
            cards.append(copy(args.render / (name + ".png"), name, label))
        cards.append(
            copy(
                vendor / character["preview"],
                identifier + "-source",
                "Microsoft’s original catalog preview · different lighting",
            )
        )
        links = []
        for view, label in [
            ("drone-nadir", "Directly overhead"),
            ("drone-oblique", "Oblique view"),
        ]:
            name = identifier + "-" + view
            source = args.render / (name + ".png")
            with Image.open(source) as image:
                assert image.size == (3840, 2160)
                box = (1600, 760, 2240, 1400)
                cropped = args.output / (name + "-crop.png")
                image.crop(box).save(cropped)
            cards.append(
                copy(cropped, name + "-crop", label + " · 640 × 640 crop, no upscaling", True)
            )
            copy(source, name, label + " · original 3840 × 2160 frame")
            links.append(
                f'<a class="full-frame" href="{name}.png">{label}: full 4K sensor frame</a>'
            )
        sections.append(
            f'<section id="{identifier}"><h2>'
            + html.escape(character["label"])
            + "</h2><p>"
            + html.escape(character["name"])
            + "</p><p>"
            + html.escape(character["assessment"])
            + '</p><div class="grid">'
            + "".join(cards)
            + '</div><p class="links">'
            + " · ".join(links)
            + "</p></section>"
        )
    shutil.copyfile(vendor / "LICENSE.md", destination / "LICENSE.txt")
    document = (
        (HERE / "rocketbox-review.html")
        .read_text()
        .replace("<!-- TITLE -->", html.escape(manifest["review"]["title"]))
        .replace("<!-- DESCRIPTION -->", html.escape(manifest["review"]["description"]))
        .replace("<!-- ASSESSMENT -->", html.escape(manifest["review"]["assessment"]))
        .replace("<!-- OVERVIEW -->", "\n".join(overview))
        .replace("<!-- CHARACTERS -->", "\n".join(sections))
    )
    with (destination / "index.html").open("x") as stream:
        stream.write(document)
    result = {
        "url": os.environ["SNR_ORIGIN"] + "/" + args.page + "/index.html",
        "source_commit": manifest["commit"],
        "manifest": str(args.manifest.resolve().relative_to(ROOT)),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "images": records,
        "sensor_crop_xyxy": [1600, 760, 2240, 1400],
        "sensor_crop_resampled": False,
        "render_report": str((args.render / "result.json").resolve().relative_to(ROOT)),
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"url": result["url"], "images": len(records)}))


if __name__ == "__main__":
    main()
