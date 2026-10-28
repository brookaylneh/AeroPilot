"""Hash the roster assets and verify completed builds, captures and public galleries."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env
from world.unreal.characters.review_metahuman import HERE, ROOT


def read(path):
    return json.loads(path.read_text())


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def file_record(path):
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def preserved(path):
    evidence = path.parent
    before = read(path)
    result = {}
    for name in ("metahuman-direct-nia", "metahuman-uploaded-face"):
        original = read(evidence.parent / name / "asset-files.json")
        for item in original["files"]:
            path = ROOT / item["path"]
            assert path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], path
        result[name] = before[name]
    for item in before["comparison_page"]:
        assert digest(ROOT / item["path"]) == item["sha256"], item["path"]
    result["comparison_files_unchanged"] = len(before["comparison_page"])
    return result


def character(person, evidence, selection):
    output = ROOT / selection["evidence"]
    recipe_path = HERE / "recipes" / (person["id"] + ".json")
    recipe = read(recipe_path)
    preparation_path = output / selection["preparation"]
    preparation = read(preparation_path)
    assert preparation["cloud"] == {"rigged": True, "high_resolution_textures": True}
    assert preparation["wardrobe"]["wardrobe"] == recipe["wardrobe"]
    preparation_process = read(preparation_path.with_name(preparation_path.stem + "-process.json"))
    assert preparation_process["exit_code"] == 0
    assembly_path = output / selection["assembly"]
    assembly = read(assembly_path)
    assert assembly["material_readback"]
    process_path = output / selection["capture_process"]
    process = read(process_path)
    assert process["exit_code"] == 0
    texture_report = output / selection["texture_inventory"]
    capture = read(output / "render/result.json")
    assert capture["console_variables_verified"] and len(capture["images"]) == 7
    assert len(capture["geometry"]["grooms"]) >= 3
    images = []
    for name in capture["images"]:
        path = Path(name)
        with Image.open(path) as image:
            assert image.size == (4096, 4096)
        images.append(file_record(path))
    asset_root = (
        ROOT
        / "world/unreal/ScenarioStudio/Content"
        / Path(recipe["asset_folder"]).parent.relative_to("/Game")
    )
    files = [file_record(p) for p in sorted(asset_root.rglob("*")) if p.is_file()]
    assert files
    manifest = {"files": files, "total_bytes": sum(item["bytes"] for item in files)}
    with (output / "asset-files.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
    browser = read(evidence / ("browser-" + person["id"]) / "result.json")
    assert browser["status"] == 200 and browser["errors"] == [] and browser["browser_closed"]
    assert len(browser["images"]) == 7
    texture_inventory = read(texture_report)
    assert set(texture_inventory["assets"]) == set(assembly["assets"])
    assert texture_inventory["export_quality"] == "Cinematic"
    resolutions = Counter((item["width"], item["height"]) for item in texture_inventory["textures"])
    assert (8192, 8192) in resolutions, "Expected completed 8K skin textures"
    return {
        "id": person["id"],
        "name": person["name"],
        "target_age": person["age"],
        "visual_assessment": selection["assessment"],
        "recipe": file_record(recipe_path),
        "evidence": str(output.relative_to(ROOT)),
        "rigged": True,
        "preparation": file_record(preparation_path),
        "assembly": file_record(assembly_path),
        "capture_process": file_record(process_path),
        "wardrobe": recipe["wardrobe"],
        "grooms": capture["geometry"]["grooms"],
        "texture_inventory": file_record(texture_report),
        "texture_dimensions": [
            {"width": w, "height": h, "count": n} for (w, h), n in sorted(resolutions.items())
        ],
        "asset_files": len(files),
        "asset_bytes": manifest["total_bytes"],
        "capture_process_seconds": process["elapsed_seconds"],
        "images": images,
        "browser": file_record(evidence / ("browser-" + person["id"]) / "result.json"),
    }


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    manifest = read(args.manifest)
    evidence = args.manifest.resolve().parent
    roster = read(HERE / "roster.json")["characters"]
    assert Counter(item["group"] for item in roster) == {"children": 4, "adults": 4, "seniors": 4}
    assert min(item["age"] for item in roster) >= 8
    selections = manifest["characters"]
    assert selections.keys() == {item["id"] for item in roster}
    browser = read(evidence / "browser/result.json")
    assert browser["status"] == 200 and browser["errors"] == [] and browser["browser_closed"]
    assert len(browser["images"]) == 24
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Appearance review; animation, final clothing and live integration are not accepted.",
        "preserved": preserved(ROOT / manifest["preserved"]),
        "characters": [character(person, evidence, selections[person["id"]]) for person in roster],
        "gallery": file_record(evidence / "gallery.json"),
        "browser": file_record(evidence / "browser/result.json"),
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
