#!/usr/bin/env python3
"""Stage the exact Cosys release. Does not install Unreal or change host drivers."""

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from mission_planner.environment import load_env

load_env()
parser = argparse.ArgumentParser()
parser.add_argument(
    "--runtime", action="store_true", help="Also download the packaged Blocks compatibility test"
)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
vendor = root / ".local/vendor"
vendor.mkdir(parents=True, exist_ok=True)
lock = json.loads((root / "world/unreal/dependencies.lock.json").read_text())
for entry in lock["files"]:
    if "packaged" in entry["name"] and not args.runtime:
        continue
    target = vendor / entry["name"]
    if not target.exists():
        print(f"Downloading {entry['name']}", flush=True)
        with urllib.request.urlopen(entry["url"], timeout=30) as response:
            with target.open("xb") as output:
                shutil.copyfileobj(response, output)
    with target.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise SystemExit(f"Checksum mismatch: {target}")
    destination = (
        root / "world/unreal/ScenarioStudio/Plugins"
        if entry["name"].startswith("AirSim")
        else vendor / ("blocks-runtime" if "packaged" in entry["name"] else "blocks-editor")
    )
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            if not (destination / name).resolve().is_relative_to(destination.resolve()):
                raise SystemExit("Archive contains an unsafe path")
        archive.extractall(destination)
    if entry["name"].startswith("AirSim"):
        for patch in ("airsim-ue58.patch", "airsim-geodesy.patch", "airsim-sensor.patch"):
            subprocess.run(
                [
                    "patch",
                    "--batch",
                    "--fuzz=0",
                    "-p1",
                    "-i",
                    str(root / "world/cosys" / patch),
                ],
                cwd=destination / "AirSim",
                check=True,
            )
    if "editor_project" in entry["name"]:
        shutil.copytree(
            destination / "Blocks/Content",
            root / "world/unreal/ScenarioStudio/Content",
            dirs_exist_ok=True,
        )
print("Cosys staged. A successful download is not a plugin launch or packaging test.")
