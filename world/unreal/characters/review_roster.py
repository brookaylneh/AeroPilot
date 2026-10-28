"""Publish complete roster captures as a portrait grid and individual seven-view galleries."""

import argparse
import hashlib
import html
import json
from pathlib import Path

from mission_planner.environment import load_env
from world.unreal.characters.review_metahuman import HERE, ROOT, publish


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    evidence = args.manifest.resolve().parent
    roster = json.loads((HERE / "roster.json").read_text())["characters"]
    template = (HERE / "metahuman-review.html").read_text()
    destination = ROOT / ".local/dist" / args.name
    assert args.name.startswith("character-") and args.name.replace("-", "").isalnum()
    assert not destination.exists(), destination
    cards = []
    records = []
    for person in roster:
        selected = manifest["characters"][person["id"]]
        output = ROOT / selected["evidence"]
        capture = json.loads((output / "render/result.json").read_text())
        assert capture["console_variables_verified"]
        assert len(capture["images"]) == 7
        images = []
        for value in capture["images"]:
            path = Path(value)
            images.append(
                {
                    "name": path.stem,
                    "path": str(path.relative_to(ROOT)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "caption": f"{person['name']} · {path.stem.replace('-', ' ')} · 4096 × 4096",
                }
            )
        selection = {
            "title": f"{person['name']} · target age {person['age']}",
            "assessment": selected["assessment"]
            + " "
            + (
                "Epic preset, groom and Cinematic assembly. Age and body appearance need visual "
                "approval. The T-shirt and shorts are the review outfit; trousers and shoes "
                "are not fitted yet. Animation and in-world performance are not accepted here."
            ),
            "images": images,
        }
        with (output / "selection.json").open("x") as stream:
            json.dump(selection, stream, indent=2)
        page_name = args.name + "-" + person["id"]
        publish(selection, page_name)
        caption = html.escape(f"{person['name']} · target age {person['age']} · {person['group']}")
        for view in ("face", "front"):
            cards.append(
                f'<figure><a href="/{page_name}/{view}.png">'
                f'<img src="/{page_name}/{view}.jpg" alt="{caption} · {view}"></a>'
                f"<figcaption>{caption}<br>{html.escape(selected['assessment'])}<br>"
                f'<a href="/{page_name}/index.html">'
                "View all seven angles</a></figcaption></figure>"
            )
        records.append(
            {"id": person["id"], "page": "/" + page_name + "/index.html", "images": images}
        )
    assessment = (
        "Twelve MetaHuman appearance studies with Epic hair and skin. Ages are authoring targets, "
        "not validated visual ages; review the children especially carefully. "
        "The review outfit is a T-shirt and shorts. Open each character for all seven views."
    )
    document = template.replace("<!-- TITLE -->", "Character roster · appearance review")
    document = document.replace("<!-- ASSESSMENT -->", assessment)
    document = document.replace("<!-- IMAGES -->", "\n".join(cards))
    destination.mkdir()
    (destination / "index.html").write_text(document)
    with (evidence / "gallery.json").open("x") as stream:
        json.dump(
            {"page": "/" + args.name + "/index.html", "characters": records}, stream, indent=2
        )
    print(destination / "index.html")


if __name__ == "__main__":
    main()
