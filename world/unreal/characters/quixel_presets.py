"""Fetch and publish official Quixel preset portraits with explicit roster gaps."""

import argparse
import hashlib
import html
import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        assert response.status == 200, response.status
        return response.read()


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("page")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    assert args.page.startswith("character-") and args.page.replace("-", "").isalnum()
    output = args.output.resolve()
    assert output.is_relative_to(ROOT / ".local/evidence/characters")
    destination = ROOT / ".local/dist" / args.page
    assert destination.is_dir() == args.update, destination
    output.mkdir()
    selection = json.loads((HERE / "quixel-presets.json").read_text())
    roster = json.loads((HERE / "roster.json").read_text())["characters"]
    roles = {item["id"]: item for item in roster}
    selected = selection["characters"]
    assert len(selected) == 12
    assert {item["roster_id"] for item in selected} == set(roles)
    assert len({item["character_id"] for item in selected}) == 12
    raw = fetch(selection["api_url"])
    (output / "catalog.json").write_bytes(raw)
    catalog = json.loads(raw)["characters"]
    candidates = [item for item in catalog if item["mhcVersion"] == selection["mhc_version"]]
    by_id = {item["characterId"]: item for item in candidates}
    assert len(by_id) == len(candidates)

    def portrait(item):
        preset = by_id[item["character_id"]]
        assert preset["name"] == item["preset_name"]
        assert preset["status"] == "ready"
        assert "ue5+source" in preset["assetTypes"]
        url = preset["previewUrls"][-1]
        data = fetch(url)
        path = output / (item["roster_id"] + ".png")
        path.write_bytes(data)
        with Image.open(path) as image:
            assert image.format == "PNG"
            size = image.size
        return {
            **item,
            "metadata": preset,
            "portrait_url": url,
            "portrait_path": str(path.relative_to(ROOT)),
            "portrait_sha256": hashlib.sha256(data).hexdigest(),
            "portrait_bytes": len(data),
            "portrait_pixels": size,
            "model_downloaded": False,
            "model_imported": False,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(portrait, selected))
    destination.mkdir(exist_ok=args.update)
    sections = []
    labels = {"children": "Younger candidates", "adults": "Adults", "seniors": "Seniors"}
    for group, label in labels.items():
        cards = []
        for record in records:
            role = roles[record["roster_id"]]
            if role["group"] != group:
                continue
            stem = role["id"] + "-" + record["character_id"]
            shutil.copyfile(ROOT / record["portrait_path"], destination / (stem + ".png"))
            with Image.open(destination / (stem + ".png")) as image:
                image.thumbnail((1000, 1000))
                image.convert("RGB").save(destination / (stem + ".jpg"), quality=95)
            name = html.escape(record["preset_name"])
            target = html.escape(f"{role['name']} · target age {role['age']}")
            match = html.escape(record["match"])
            gap = html.escape(record["gap"])
            source = selection["catalog_url"] + "&assetId=" + record["character_id"]
            cards.append(
                f'<article><a class="portrait" href="{stem}.png">'
                f'<img src="{stem}.jpg" alt="Epic catalog portrait of {name}"></a>'
                f'<div class="details"><p class="target">{target}</p><h3>{name}</h3>'
                f'<p>{match}</p><p class="gap">{gap}</p>'
                f'<a href="{html.escape(source)}" target="_blank" rel="noopener">'
                "View on Quixel ↗</a></div></article>"
            )
        sections.append(
            f'<section><h2>{label}</h2><div class="grid">{"".join(cards)}</div></section>'
        )
    document = (
        (HERE / "preset-review.html")
        .read_text()
        .replace("<!-- CHARACTERS -->", "\n".join(sections))
    )
    temporary = destination / "index.html.next"
    with temporary.open("x") as stream:
        stream.write(document)
    temporary.replace(destination / "index.html")
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "catalog_url": selection["catalog_url"],
        "api_url": selection["api_url"],
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "catalog_preset_count": len(candidates),
        "mhc_version": selection["mhc_version"],
        "selection_sha256": hashlib.sha256((HERE / "quixel-presets.json").read_bytes()).hexdigest(),
        "image_kind": "Epic catalog portraits, not local Unreal renders",
        "model_acquisition_status": "Requires authenticated Quixel download; no 3D assets downloaded",
        "characters": records,
        "page": str((destination / "index.html").relative_to(ROOT)),
    }
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "page": args.page,
                "portraits": len(records),
                "sizes": [r["portrait_pixels"] for r in records],
            }
        )
    )


if __name__ == "__main__":
    main()
