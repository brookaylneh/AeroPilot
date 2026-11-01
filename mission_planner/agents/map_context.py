"""Compact public map context and sampled ground profiles in north/east metres."""

import math

from world.cosys.landmarks import directions
from world.cosys.terrain import WORLD, terrain_height


def simplify(points):
    if len(points) <= 2:
        return points
    a, b = points[0], points[-1]
    delta = [b[k] - a[k] for k in (0, 1)]
    length2 = sum(value * value for value in delta)
    distances = []
    for point in points[1:-1]:
        t = (
            min(1, max(0, sum((point[k] - a[k]) * delta[k] for k in (0, 1)) / length2))
            if length2
            else 0
        )
        distances.append(math.dist(point, [a[k] + t * delta[k] for k in (0, 1)]))
    if max(distances) <= 5:
        return [a, b]
    index = distances.index(max(distances)) + 1
    return [*simplify(points[: index + 1])[:-1], *simplify(points[index:])]


def compact_map(landmarks):
    assert landmarks
    launch = WORLD["port"]["centerUeCm"]
    pad = [launch["x"] / 100, launch["y"] / 100]
    places = []
    for landmark in landmarks:
        geometry = landmark["geometry"]
        points = [[p["x"] / 100, p["y"] / 100] for p in geometry["points"]]
        anchor = [(min(p[k] for p in points) + max(p[k] for p in points)) / 2 for k in (0, 1)]
        nearby, others = [], [p for p in landmarks if p["id"] != landmark["id"]]
        for _ in range(min(3, len(others))):
            nearest = directions(others, *anchor)["nearest"]
            nearby.append({"id": nearest["id"], "distance_m": round(nearest["distance_m"], 1)})
            others = [p for p in others if p["id"] != nearest["id"]]
        if geometry["type"] == "polygon":
            ring = [*points, points[0]]
            cuts = [0, len(points) // 3, 2 * len(points) // 3, len(points)]
            outline = [
                point
                for start, end in zip(cuts, cuts[1:])
                for point in simplify(ring[start : end + 1])[:-1]
            ]
        else:
            outline = simplify(points)
        assert len(outline) >= (3 if geometry["type"] == "polygon" else 1)
        places.append(
            {
                "id": landmark["id"],
                "name": landmark["name"],
                "aliases": landmark["aliases"],
                "kind": landmark["kind"],
                "height_m": landmark["heightM"],
                "anchor_ne_m": [round(v, 1) for v in anchor],
                "ground_ue_up_m": round(terrain_height(*anchor), 2),
                "geometry": {
                    "type": geometry["type"],
                    "points_ne_m": [[round(v, 1) for v in point] for point in outline],
                },
                "nearby": nearby,
                "from_launch": {
                    "distance_m": round(math.dist(anchor, pad), 1),
                    "bearing_deg": round(
                        math.degrees(math.atan2(anchor[1] - pad[1], anchor[0] - pad[0])) % 360,
                        1,
                    )
                    % 360,
                },
            }
        )
    return {
        "coordinates": "metres north/east; ground is Unreal metres up; bearings clockwise from north",
        "geometry_tolerance_m": 5.1,
        "anchor": "outline bounding-box centre; ground and launch distance/bearing refer to this point",
        "launch_pad_ne_m": pad,
        "places": places,
    }


def ground_height(north_m, east_m):
    assert all(type(v) in (int, float) and math.isfinite(v) for v in (north_m, east_m))
    return {
        "north_m": north_m,
        "east_m": east_m,
        "ground_ue_up_m": terrain_height(north_m, east_m),
    }


def ground_profile(points):
    assert isinstance(points, list) and len(points) >= 2
    samples, distance = [], 0.0
    for a, b in zip(points, points[1:]):
        assert set(a) == set(b) == {"north_m", "east_m"}
        length = math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"])
        assert math.isfinite(length) and length > 0, "Ground profile has a zero-length leg"
        count = math.ceil(length / 10)
        for index in range(0 if not samples else 1, count + 1):
            t = index / count
            point = {key: a[key] + t * (b[key] - a[key]) for key in a}
            samples.append({"distance_m": distance + t * length, **ground_height(**point)})
        distance += length
    return {"sample_spacing_at_most_m": 10, "distance_m": distance, "samples": samples}
