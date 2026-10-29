"""Assemble generated design references and separate their labelled camera views."""

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]


def verified_image(record):
    path = ROOT / record["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"], path
    image = Image.open(path).convert("RGB")
    assert image.size == (record["width"], record["height"]), path
    return image


def receipt(path):
    with Image.open(path) as image:
        width, height = image.size
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
        "bytes": len(data),
    }


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assets = json.loads((ROOT / "world/unreal/valley-reference-assets.json").read_text())["assets"]
    selected = json.loads(args.selection.read_text())["assets"]
    assert {a["id"] for a in assets} == set(selected)
    output = ROOT / args.output
    assert output.resolve().is_relative_to((ROOT / ".local/world/valley").resolve())
    output.mkdir(parents=True)
    regular = ROOT / "web/public/fonts/dm-sans.ttf"
    title_font = ImageFont.truetype(str(regular), 94)
    name_font = ImageFont.truetype(str(regular), 38)
    note_font = ImageFont.truetype(str(regular), 28)
    columns, tile = 5, 1000
    heading, row_height = 280, 1120
    rows = math.ceil(len(assets) / columns)
    sheet = Image.new("RGB", (columns * tile, heading + rows * row_height), "#eeeae1")
    draw = ImageDraw.Draw(sheet)
    draw.text((54, 32), "SIERRA SEARCH VALLEY", fill="#253c32", font=title_font)
    draw.text(
        (58, 151),
        "Asset design collection · Late October at Tuolumne Meadows",
        fill="#52604e",
        font=name_font,
    )
    draw.text(
        (58, 213),
        "AI-generated appearance references · Proposed designs, not installed Unreal assets",
        fill="#52604e",
        font=note_font,
    )
    records, sections = [], []
    for index, asset in enumerate(assets):
        choice = selected[asset["id"]]
        master = verified_image(choice["master"])
        x, y = index % columns * tile, heading + index // columns * row_height
        master.thumbnail((tile - 28, tile - 28), Image.Resampling.LANCZOS)
        sheet.paste(master, (x + (tile - master.width) // 2, y + 14))
        name = f"{index + 1:02d}  {asset['name']}"
        assert draw.textlength(name, font=name_font) < tile - 48, name
        draw.text((x + 24, y + tile + 6), name, fill="#253c32", font=name_font)
        route = asset["productionRoute"].replace("_", " ")
        if len(route) > 52:
            route = route.replace(" then ", " + ")
        draw.text((x + 24, y + tile + 59), route, fill="#687060", font=note_font)
        board = verified_image(choice["turnaround"])
        assert board.width == board.height and board.width % 2 == 0
        half = board.width // 2
        labels = (
            ("overhead", "grazing", "detail", "context")
            if asset["category"] in ("surface", "water")
            else ("front", "back", "left", "top")
        )
        views = []
        for position, label in enumerate(labels):
            left, top = position % 2 * half, position // 2 * half
            path = output / (asset["id"] + "-" + label + ".png")
            board.crop((left, top, left + half, top + half)).save(path)
            views.append({"view": label, **receipt(path)})
        records.append({"id": asset["id"], **choice, "separateViews": views})
        figures = []
        for label, record in [
            ("Master design", choice["master"]),
            *[(v["view"], v) for v in views],
        ]:
            link = html.escape(os.path.relpath(ROOT / record["path"], output), quote=True)
            figures.append(
                f'<figure><a href="{link}"><img src="{link}"></a><figcaption>{html.escape(label)}</figcaption></figure>'
            )
        sections.append(
            f'<section id="{asset["id"]}"><h2>{html.escape(name)}</h2>'
            f"<p>{html.escape(asset['purpose'])}</p>"
            f"<p>Production route: {html.escape(route)}. Dimensions are design targets; mesh scale is unverified.</p>"
            '<div class="views">' + "".join(figures) + "</div></section>"
        )
    full = output / "all-assets.png"
    sheet.save(full)
    sheet.thumbnail((2000, 2800), Image.Resampling.LANCZOS)
    preview = output / "all-assets-preview.jpg"
    sheet.save(preview, quality=95, subsampling=0)
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sierra valley asset references</title><style>body{margin:0;padding:32px;background:#eeeae1;color:#253c32;font:16px system-ui}h1{font-size:42px}p{max-width:1000px;line-height:1.5}.views{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}figure{margin:0}img{width:100%;height:auto}figcaption{padding:8px}section{margin:48px 0}@media(max-width:900px){.views{grid-template-columns:repeat(2,minmax(0,1fr))}}</style><h1>Sierra search valley · Asset references</h1><p>Generated appearance studies for approval. Views share a reference design but are not reconstructed geometry or proven camera projections. Inspect seams, silhouettes and hidden-side consistency before reconstruction. Foliage, water and landscape surfaces need native Unreal authoring. No 3D conversion or Content placement has been performed.</p><p><a href="all-assets.png">Download the complete asset board</a></p>"""
    (output / "index.html").write_text(page + "".join(sections) + "</html>\n")
    report = {"assets": records, "overview": receipt(full), "preview": receipt(preview)}
    (output / "review.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "assets": len(records),
                "separateViews": sum(len(r["separateViews"]) for r in records),
                "overview": report["overview"],
            }
        )
    )


if __name__ == "__main__":
    main()
