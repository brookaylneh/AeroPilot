"""Publish measured render variants and fixed-pose images without private scenario data."""

import argparse
import hashlib
import html
import json
import shutil
from pathlib import Path

from PIL import Image

from mission_planner.environment import load_env


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("variants", nargs="+")
    args = parser.parse_args()
    assert args.name.startswith("render-quality-") and args.name.replace("-", "").isalnum()
    record = json.loads(Path("mission_planner/measurements/render-quality.json").read_text())
    by_name = {entry["name"]: entry for entry in record["variants"]}
    variants = [by_name[name] for name in args.variants]
    assert all("screenshot_files" in entry for entry in variants), "Missing image hash manifest"
    output = Path(".local/dist") / args.name
    output.mkdir()
    rows, cards, artifacts = [], [], []
    for entry in variants:
        name = entry["name"]
        assert name.replace("-", "").isalnum(), name
        summary, profile = entry["summary"], entry["profile"]
        label = html.escape(name)
        gpu = summary["gpuMs"]
        rows.append(
            f"<tr><th>{label}</th><td>{profile['width']} × {profile['height']}</td>"
            f"<td>{gpu['mean']:.2f} / {gpu['p95']:.2f} / {gpu['p99']:.2f}</td>"
            f"<td>{summary['decodedFps']:.2f}</td><td>{summary['freezes']}</td>"
            f"<td>{summary['bitrateMbps']:.2f}</td><td>{summary['powerW']['mean']:.1f}</td>"
            f"<td>{summary['seconds']:.1f}</td></tr>"
        )
        cards.append(f"<h2>{label}</h2><p>{html.escape(entry['workload'])}</p><div class=poses>")
        for pose in ("person", "meadow", "port"):
            source = Path(entry["evidence"]).parent / f"{pose}.png"
            expected = next(
                item for item in entry["screenshot_files"] if item["path"] == str(source)
            )
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            assert digest == expected["sha256"], source
            original = output / f"{name}-{pose}.png"
            shutil.copy2(source, original)
            thumbnail = original.with_suffix(".jpg")
            with Image.open(source) as image:
                image.thumbnail((1280, 720))
                image.convert("RGB").save(thumbnail, quality=92)
            cards.append(
                f'<figure><a href="{original.name}"><img loading=lazy src="{thumbnail.name}" '
                f'alt="{label}: {pose}"></a><figcaption>{pose} · '
                f'<a href="{original.name}">Full resolution</a></figcaption></figure>'
            )
            artifacts.append({"source": str(source), "published": str(original), "sha256": digest})
        cards.append("</div>")
    document = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Render quality comparisons</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;margin:32px;background:#141920;color:#edf2f7}
a{color:#8acaff}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px;text-align:left;border-bottom:1px solid #48515d}
.poses{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}
figure{margin:0}img{width:100%;height:auto}h2{margin-top:40px}.table{overflow:auto}
</style><h1>Render quality comparisons</h1>
<p>Actual browser stream frames at fixed person, meadow and port poses. Open images at full
resolution to inspect detail. These are individual measured runs, not final acceptance.</p>
<p>GPU timings are mean / p95 / p99 in milliseconds. FPS is decoded browser video.
Power is the whole GPU, including other active workloads. Workload and duration are shown
for each variant; compare equivalent workloads. Screenshots contain no evaluator truth.</p>
<div class="table"><table><thead><tr><th>Variant</th><th>Output</th><th>GPU ms</th>
<th>FPS</th><th>Freezes</th><th>Mbps</th><th>Watts</th><th>Seconds</th></tr></thead><tbody>
"""
    document += "\n".join(rows) + "</tbody></table></div>\n" + "\n".join(cards) + "</html>\n"
    (output / "index.html").write_text(document)
    manifest = {"page": f"/{args.name}/index.html", "variants": args.variants, "images": artifacts}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(manifest["page"])


if __name__ == "__main__":
    main()
