"""Search-centred WGS84 tangent plane; north/east metres, geodetic degrees."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = tuple(f"Drone{i}" for i in range(1, 5))
ORIGIN = {
    "name": "Tuolumne Meadows, Yosemite National Park",
    "latitude_deg": 37.876432,
    "longitude_deg": -119.393421,
    "altitude_msl_m": 2621.28,
}
BOUND_M = 804.672
MIN_ALTITUDE_M = 10.0
MAX_ALTITUDE_M = 100.0
MIN_SEPARATION_M = 3.5
_LAT = math.radians(ORIGIN["latitude_deg"])
_E2 = 6.6943799901413165e-3
_W = math.sqrt(1 - _E2 * math.sin(_LAT) ** 2)
_N = 6378137.0 / _W
_M = 6378137.0 * (1 - _E2) / _W**3


def to_geodetic(north_m, east_m):
    assert all(math.isfinite(v) and abs(v) <= 10000 for v in (north_m, east_m))
    return (
        ORIGIN["latitude_deg"] + math.degrees(north_m / _M),
        ORIGIN["longitude_deg"] + math.degrees(east_m / (_N * math.cos(_LAT))),
    )


def to_local(latitude_deg, longitude_deg):
    assert -90 <= latitude_deg <= 90 and -180 <= longitude_deg <= 180
    return (
        math.radians(latitude_deg - ORIGIN["latitude_deg"]) * _M,
        math.radians(longitude_deg - ORIGIN["longitude_deg"]) * _N * math.cos(_LAT),
    )


def homes():
    settings = json.loads((ROOT / "world/cosys/settings.json").read_text())
    assert set(settings["Vehicles"]) == set(NAMES)
    result = {}
    for name in NAMES:
        vehicle = settings["Vehicles"][name]
        # AirSim settings X/Y/Z are metres in NED; world PlayerStart is +40 m up.
        north, east = vehicle["X"], vehicle["Y"]
        latitude, longitude = to_geodetic(north, east)
        back = to_local(latitude, longitude)
        assert math.dist((north, east), back) < 1e-6
        result[name] = {
            "north_m": north,
            "east_m": east,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_msl_m": ORIGIN["altitude_msl_m"] + 40 - vehicle["Z"],
            "heading_deg": vehicle["Yaw"],
        }
    return result


def fence_vertices():
    return [
        {"north_m": north, "east_m": east}
        for north, east in (
            (-BOUND_M, -BOUND_M),
            (-BOUND_M, -25.0),
            (-BOUND_M - 40.0, -25.0),
            (-BOUND_M - 40.0, 25.0),
            (-BOUND_M, 25.0),
            (-BOUND_M, BOUND_M),
            (BOUND_M, BOUND_M),
            (BOUND_M, -BOUND_M),
        )
    ]


def rtl_route(state):
    # Fleet startup verifies the pinned RTL/fence defaults used by this nominal path.
    altitude = state["altitude_above_home_m"]
    distance = math.hypot(
        state["north_m"] - state["home"]["north_m"],
        state["east_m"] - state["home"]["east_m"],
    )
    return_altitude = max(altitude, min(15, max(altitude, 0.3, distance * 3)))
    return [
        state,
        {**state, "altitude_above_home_m": return_altitude},
        {**state["home"], "altitude_above_home_m": return_altitude},
        {**state["home"], "altitude_above_home_m": 0},
    ]


def segment_distance(a, b, c, d):
    """Exact minimum between closed 3D segments, including points and parallel lines."""

    def subtract(x, y):
        return tuple(i - j for i, j in zip(x, y))

    def dot(x, y):
        return sum(i * j for i, j in zip(x, y))

    def clamp(value):
        return min(1, max(0, value))

    u, v, w = subtract(b, a), subtract(d, c), subtract(a, c)
    uu, vv, uv = dot(u, u), dot(v, v), dot(u, v)
    uw, vw = dot(u, w), dot(v, w)
    candidates = []
    if uu == 0 and vv == 0:
        return math.dist(a, c)
    if uu == 0:
        candidates.append((0, clamp(vw / vv)))
    elif vv == 0:
        candidates.append((clamp(-uw / uu), 0))
    else:
        candidates.extend(
            (
                (0, clamp(vw / vv)),
                (1, clamp((vw + uv) / vv)),
                (clamp(-uw / uu), 0),
                (clamp((uv - uw) / uu), 1),
            )
        )
        denominator = uu * vv - uv * uv
        if denominator > 1e-12 * uu * vv:
            s, t = (uv * vw - vv * uw) / denominator, (uu * vw - uv * uw) / denominator
            if 0 <= s <= 1 and 0 <= t <= 1:
                candidates.append((s, t))
    return min(
        math.sqrt(sum((w[i] + s * u[i] - t * v[i]) ** 2 for i in range(3))) for s, t in candidates
    )


def point_in_polygon(point, polygon):
    """Closed inclusion polygon in local north/east metres, including its boundary."""
    assert len(polygon) >= 3
    x, y = point["north_m"], point["east_m"]
    included = False
    for a, b in zip(polygon, [*polygon[1:], polygon[0]]):
        ax, ay, bx, by = a["north_m"], a["east_m"], b["north_m"], b["east_m"]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (
            abs(cross) < 1e-8
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
        ):
            return True
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            included = not included
    return included


def segment_in_polygon(a, b, polygon):
    if not point_in_polygon(a, polygon) or not point_in_polygon(b, polygon):
        return False
    ax, ay = a["north_m"], a["east_m"]
    dx, dy = b["north_m"] - ax, b["east_m"] - ay
    cuts = [0.0, 1.0]
    for c, d in zip(polygon, [*polygon[1:], polygon[0]]):
        ex, ey = d["north_m"] - c["north_m"], d["east_m"] - c["east_m"]
        cx, cy = c["north_m"] - ax, c["east_m"] - ay
        denominator = dx * ey - dy * ex
        if abs(denominator) > 1e-12:
            t, u = (cx * ey - cy * ex) / denominator, (cx * dy - cy * dx) / denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                cuts.append(t)
    cuts.sort()
    return all(
        point_in_polygon(
            {"north_m": ax + dx * (u + v) / 2, "east_m": ay + dy * (u + v) / 2}, polygon
        )
        for u, v in zip(cuts, cuts[1:])
    )
