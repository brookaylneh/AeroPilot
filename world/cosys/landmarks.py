"""Validated public landmarks and horizontal directions in mission north/east metres."""

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDMARKS_PATH = ROOT / "world/unreal/landmarks.json"
WORLD_PATH = ROOT / "world/cosys/world.json"
KINDS = (
    "trail",
    "road",
    "creek",
    "pond",
    "bridge",
    "structure",
    "forest",
    "ridge",
    "outcrop",
    "campsite",
    "parking",
    "command_post",
)
COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a, b, c, d):
    return (
        all(
            max(min(a[k], b[k]), min(c[k], d[k])) <= min(max(a[k], b[k]), max(c[k], d[k]))
            for k in (0, 1)
        )
        and _cross(a, b, c) * _cross(a, b, d) <= 0
        and _cross(c, d, a) * _cross(c, d, b) <= 0
    )


def validate(document, world):
    assert isinstance(document, dict) and isinstance(world, dict)
    assert set(document) == {"schemaVersion", "worldId", "coordinateSystem", "landmarks"}
    assert type(document["schemaVersion"]) is int and document["schemaVersion"] == 1
    assert isinstance(world["worldId"], str) and world["worldId"].strip()
    assert document["worldId"] == world["worldId"], "Landmark world differs from world.json"
    assert document["coordinateSystem"] == world["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
    assert (world["north"], world["east"]) == ("+X", "+Y")
    bounds = world["searchBoundsCm"]
    assert isinstance(bounds, dict) and set(bounds) == {"min", "max"}
    assert all(isinstance(bounds[end], dict) and set(bounds[end]) == set("xyz") for end in bounds)
    assert all(
        type(bounds[end][axis]) in (int, float) and math.isfinite(bounds[end][axis])
        for end in ("min", "max")
        for axis in "xyz"
    )
    assert all(bounds["min"][axis] < bounds["max"][axis] for axis in "xyz")
    landmarks = document["landmarks"]
    assert isinstance(landmarks, list) and landmarks, "Landmark catalogue is empty"
    ids = set()
    for landmark in landmarks:
        assert isinstance(landmark, dict)
        assert set(landmark) == {"id", "name", "aliases", "kind", "geometry", "heightM"}
        identifier = landmark["id"]
        assert isinstance(identifier, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier)
        assert identifier not in ids, "Duplicate landmark id"
        ids.add(identifier)
        assert isinstance(landmark["aliases"], list)
        names = [landmark["name"], *landmark["aliases"]]
        assert all(isinstance(name, str) and name and name == name.strip() for name in names)
        assert len({name.casefold() for name in names}) == len(names), "Duplicate landmark alias"
        assert landmark["kind"] in KINDS
        height = landmark["heightM"]
        assert type(height) in (int, float) and math.isfinite(height) and height >= 0, (
            "Landmark heightM must be finite nonnegative metres above terrain"
        )
        if landmark["kind"] in ("trail", "road", "creek", "pond"):
            assert height == 0, "Trail, road, creek and pond heightM must be zero"
        geometry = landmark["geometry"]
        assert isinstance(geometry, dict)
        assert set(geometry) == {"type", "points"}
        kind = geometry["type"]
        assert kind in ("point", "polyline", "polygon")
        if height > 0:
            assert kind == "polygon", "Raised landmarks require a polygon footprint"
        if landmark["kind"] in ("trail", "road", "creek", "ridge"):
            assert kind == "polyline", "Linear features require polyline geometry"
        vertices = geometry["points"]
        assert isinstance(vertices, list)
        assert (
            len(vertices) == 1
            if kind == "point"
            else len(vertices) >= (2 if kind == "polyline" else 3)
        )
        for point in vertices:
            assert isinstance(point, dict)
            assert set(point) == set("xyz")
            assert all(
                type(point[axis]) in (int, float)
                and math.isfinite(point[axis])
                and bounds["min"][axis] <= point[axis] <= bounds["max"][axis]
                for axis in "xyz"
            ), "Landmark coordinate outside world.json bounds or nonfinite"
        xy = [(point["x"], point["y"]) for point in vertices]
        assert all(a != b for a, b in zip(xy, xy[1:])), "Zero-length horizontal segment"
        if kind == "polygon":
            assert len(set(xy)) == len(xy), "Polygon vertices must be unique; closure is implicit"
            edges = list(zip(xy, xy[1:] + xy[:1]))
            assert sum(a[0] * b[1] - b[0] * a[1] for a, b in edges) != 0, "Zero-area polygon"
            for i, (a, b) in enumerate(edges):
                for j, (c, d) in enumerate(edges):
                    if j > i + 1 and (i, j) != (0, len(edges) - 1):
                        assert not _intersects(a, b, c, d), "Polygon self-intersects"
    return landmarks


def load(path=LANDMARKS_PATH, world_path=WORLD_PATH):
    return validate(json.loads(Path(path).read_text()), json.loads(Path(world_path).read_text()))


def find(landmarks, name):
    assert landmarks, "Place lookup requires a nonempty landmark catalogue"
    assert isinstance(name, str) and name.strip(), "Place query is empty"
    matches = [
        landmark
        for landmark in landmarks
        if name.casefold()
        in {value.casefold() for value in [landmark["name"], *landmark["aliases"]]}
    ]
    return {
        "matches": sorted(matches, key=lambda item: item["id"]),
        "known_names": []
        if matches
        else sorted({item["name"] for item in landmarks}, key=str.casefold),
    }


def _projection(point, a, b):
    delta = (b[0] - a[0], b[1] - a[1])
    t = sum((point[k] - a[k]) * delta[k] for k in (0, 1)) / sum(value**2 for value in delta)
    nearest = a if t <= 0 else b if t >= 1 else tuple(a[k] + t * delta[k] for k in (0, 1))
    return math.dist(point, nearest), nearest, t


def _nearest(landmark, point):
    geometry = landmark["geometry"]
    vertices = [(p["x"] / 100, p["y"] / 100) for p in geometry["points"]]
    if geometry["type"] == "point":
        return math.dist(point, vertices[0]), vertices[0], False
    edges = list(
        zip(vertices, vertices[1:] + (vertices[:1] if geometry["type"] == "polygon" else []))
    )
    distance, nearest, t = min(
        (_projection(point, a, b) for a, b in edges), key=lambda item: item[0]
    )
    if geometry["type"] == "polygon":
        inside = False
        for a, b in edges:
            if (a[1] > point[1]) != (b[1] > point[1]):
                inside ^= point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]
        if inside:
            return 0.0, point, False
    beyond = (
        geometry["type"] == "polyline"
        and vertices[0] != vertices[-1]
        and nearest in (vertices[0], vertices[-1])
        and (t < 0 or t > 1)
    )
    return distance, nearest, beyond


