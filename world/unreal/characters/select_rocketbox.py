"""Assemble a Rocketbox review from explicitly selected, completed captures."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[3]
PROFILE_FIELDS = ["renderer_cvars", "exposure_ev100", "white_balance_kelvin"]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    output = args.output.resolve()
    assert output.is_relative_to(ROOT / ".local")
    output.mkdir(parents=True, exist_ok=False)
    (output / "source").mkdir()
    (output / "render").mkdir()
    characters, views, subjects, profiles, commits, licenses, copies = {}, [], [], [], [], [], []

    def copy(source, destination):
        assert not destination.exists(), destination
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
        copies.append(
            {
                "source": str(source.relative_to(ROOT)),
                "target": str(destination.relative_to(ROOT)),
                "sha256": digest,
                "bytes": destination.stat().st_size,
            }
        )

    for source in selection["sources"]:
        manifest = json.loads((ROOT / source["manifest"]).read_text())
        render_dir = ROOT / source["render"]
        render = json.loads((render_dir / "result.json").read_text())
        assert len(render["images"]) == len(render["views"]) == 5 * len(manifest["characters"])
        profiles.append({key: render[key] for key in PROFILE_FIELDS})
        commits.append(manifest["commit"])
        assert manifest["license"] == "MIT"
        vendor = ROOT / manifest["vendor"]
        licenses.append((vendor / "LICENSE.md").read_bytes())
        by_id = {item["id"]: item for item in manifest["characters"]}
        for identifier in source["ids"]:
            assert identifier not in characters
            character = dict(by_id[identifier])
            copy(vendor / character["preview"], output / "source" / (identifier + ".png"))
            character["preview"] = identifier + ".png"
            characters[identifier] = character
            selected_views = [v for v in render["views"] if v["name"].startswith(identifier + "-")]
            assert len(selected_views) == 5
            for view in selected_views:
                path = render_dir / (view["name"] + ".png")
                assert str(path.resolve()) in render["images"], path
                copy(path, output / "render" / path.name)
            views.extend(selected_views)
            person = [item for item in render["subjects"] if item["id"] == identifier]
            assert len(person) == 1
            subjects.extend(person)
    assert profiles and all(profile == profiles[0] for profile in profiles)
    assert len(set(commits)) == 1 and all(value == licenses[0] for value in licenses)
    assert len(selection["order"]) == len(set(selection["order"])) == len(characters)
    assert set(selection["order"]) == set(characters)
    for identifier, assessment in selection["assessments"].items():
        characters[identifier]["assessment"] = assessment
    (output / "source/LICENSE.md").write_bytes(licenses[0])
    manifest = {
        "commit": commits[0],
        "vendor": str((output / "source").relative_to(ROOT)),
        "review": selection["review"],
        "characters": [characters[identifier] for identifier in selection["order"]],
    }
    report = {
        **profiles[0],
        "views": views,
        "subjects": subjects,
        "images": [str(output / "render" / (view["name"] + ".png")) for view in views],
        "selection": str(args.selection.resolve().relative_to(ROOT)),
    }
    for path, value in [
        ("review-manifest.json", manifest),
        ("render/result.json", report),
        ("copies.json", copies),
    ]:
        (output / path).write_text(json.dumps(value, indent=2) + "\n")
    print(
        json.dumps({"characters": len(characters), "captures": len(views), "copies": len(copies)})
    )


if __name__ == "__main__":
    main()
