"""Publish verified Unreal character renders to the installed web server."""

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


def publish(selection, name, update=False):
    assert name.startswith("character-") and name.replace("-", "").isalnum()
    destination = ROOT / ".local/dist" / name
    assert destination.is_dir() == update, destination
    destination.mkdir(exist_ok=update)
    cards = []
    for item in selection["images"]:
        name = item["name"]
        assert name.replace("-", "").isalnum(), name
        source = ROOT / item["path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"], source
        shutil.copyfile(source, destination / (name + ".png"))
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
    document = document.replace("<!-- TITLE -->", html.escape(selection["title"]))
    document = document.replace("<!-- ASSESSMENT -->", html.escape(selection["assessment"]))
    temporary = destination / "index.html.next"
    with temporary.open("x") as stream:
        stream.write(document)
    temporary.replace(destination / "index.html")
    return destination / "index.html"


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("name")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    print(publish(selection, args.name, args.update))


if __name__ == "__main__":
    main()
