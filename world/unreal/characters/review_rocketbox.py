"""Publish the three Rocketbox renders, source previews and native sensor crops."""

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("render", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("page")
    args = parser.parse_args()
    assert args.page.startswith("character-") and args.page.replace("-", "").isalnum()
    report = json.loads((args.render / "result.json").read_text())
    assert len(report["images"]) == 15
    manifest = json.loads((HERE / "rocketbox-assets.lock.json").read_text())
    destination = ROOT / ".local/dist" / args.page
    args.output.mkdir(parents=True, exist_ok=False)
    destination.mkdir(exist_ok=False)
    records = []
    sections = []
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
            "<section><h2>"
            + html.escape(character["label"])
            + "</h2><p>"
            + html.escape(character["name"])
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
        .replace("<!-- CHARACTERS -->", "\n".join(sections))
    )
    with (destination / "index.html").open("x") as stream:
        stream.write(document)
    result = {
        "url": "http://35.81.229.78/" + args.page + "/index.html",
        "source_commit": manifest["commit"],
        "images": records,
        "sensor_crop_xyxy": [1600, 760, 2240, 1400],
        "sensor_crop_resampled": False,
        "render_report": str((args.render / "result.json").resolve().relative_to(ROOT)),
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"url": result["url"], "images": len(records)}))


if __name__ == "__main__":
    main()
