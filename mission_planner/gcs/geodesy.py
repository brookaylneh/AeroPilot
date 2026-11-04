"""Search-centred WGS84 tangent plane; north/east metres, geodetic degrees."""

import math
from pathlib import Path

from world.cosys.terrain import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M, NORTH_MIN_M, WORLD

ROOT = Path(__file__).resolve().parents[2]
NAMES = tuple(f"Drone{i}" for i in range(1, 5))
ORIGIN = {
    "name": "Tuolumne Meadows, Yosemite National Park",
    "latitude_deg": 37.876432,
    "longitude_deg": -119.393421,
    "altitude_msl_m": 2621.28,
}
LAUNCH_SOUTH_M = NORTH_MIN_M - 40.0
LAUNCH_HALF_WIDTH_M = 25.0
MIN_ALTITUDE_M = 10.0
MAX_ALTITUDE_M = 100.0
# Launch pads stand 5 m apart, so their takeoff and landing columns bound this minimum.
MIN_SEPARATION_M = 4.0
SEPARATION_TOLERANCE_M = 1e-12
MIN_SPEED_MPS = 0.5
MAX_SPEED_MPS = 15.0
RTL_ALTITUDE_M = 80.0
# Half the 5 m spacing between neighbouring home pads.
BASE_RADIUS_M = 2.5
MIN_MAP_RADIUS_M = 1.0
MAX_MAP_RADIUS_M = min(NORTH_MAX_M - NORTH_MIN_M, EAST_MAX_M - EAST_MIN_M) / 2
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
    assert set(WORLD["vehicles"]) == set(NAMES)
    origin = WORLD["playerStartUeCm"]
    result = {}
    for name in NAMES:
        vehicle = WORLD["vehicles"][name]
        spawn = vehicle["spawnUeCm"]
        # Unreal centimetres with +Z up; the NED origin is the PlayerStart at ORIGIN's altitude.
        north, east = (spawn["x"] - origin["x"]) / 100, (spawn["y"] - origin["y"]) / 100
        latitude, longitude = to_geodetic(north, east)
        back = to_local(latitude, longitude)
        assert math.dist((north, east), back) < 1e-6
        result[name] = {
            "north_m": north,
            "east_m": east,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_msl_m": ORIGIN["altitude_msl_m"] + spawn["z"] / 100,
            "heading_deg": vehicle["yawDegrees"],
        }
    return result


def fence_vertices():
    return [
        {"north_m": north, "east_m": east}
        for north, east in (
            (NORTH_MIN_M, EAST_MIN_M),
            (NORTH_MIN_M, -LAUNCH_HALF_WIDTH_M),
            (LAUNCH_SOUTH_M, -LAUNCH_HALF_WIDTH_M),
            (LAUNCH_SOUTH_M, LAUNCH_HALF_WIDTH_M),
            (NORTH_MIN_M, LAUNCH_HALF_WIDTH_M),
            (NORTH_MIN_M, EAST_MAX_M),
            (NORTH_MAX_M, EAST_MAX_M),
            (NORTH_MAX_M, EAST_MIN_M),
        )
    ]


def rtl_route(state):
    # Fleet startup verifies RTL_ALT_M, zero cone slope and zero minimum climb.
    altitude = state["altitude_above_home_m"]
    return_altitude = max(altitude, RTL_ALTITUDE_M)
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
            abs(cross) <= 1e-9 * math.hypot(bx - ax, by - ay)
            and min(ax, bx) - 1e-9 <= x <= max(ax, bx) + 1e-9
            and min(ay, by) - 1e-9 <= y <= max(ay, by) + 1e-9
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


def polygon_error(points):
    """Why the points are not a simple polygon with implicit closure, or None."""
    from world.cosys.landmarks import _intersects

    vertices = [(p["north_m"], p["east_m"]) for p in points]
    if len(vertices) < 3 or len(set(vertices)) != len(vertices):
        return "vertices must be unique; closure is implicit"
    edges = list(zip(vertices, [*vertices[1:], vertices[0]]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j > i + 1 and (i, j) != (0, len(edges) - 1) and _intersects(a, b, c, d):
                return "polygon self-intersects"
    if abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) <= 1e-6:
        return "polygon has no area"
    return None


def validate_polygon(points, label):
    error = polygon_error(points)
    assert error is None, f"{label}: {error}"
