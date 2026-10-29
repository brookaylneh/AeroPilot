"""Replace explicitly reviewed turnaround panels while preserving all other pixels."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import ImageChops

from mission_planner.environment import load_env
from world.unreal.review_asset_images import receipt, verified_image

ROOT = Path(__file__).resolve().parents[2]


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    output = ROOT / args.output
    assert output.resolve().is_relative_to((ROOT / ".local/world/valley").resolve())
    output.mkdir(parents=True)
    labels = ("front", "back", "left", "top")
    records = []
    for job in plan["jobs"]:
        base = verified_image(job["base"])
        revision = verified_image(job["revision"])
        assert base.size == revision.size and base.width == base.height and base.width % 2 == 0
        panels = job["replacePanels"]
        assert panels and len(set(panels)) == len(panels) and set(panels) <= set(labels)
        composed = base.copy()
        half = base.width // 2
        views = []
        for index, label in enumerate(labels):
            x, y = index % 2 * half, index // 2 * half
            box = (x, y, x + half, y + half)
            if label in panels:
                composed.paste(revision.crop(box), (x, y))
            else:
                assert ImageChops.difference(base.crop(box), composed.crop(box)).getbbox() is None
            path = output / (job["id"] + "-" + label + ".png")
            composed.crop(box).save(path)
            views.append({"view": label, **receipt(path)})
        path = output / (job["id"] + ".png")
        composed.save(path)
        records.append({**job, "composed": receipt(path), "separateViews": views})
    report = {
        "sourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "planSha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        "assets": records,
    }
    with (output / "composition.json").open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