def _compass(north, east):
    if math.hypot(north, east) < 1e-9:
        return "at"
    bearing = math.degrees(math.atan2(east, north)) % 360
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def directions(landmarks, north_m, east_m):
    assert landmarks, "Directions require a nonempty landmark catalogue"
    assert all(type(value) in (int, float) and math.isfinite(value) for value in (north_m, east_m))
    point = north_m, east_m
    candidates = []
    for landmark in landmarks:
        distance, nearest, beyond = _nearest(landmark, point)
        assert math.isfinite(distance), "Direction distance is nonfinite"
        compass = _compass(north_m - nearest[0], east_m - nearest[1])
        item = {
            "id": landmark["id"],
            "name": landmark["name"],
            "kind": landmark["kind"],
            "distance_m": distance,
            "compass": compass,
            "point": {"north_m": nearest[0], "east_m": nearest[1]},
        }
        side = None
        if landmark["geometry"]["type"] == "polyline":
            side = "beyond endpoint" if beyond else "on" if compass == "at" else compass
        candidates.append((item, side))
    candidates.sort(key=lambda row: (row[0]["distance_m"], row[0]["id"]))
    nearest = candidates[0][0]
    line = next((dict(item, side=side) for item, side in candidates if side is not None), None)
    text = (
        f"At {nearest['name']}"
        if nearest["compass"] == "at"
        else f"{nearest['distance_m']:.1f} m {nearest['compass']} of {nearest['name']}"
    )
    if line is not None:
        relation = "bank" if line["kind"] == "creek" else "side"
        side = line["side"]
        suffix = (
            f"on {line['name']}"
            if side == "on"
            else f"beyond an endpoint of {line['name']}"
            if side == "beyond endpoint"
            else f"{side} {relation} of {line['name']}"
        )
        text += "; " + suffix
    return {"text": text, "nearest": nearest, "linear": line}
