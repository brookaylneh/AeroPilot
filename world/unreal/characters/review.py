"""Publish only selected generated character assets to the installed web server."""

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def copy_verified(source, target, expected):
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, source
    shutil.copyfile(source, target)


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    assert args.name.startswith("character-") and args.name.replace("-", "").isalnum()
    selected = json.loads(args.selection.read_text())
    roster = json.loads((HERE / "roster.json").read_text())["characters"]
    assert {item["id"] for item in selected} == {item["id"] for item in roster}
    destination = ROOT / ".local/dist" / args.name
    destination.mkdir(exist_ok=False)
    lock = json.loads((HERE / "viewer.lock.json").read_text())
    vendor = ROOT / ".local/vendor" / f"model-viewer-{lock['version']}"
    vendor.mkdir(exist_ok=True)
    for file in lock["files"]:
        source = vendor / file["name"]
        if not source.exists():
            response = httpx.get(file["url"], timeout=60, follow_redirects=True)
            response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest() == file["sha256"]
            source.write_bytes(response.content)
        copy_verified(source, destination / file["name"], file["sha256"])
    sections = []
    for group in ("children", "adults", "seniors"):
        cards = []
        for character in (item for item in roster if item["group"] == group):
            identity = character["id"]
            item = next(item for item in selected if item["id"] == identity)
            image_run = ROOT / item["image_run"]
            record = json.loads((image_run / "result.json").read_text())
            assert record["character"]["id"] == identity
            image = record["files"][0]
            copy_verified(ROOT / image["path"], destination / f"{identity}.png", image["sha256"])
            with Image.open(destination / f"{identity}.png") as source:
                source.thumbnail((640, 960))
                source.convert("RGB").save(destination / f"{identity}.jpg", quality=93)
            title = html.escape(f"{character['name']} · age {character['age']}")
            note = html.escape(item["review"])
            model_button = ""
            if item["mesh_run"] is not None:
                mesh_record = json.loads((ROOT / item["mesh_run"] / "result.json").read_text())
                assert mesh_record["source_image_sha256"] == image["sha256"]
                model = mesh_record["files"][0]
                model_name = f"{identity}.glb"
                copy_verified(ROOT / model["path"], destination / model_name, model["sha256"])
                model_button = (
                    f'<button type="button" data-model="{model_name}" data-title="{title}">'
                    "Inspect 3D model</button>"
                )
            cards.append(
                f'<article><a href="{identity}.png"><img src="{identity}.jpg" '
                f'alt="{title}, full-body reference" loading="lazy"></a>'
                f'<div class="text"><h3>{title}</h3><p>{note}</p>{model_button}</div></article>'
            )
        sections.append(f'<h2>{group.title()}</h2><div class="grid">{"".join(cards)}</div>')
    document = (
        (HERE / "review.html").read_text().replace("<!-- CHARACTERS -->", "\n".join(sections))
    )
    (destination / "index.html").write_text(document)
    origin = dotenv_values(ROOT / ".local/runtime.env")["SNR_ORIGIN"]
    print(f"{origin}/{args.name}/index.html")


if __name__ == "__main__":
    main()
