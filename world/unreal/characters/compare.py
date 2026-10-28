"""Publish same-image mesh comparisons with geometry and base-color diagnostics."""

import argparse
import copy
import html
import json
import struct
from pathlib import Path

from dotenv import dotenv_values
from PIL import Image

from mission_planner.environment import load_env
from world.unreal.characters.generate import digest, write_json
from world.unreal.characters.inspect_mesh import read_glb
from world.unreal.characters.review import copy_verified

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def diagnostic(source, target, mode):
    document, binary = read_glb(source)
    for material in document["materials"]:
        base = copy.deepcopy(material["pbrMetallicRoughness"])
        material.clear()
        if mode == "unlit":
            material["pbrMetallicRoughness"] = {
                key: value for key, value in base.items() if key.startswith("baseColor")
            }
            material["extensions"] = {"KHR_materials_unlit": {}}
        else:
            assert mode == "clay"
            material["pbrMetallicRoughness"] = {
                "baseColorFactor": [0.5, 0.5, 0.5, 1],
                "metallicFactor": 0,
                "roughnessFactor": 1,
            }
        material["doubleSided"] = True
    if mode == "unlit":
        if "extensionsUsed" not in document:
            document["extensionsUsed"] = []
        if "KHR_materials_unlit" not in document["extensionsUsed"]:
            document["extensionsUsed"].append("KHR_materials_unlit")
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    raw = (
        struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary))
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    with target.open("xb") as stream:
        stream.write(raw)
    check, check_binary = read_glb(target)
    assert check_binary == binary and check["meshes"] == document["meshes"]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("name")
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    assert args.name.startswith("character-") and args.name.replace("-", "").isalnum()
    selected = json.loads(args.selection.read_text())
    assert selected and len({item["key"] for item in selected}) == len(selected)
    destination = ROOT / ".local/dist" / args.name
    destination.mkdir(exist_ok=False)
    lock = json.loads((HERE / "viewer.lock.json").read_text())
    vendor = ROOT / ".local/vendor" / f"model-viewer-{lock['version']}"
    for file in lock["files"]:
        copy_verified(vendor / file["name"], destination / file["name"], file["sha256"])
    cards = []
    inputs = {}
    for item in selected:
        key = item["key"]
        assert key.replace("-", "").isalnum()
        image = json.loads((ROOT / item["image_run"] / "result.json").read_text())
        mesh = json.loads((ROOT / item["mesh_run"] / "result.json").read_text())
        assert image["character"]["id"] == mesh["character"]["id"] == item["id"]
        source_hash = image["files"][0]["sha256"]
        assert source_hash == mesh["source_image_sha256"]
        if item["id"] in inputs:
            assert inputs[item["id"]] == source_hash, "Compared meshes must share their input"
        inputs[item["id"]] = source_hash
        copy_verified(ROOT / image["files"][0]["path"], destination / f"{key}.png", source_hash)
        with Image.open(destination / f"{key}.png") as reference:
            reference.thumbnail((640, 960))
            reference.convert("RGB").save(destination / f"{key}.jpg", quality=93)
        model = mesh["files"][0]
        original = destination / f"{key}-pbr.glb"
        copy_verified(ROOT / model["path"], original, model["sha256"])
        title = html.escape(item["label"])
        buttons = []
        for mode, label in (("pbr", "Original PBR"), ("unlit", "Base color"), ("clay", "Clay")):
            name = f"{key}-{mode}.glb"
            if mode != "pbr":
                diagnostic(original, destination / name, mode)
            buttons.append(
                f'<button type="button" data-model="{name}" data-title="{title} · {label}">'
                f"{label}</button>"
            )
        cards.append(
            f'<article><a href="{key}.png"><img src="{key}.jpg" '
            f'alt="{title} input reference"></a><div class="text"><h3>{title}</h3>'
            f'<div class="controls">{"".join(buttons)}</div></div></article>'
        )
    document = (HERE / "review.html").read_text()
    document = document.replace("Character reference review", "Character model comparison")
    document = document.replace(
        "Twelve fictional people. Click an image for its original resolution. Ages describe the generation brief.",
        "Same input for each model. Original PBR shows the delivered materials; base color removes "
        "lighting; clay shows geometry without textures or normal maps. Click a reference to enlarge.",
    )
    document = document.replace("<!-- CHARACTERS -->", f'<div class="grid">{"".join(cards)}</div>')
    (destination / "index.html").write_text(document)
    origin = dotenv_values(ROOT / ".local/runtime.env")["SNR_ORIGIN"]
    url = f"{origin}/{args.name}/index.html"
    files = [
        {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(destination.iterdir())
    ]
    write_json(args.record, {"url": url, "selection": selected, "inputs": inputs, "files": files})
    print(url)


if __name__ == "__main__":
    main()
