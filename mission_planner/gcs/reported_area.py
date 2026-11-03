"""Resolve a public witness report's landmark-relative bounds without authoring data."""

import math
import re

from mission_planner.gcs.geodesy import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M, NORTH_MIN_M


def resolve(brief, landmarks):
    assert isinstance(brief, str) and 0 < len(brief) <= 4000, "Invalid public mission brief"
    sections = [p for p in brief.split("\n\n") if p.startswith("Reported search area\n")]
    assert len(sections) == 1, "The brief must contain one Reported search area paragraph"
    source = sections[0].split("\n", 1)[1]
    number = r"(\d+(?:\.\d+)?)"
    match = re.fullmatch(
        rf"From {number} metres (north|south) to {number} metres (north|south), "
        rf"and from {number} metres (east|west) to {number} metres (east|west), "
        r"measured from the centre of (.+)\.",
        source,
    )
    assert match is not None, (
        "The reported area needs two north/south bounds, two east/west bounds and a named "
        "landmark centre. Restore or edit that paragraph before planning."
    )
    values = match.groups()
    offsets = [
        float(values[i]) * (-1 if values[i + 1] in ("south", "west") else 1) for i in range(0, 8, 2)
    ]
    assert all(math.isfinite(v) for v in offsets), "Reported distances must be finite metres"
    north0, north1, east0, east1 = offsets
    assert north0 < north1 and east0 < east1, "Reported area bounds are reversed or empty"
    matches = [place for place in landmarks if place["name"].casefold() == values[8].casefold()]
    assert len(matches) == 1, "Reported area must name one public landmark exactly"
    place = matches[0]
    assert place["kind"] in ("bridge", "structure") or place["name"].endswith(" Signpost"), (
        "Reported area requires a bridge, structure or signpost with an identifiable centre"
    )
    points = place["geometry"]["points"]
    assert place["geometry"]["type"] == "polygon" and len(points) >= 3
    north = sum(p["x"] for p in points) / len(points) / 100
    east = sum(p["y"] for p in points) / len(points) / 100
    polygon = [
        {"north_m": north + n, "east_m": east + e}
        for n, e in ((north0, east0), (north1, east0), (north1, east1), (north0, east1))
    ]
    assert all(
        NORTH_MIN_M <= p["north_m"] <= NORTH_MAX_M and EAST_MIN_M <= p["east_m"] <= EAST_MAX_M
        for p in polygon
    ), "The reported area crosses the searchable map boundary"
    return {
        "source": source,
        "landmark_id": place["id"],
        "anchor": {"north_m": north, "east_m": east},
        "offsets_m": {"north": [north0, north1], "east": [east0, east1]},
        "polygon": polygon,
        "area_m2": (north1 - north0) * (east1 - east0),
    }
