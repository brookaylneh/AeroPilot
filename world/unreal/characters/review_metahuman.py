"""Publish verified Unreal character renders alongside their source image and SAM body."""

import argparse
import html
import json
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env
from world.unreal.characters.review import HERE, ROOT, copy_verified


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    assert args.name.startswith("character-") and args.name.replace("-", "").isalnum()
    selection = json.loads(args.selection.read_text())
    destination = ROOT / ".local/dist" / args.name
    destination.mkdir(exist_ok=False)
    cards = []
    for item in selection["images"]:
        name = item["name"]
        assert name.replace("-", "").isalnum(), name
        copy_verified(ROOT / item["path"], destination / (name + ".png"), item["sha256"])
        with Image.open(destination / (name + ".png")) as source:
            source.thumbnail((1000, 1000))
            source.convert("RGB").save(destination / (name + ".jpg"), quality=95)
        caption = html.escape(item["caption"])
        cards.append(
            f'<figure><a href="{name}.png"><img src="{name}.jpg" alt="{caption}"></a>'
            f"<figcaption>{caption}</figcaption></figure>"
        )
    document = (HERE / "metahuman-review.html").read_text()
    document = document.replace("<!-- IMAGES -->", "\n".join(cards))
    document = document.replace("<!-- ASSESSMENT -->", html.escape(selection["assessment"]))
    (destination / "index.html").write_text(document)
    print(destination / "index.html")


if __name__ == "__main__":
    main()
