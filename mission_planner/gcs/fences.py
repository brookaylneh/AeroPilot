"""One fleet boundary: polygon encoding, editing checks and continuous flight guards."""

import math
from functools import lru_cache

from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.gcs.geodesy import (
    ORIGIN,
    RTL_ALTITUDE_M,
    fence_vertices,
    point_in_polygon,
    segment_distance,
    segment_in_polygon,
    to_geodetic,
    to_local,
    validate_polygon,
)
from world.cosys.terrain import FIELD, heights, launch_triangles

MAX_VERTICES = 70
BOUNDARY_CLEARANCE_M = 10.0


def fence_items(fences):
    items = []
    for command, polygons in (
        (MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION, [fences["inclusion"]]),
        (MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION, fences["exclusions"]),
    ):
        for polygon in polygons:
            for point in polygon:
                lat, lon = to_geodetic(point["north_m"], point["east_m"])
                items.append(
                    {
                        "frame": MAV.MAV_FRAME_GLOBAL,
                        "command": command,
                        "param1": len(polygon),
                        "param2": 0,
                        "param3": 0,
                        "param4": 0,
                        "x": round(lat * 1e7),
                        "y": round(lon * 1e7),
                        "z": 0,
                    }
                )
    return items


def read_fences(items):
    assert 3 <= len(items) <= MAX_VERTICES, "Fence readback needs 3–70 vertices"
    result = {"inclusion": [], "exclusions": []}
    index = 0
    while index < len(items):
        item = items[index]
        count = int(item["param1"])
        assert count == item["param1"] and 3 <= count <= len(items) - index, (
            "Fence readback has an invalid polygon vertex count"
        )
        command = item["command"]
        assert command in (
            MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION,
            MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION,
        ), "Fence readback contains an unsupported command"
        group = items[index : index + count]
        assert all(p["command"] == command and p["param1"] == count for p in group), (
            "Fence readback mixes polygon vertices"
        )
        polygon = [
            dict(zip(("north_m", "east_m"), to_local(p["x"] / 1e7, p["y"] / 1e7))) for p in group
        ]
        if command == MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION:
            assert not result["inclusion"], "Exactly one inclusion fence is supported"
            result["inclusion"] = polygon
        else:
            result["exclusions"].append(polygon)
        index += count
    assert result["inclusion"], "Fence readback has no inclusion polygon"
    return result


def default_fences():
    return {
        "version": 0,
        "names": {"inclusion": "Inclusion", "exclusions": []},
        **read_fences(fence_items({"inclusion": fence_vertices(), "exclusions": []})),
    }


def validate_fences(fences, homes):
    assert isinstance(fences, dict) and set(fences) == {
        "version",
        "names",
        "inclusion",
        "exclusions",
    }, "Expected fence version, names, inclusion polygon and exclusion polygons"
    assert type(fences["version"]) is int and fences["version"] >= 0, "Invalid fence version"
    assert isinstance(fences["exclusions"], list), "Exclusions must be a list of polygons"
    names = fences["names"]
    assert isinstance(names, dict) and set(names) == {"inclusion", "exclusions"}, (
        "Expected inclusion and exclusion names"
    )
    assert isinstance(names["exclusions"], list) and len(names["exclusions"]) == len(
        fences["exclusions"]
    ), "Each exclusion polygon needs a name"
    assert all(
        isinstance(name, str) and 1 <= len(name) <= 60 and name == name.strip()
        for name in [names["inclusion"], *names["exclusions"]]
    ), "Fence names must be 1–60 characters without surrounding whitespace"
    polygons = [fences["inclusion"], *fences["exclusions"]]
    assert all(isinstance(polygon, list) for polygon in polygons), "Expected polygon vertices"
    assert sum(map(len, polygons)) <= MAX_VERTICES, "Fences allow at most 70 vertices in total"
    for index, polygon in enumerate(polygons):
        label = "Inclusion fence" if index == 0 else f"Exclusion {index}"
        for point in polygon:
            assert isinstance(point, dict) and set(point) == {"north_m", "east_m"}, (
                f"{label}: each vertex needs north_m and east_m"
            )
            assert all(type(v) in (int, float) and math.isfinite(v) for v in point.values()), (
                f"{label}: coordinates must be finite metres"
            )
        validate_polygon(polygon, label)
        for a, b, c in zip(polygon, [*polygon[1:], polygon[0]], [*polygon[2:], *polygon[:2]]):
            assert (b["north_m"] - a["north_m"]) * (c["east_m"] - b["east_m"]) != (
                b["east_m"] - a["east_m"]
            ) * (c["north_m"] - b["north_m"]), (
                f"{label}: move or remove a corner on a straight edge"
            )
    encoded = read_fences(fence_items(fences))
    for index, polygon in enumerate([encoded["inclusion"], *encoded["exclusions"]]):
        validate_polygon(polygon, f"Encoded fence polygon {index + 1}")
        label = "Inclusion fence" if index == 0 else f"Exclusion {index}"
        limit = default_fences()["inclusion"] if index == 0 else encoded["inclusion"]
        assert all(
            segment_in_polygon(a, b, limit) for a, b in zip(polygon, [*polygon[1:], polygon[0]])
        ), (
            f"{label}: polygon must remain inside the {'world boundary' if index == 0 else 'inclusion fence'}"
        )
    for name, point in homes.items():
        validate_fence_segment(point, point, encoded, name + " home/launch position")
    return {"version": fences["version"], "names": names, **encoded}


def validate_fence_segment(a, b, fences, label, clearance=BOUNDARY_CLEARANCE_M):
    assert all(math.isfinite(p[k]) for p in (a, b) for k in ("north_m", "east_m")), (
        label + ": flight coordinates must be finite metres"
    )
    assert segment_in_polygon(a, b, fences["inclusion"]), label + ": exits the inclusion fence"
    start, end = ((p["north_m"], p["east_m"], 0) for p in (a, b))
    for index, polygon in enumerate([fences["inclusion"], *fences["exclusions"]]):
        name = "inclusion fence" if index == 0 else f"Exclusion {index}"
        distance = min(
            segment_distance(
                start, end, (c["north_m"], c["east_m"], 0), (d["north_m"], d["east_m"], 0)
            )
            for c, d in zip(polygon, [*polygon[1:], polygon[0]])
        )
        if index:
            assert (
                not point_in_polygon(a, polygon)
                and not point_in_polygon(b, polygon)
                and distance > 1e-9
            ), f"{label}: enters {name}; add intermediate fly points around this no-fly zone"
        assert distance + 1e-9 >= clearance, (
            f"{label}: {distance:.3f} m from {name}; requires {clearance:g} m boundary clearance"
        )


@lru_cache(maxsize=1)
def terrain_ceiling():
    return max(
        (max(heights()) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"],
        max(p[2] for triangle in launch_triangles() for p in triangle),
    )


def rtl_envelope(home, landmarks):
    ground = terrain_ceiling()
    obstacle = max([0, *(p["heightM"] for p in landmarks)])
    return {
        "altitude_above_home_m": RTL_ALTITUDE_M,
        "terrain_max_m": ground,
        "obstacle_max_m": obstacle,
        "clearance_m": home["altitude_msl_m"]
        - ORIGIN["altitude_msl_m"]
        + RTL_ALTITUDE_M
        - ground
        - obstacle,
    }
