"""Quoted place proposals and uniform arclength support for an approved location."""

import re
from copy import deepcopy

import numpy as np

from mission_planner.gcs.geodesy import point_in_polygon, segment_in_polygon
from mission_planner.search.subject import evidence_text


def proposal(landmarks, place_id, evidence, briefing, fence):
    assert landmarks is not None, "Landmark data unavailable: world/unreal/landmarks.json"
    matches = [place for place in landmarks if place["id"] == place_id]
    assert len(matches) == 1, "Unknown landmark id"
    place = matches[0]
    text = evidence_text(evidence, briefing)
    assert any(
        re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I)
        for name in [place["name"], *place["aliases"]]
    ), "Quoted briefing evidence must name this place or one of its aliases"
    geometry = place["geometry"]
    assert geometry["type"] in ("point", "polyline"), (
        "Last known place must be a point or line; mark a point for an area landmark"
    )
    points = [{"north_m": p["x"] / 100, "east_m": p["y"] / 100} for p in geometry["points"]]
    assert all(point_in_polygon(p, fence) for p in points) and all(
        segment_in_polygon(a, b, fence) for a, b in zip(points, points[1:])
    ), "Last known geometry outside read-back fence"
    vertices = np.array([[p["north_m"], p["east_m"]] for p in points])
    anchor = vertices[0]
    if geometry["type"] == "polyline":
        lengths = np.linalg.norm(np.diff(vertices, axis=0), axis=1)
        cumulative = np.r_[0, np.cumsum(lengths)]
        midpoint = cumulative[-1] / 2
        i = int(np.searchsorted(cumulative[1:], midpoint))
        anchor = vertices[i] + (vertices[i + 1] - vertices[i]) * (
            (midpoint - cumulative[i]) / lengths[i]
        )
    return {
        "north_m": float(anchor[0]),
        "east_m": float(anchor[1]),
        "source": "operator_approved_place",
        "evidence": deepcopy(evidence),
        "place_id": place["id"],
        "place_name": place["name"],
        "geometry": deepcopy(geometry),
    }


def location_support(location, cell_m):
    assert np.isfinite(cell_m) and cell_m > 0
    if "geometry" not in location or location["geometry"]["type"] == "point":
        return np.array([[location["north_m"], location["east_m"]]]), np.ones(1)
    geometry = location["geometry"]
    assert geometry["type"] == "polyline"
    vertices = np.array([[p["x"], p["y"]] for p in geometry["points"]]) / 100
    lengths = np.linalg.norm(np.diff(vertices, axis=0), axis=1)
    assert np.isfinite(vertices).all() and (lengths > 0).all()
    positions, weights = [], []
    for a, b, length in zip(vertices, vertices[1:], lengths):
        count = int(np.ceil(length / cell_m))
        fractions = (np.arange(count) + 0.5) / count
        positions.extend(a + fractions[:, None] * (b - a))
        weights.extend(np.full(count, length / count / lengths.sum()))
    assert abs(sum(weights) - 1) < 1e-12
    return np.array(positions), np.array(weights)
