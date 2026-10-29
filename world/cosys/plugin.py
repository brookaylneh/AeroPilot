"""Verify the pinned plugin carries every required source patch before building."""

import subprocess
from pathlib import Path

from mission_planner.environment import load_env

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ("airsim-ue58.patch", "airsim-geodesy.patch", "airsim-sensor.patch")


def verify_plugin():
    for name in PATCHES:
        subprocess.run(
            [
                "patch",
                "--force",
                "--reverse",
                "--dry-run",
                "--fuzz=0",
                "-p1",
                "-i",
                str(ROOT / "world/cosys" / name),
            ],
            cwd=ROOT / "world/unreal/ScenarioStudio/Plugins/AirSim",
            check=True,
        )


if __name__ == "__main__":
    load_env()
    verify_plugin()
