"""Render the authored public landmark catalogue over the active heightfield."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

from mission_planner.environment import load_env
from world.cosys.landmarks import LANDMARKS_PATH
from world.cosys.terrain import COLUMNS, FIELD, ROWS, WORLD, heights
from world.unreal.valley import axes, catalogue, points

COLORS = {
    "trail": "#8a481b",
    "road": "#703c35",
    "creek": "#006cad",
    "pond": "#006cad",
    "bridge": "#722faa",
    "structure": "#722faa",
    "forest": "#216e3a",
    "ridge": "#47454d",
    "outcrop": "#47454d",
    "campsite": "#bd4b33",
    "parking": "#bd4b33",
    "command_post": "#bd4b33",
}


def plot(output):
    load_env()
    assert not output.exists(), f"Map destination already exists: {output}"
    landmarks = catalogue(json.loads(LANDMARKS_PATH.read_text()))
    elevation = (np.array(heights()).reshape(ROWS, COLUMNS) - FIELD["zeroSample"]) / FIELD[
        "samplesPerMetre"
    ]
    north, east = axes()
    figure = plt.figure(figsize=(19.2, 12), facecolor="#faf9f4")
    axis = figure.add_axes((0.055, 0.15, 0.68, 0.77))
    backdrop = axis.imshow(
        elevation.T,
        origin="lower",
        extent=(east[0], east[-1], north[0], north[-1]),
        cmap="gist_earth",
        alpha=0.53,
        vmin=float(elevation.min()),
        vmax=float(elevation.max()),
    )
    for number, item in enumerate(landmarks, 1):
        vertices = points(item)[:, ::-1]
        color = COLORS[item["kind"]]
        if item["geometry"]["type"] == "polygon":
            axis.add_patch(Polygon(vertices, facecolor=color, edgecolor=color, alpha=0.28))
        else:
            axis.plot(vertices[:, 0], vertices[:, 1], color=color, linewidth=2)
        center = vertices.mean(axis=0)
        offset = (12, 13) if number % 2 else (-16, -16)
        axis.annotate(
            str(number),
            center,
            xytext=offset,
            textcoords="offset points",
            color=color,
            weight="bold",
            fontsize=10,
            ha="center",
            va="center",
            bbox={"boxstyle": "circle,pad=0.25", "fc": "white", "ec": color, "alpha": 0.95},
            arrowprops={"arrowstyle": "-", "color": color, "lw": 0.7},
        )
        figure.text(
            0.755,
            0.875 - (number - 1) * 0.032,
            f"{number:2d}  {item['name']}",
            color=color,
            fontsize=11,
        )
    axis.set(xlabel="East / UE +Y (m)", ylabel="North / UE +X (m)")
    axis.grid(alpha=0.2)
    figure.colorbar(
        backdrop,
        ax=axis,
        orientation="horizontal",
        fraction=0.03,
        pad=0.08,
        label="Terrain elevation / UE +Z (m)",
    )
    figure.suptitle("Valley landmark authoring map", x=0.055, ha="left", fontsize=22)
    figure.text(0.755, 0.92, "PUBLIC LANDMARKS", weight="bold", fontsize=13)
    figure.text(
        0.055,
        0.045,
        f"{WORLD['environmentAssetVersion']} · Generated terrain and authored geometry; no art placed.\nCreek is the selected natural eastern reach. Heights are design envelopes, not mesh measurements.",
        fontsize=11,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=100)
    plt.close(figure)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    plot(parser.parse_args().output)
