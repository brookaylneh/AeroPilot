"""Deterministic valley terrain from the accepted meadow and public landmarks."""

import argparse
import hashlib
import heapq
import importlib
import json
import platform
import time
from collections import deque
from importlib.metadata import version
from itertools import combinations
from pathlib import Path

import numpy as np
from matplotlib.path import Path as PolygonPath
from PIL import Image
from scipy.ndimage import (
    distance_transform_edt,
    gaussian_filter,
    gaussian_filter1d,
    map_coordinates,
)

from mission_planner.environment import load_env
from world.cosys.landmarks import LANDMARKS_PATH, _intersects, _nearest, validate
from world.cosys.terrain import BOUNDS, COLUMNS, FIELD, ROOT, ROWS, WORLD

NEIGHBORS = tuple((r, c) for r in (-1, 0, 1) for c in (-1, 0, 1) if r or c)
BASELINE_SHA256 = "ef7cf15ea67088cff77cda0f24cb41bae61f2fbd4c1ebec8a36199e775e6f63f"
DRAINAGE_STRIDE = 8
SOUTH_BUFFER_M = 32
CHANNEL_DEPTH_M = 0.65
CHANNEL_GRADE = 0.0005
CHANNEL_BANK_M = 40
CHANNEL = {"bedM": 2.4, "bankM": 0.7, "bankDegrees": 35, "swaleDegrees": 12, "waterM": 0.25}
CREEK_HEAD_M = (76.64, 440.65)
NATURAL_REACH_M = 12
GRADE_BANK_M = {"trail": 7, "road": 15}
PAD_BLEND_M = 15
# Nadir footprints require corner rays (38.8 degrees off nadir) to descend faster than the
# steepest adjacent sample pair along each axis, at any yaw; that fails above 41.4 degrees.
MAX_SLOPE_DEGREES = 37
# Shaping limit, leaving the 1/128 m quantization within the cap.
SHAPED_SLOPE_DEGREES = 36
# ScenarioManager::ValidateGround walking limit: region MaximumSlopeDegrees, 18 in the catalogue.
TRAIL_SLOPE_DEGREES = 18
# USFS Low-Volume Roads BMP Field Guide ch. 4: grades of 12% or less.
ROAD_GRADE = 0.12
MEADOW_M = ((-650, 0), (-450, 550))
# Hummocks and swales (gaussian sigma m, standard deviation m), feathered outside the meadow.
MEADOW_RELIEF = ((3, 0.15), (15, 0.25))
# Per-bearing tuples are evenly spaced clockwise from north.
DOME = {
    "id": "granite-dome",
    "summitM": (600, 300),
    "capM": 3,
    "capSoftM": 1,
    "heightM": 49,
    "exponents": (1.5, 2.4, 2.6, 2.4),
    "toeExponent": 1.4,
    "leeBearing": 195,
    "leeDegrees": 27,
    "leeBaseM": 95,
    "crestM": 8,
    "ledgeM": 7,
    "pluckM": 8,
    "sheetM": 1.3,
    "joints": ((35, 34), (125, 47)),
    "jointM": 0.6,
    "jointWidthM": 1.4,
    "apronM": 9,
    "apronOutM": 40,
    "apronHalfM": 55,
    "marginM": 60,
}
SLAB_M = 1.5
# Underwater the shore slope approaches the depth. The floor extends beyond the water by bearing;
# outside it the rim rises to a lip held for the bench width, then merges into the terrain over
# the merge width. Walls cut from the floor edge and slabs terrace the shore. A talus fan falls
# from the floor point farthest from the water down to it; the talus lies on the floor (id, rise m).
BASINS = {
    "granite-lake": {
        "surfaceM": 15.0,
        "depthM": (11, 10, 6, 2.5, 1.5, 2, 4, 9),
        "shoreDegrees": (34, 30, 14, 6, 4, 5, 10, 28),
        "floorM": (0,),
        "riseDegrees": (32, 26, 12, 8, 3, 4, 8, 28),
        "lipM": (4, 3.5, 1.5, 2, 0.6, 0.8, 1.2, 3.5),
        "benchM": (10, 10, 20, 4, 25, 20, 15, 10),
        "mergeM": (35, 35, 30, 25, 45, 45, 40, 35),
        "wallDegrees": (SHAPED_SLOPE_DEGREES,),
        "slabs": (0.7, 0.5, 0, 0, 0, 0, 0, 0.5),
        "talus": None,
    },
    "lookout-tarn": {
        "surfaceM": 16.0,
        "depthM": (4.5, 6.5, 4.5, 2.5),
        "shoreDegrees": (30, 32, 30, 12),
        "floorM": (10, 85, 90, 20, 8, 0, 0, 0),
        "riseDegrees": (6, 6, 6, 8),
        "lipM": (1.5,),
        "benchM": (12, 12, 12, 20),
        "mergeM": (20,),
        "wallDegrees": (SHAPED_SLOPE_DEGREES,) * 3 + (8,),
        "slabs": (0.5, 0.5, 0.5, 0),
        "talus": ("lookout-talus", 7.0),
    },
    "beaver-pond": {
        "surfaceM": 5.3,
        "depthM": (1.2,),
        "shoreDegrees": (8,),
        "floorM": (0,),
        "riseDegrees": (4,),
        "lipM": (0.35,),
        "benchM": (3,),
        "mergeM": (10,),
        "wallDegrees": (SHAPED_SLOPE_DEGREES,),
        "slabs": (0,),
        "talus": None,
    },
}
BASIN_MARGIN_M = 170
MASKS = ("cobble-bed", "granite", "gravel-road", "trail-tread", "needle-floor", "wet-meadow")
LODGEPOLES = ("west-lodgepoles", "north-lodgepoles", "east-lodgepoles")
GRANITE = ("granite-dome", "granite-outcrop", "lookout-talus")


def drainage(elevation, spacing_m):
    """Priority-flood depressions, D8 steepest descent, then upstream sample counts."""
    assert elevation.ndim == 2 and np.isfinite(elevation).all() and spacing_m > 0
    rows, cols = elevation.shape
    filled = elevation.copy()
    visited = np.zeros(elevation.shape, dtype=bool)
    receiver = np.full(elevation.shape, -1, dtype=np.int64)
    heap = []
    for r in range(rows):
        for c in range(cols):
            if r in (0, rows - 1) or c in (0, cols - 1):
                visited[r, c] = True
                heapq.heappush(heap, (filled[r, c], r * cols + c))
    while heap:
        level, index = heapq.heappop(heap)
        r, c = divmod(index, cols)
        for dr, dc in NEIGHBORS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and not visited[rr, cc]:
                visited[rr, cc] = True
                filled[rr, cc] = max(level, filled[rr, cc])
                receiver[rr, cc] = index
                heapq.heappush(heap, (filled[rr, cc], rr * cols + cc))
    steepest = np.zeros(elevation.shape)
    for dr, dc in NEIGHBORS:
        rs = slice(max(0, -dr), min(rows, rows - dr))
        cs = slice(max(0, -dc), min(cols, cols - dc))
        rr = slice(max(0, dr), min(rows, rows + dr))
        cc = slice(max(0, dc), min(cols, cols + dc))
        slope = (filled[rs, cs] - filled[rr, cc]) / (spacing_m * np.hypot(dr, dc))
        better = slope > steepest[rs, cs]
        rgrid, cgrid = np.mgrid[rr, cc]
        receiver[rs, cs][better] = (rgrid * cols + cgrid)[better]
        steepest[rs, cs][better] = slope[better]
    receiver[0, :] = receiver[-1, :] = -1
    receiver[:, 0] = receiver[:, -1] = -1
    flat = receiver.ravel()
    incoming = np.bincount(flat[flat >= 0], minlength=elevation.size)
    queue = deque(np.flatnonzero(incoming == 0))
    accumulation = np.ones(elevation.size, dtype=np.int64)
    processed = 0
    while queue:
        index = queue.popleft()
        processed += 1
        target = flat[index]
        if target >= 0:
            accumulation[target] += accumulation[index]
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    assert processed == elevation.size, "Drainage graph contains a cycle"
    assert accumulation[flat == -1].sum() == elevation.size
    return receiver, accumulation.reshape(elevation.shape), filled


def catalogue(document):
    landmarks = validate(document, WORLD)
    required = {
        "parking",
        "command_post",
        "trail",
        "creek",
        "bridge",
        "pond",
        "forest",
        "ridge",
        "outcrop",
        "structure",
        "road",
        "campsite",
    }
    assert required <= {item["kind"] for item in landmarks}
    for item in landmarks:
        if item["kind"] in ("structure", "bridge", "command_post", "parking", "campsite"):
            assert item["heightM"] <= 15, "Structure exceeds authored height envelope"
            assert item["geometry"]["type"] == "polygon"
        if item["kind"] in ("forest", "pond"):
            assert item["geometry"]["type"] == "polygon"
        if item["kind"] in ("forest", "obstacle"):
            assert item["heightM"] <= 18, f"{item['id']} exceeds the tree height envelope"
    names = [name.casefold() for item in landmarks for name in (item["name"], *item["aliases"])]
    assert len(names) == len(set(names)), "Landmark names and aliases must be unique"
    polygons = [item for item in landmarks if item["geometry"]["type"] == "polygon"]
    for a, b in combinations(polygons, 2):
        if a["kind"] != b["kind"]:
            continue
        av, bv = points(a), points(b)
        ae = list(zip(av, np.roll(av, -1, axis=0)))
        be = list(zip(bv, np.roll(bv, -1, axis=0)))
        assert not any(_intersects(*x, *y) for x in ae for y in be), (
            f"Same-kind polygons intersect or touch: {a['id']}, {b['id']}"
        )
        assert _nearest(a, bv[0])[0] > 0 and _nearest(b, av[0])[0] > 0, (
            f"Same-kind polygon containment: {a['id']}, {b['id']}"
        )
    bridges = [item for item in landmarks if item["kind"] == "bridge"]
    streams = [points(item) for item in landmarks if item["kind"] == "creek"]
    crossed = set()
    for item in landmarks:
        if item["kind"] not in ("trail", "road"):
            continue
        route = points(item)
        for a, b in zip(route, route[1:]):
            for stream in streams:
                for c, d in zip(stream, stream[1:]):
                    if not _intersects(a, b, c, d):
                        continue
                    matrix = np.column_stack((b - a, c - d))
                    assert abs(np.linalg.det(matrix)) > 1e-8, "Route overlaps creek"
                    t, _ = np.linalg.solve(matrix, c - a)
                    crossing = a + t * (b - a)
                    matches = {
                        bridge["id"] for bridge in bridges if _nearest(bridge, crossing)[0] < 1e-8
                    }
                    assert matches, f"Unbridged creek crossing: {item['id']}"
                    crossed.update(matches)
    assert crossed == {bridge["id"] for bridge in bridges}, "Bridge must carry a route across creek"
    return landmarks


def points(item):
    return np.array([[p["x"] / 100, p["y"] / 100] for p in item["geometry"]["points"]])


def axes():
    return (
        np.linspace(BOUNDS["min"]["x"] / 100, BOUNDS["max"]["x"] / 100, COLUMNS),
        np.linspace(BOUNDS["min"]["y"] / 100, BOUNDS["max"]["y"] / 100, ROWS),
    )


def baseline_samples():
    generator = importlib.import_module("world.unreal.scripts.generate-open-meadow")
    north, east = np.meshgrid(*axes())
    samples = np.rint(
        FIELD["zeroSample"] + generator.height(north, east) * FIELD["samplesPerMetre"]
    )
    assert np.isfinite(samples).all() and samples.min() >= 0 and samples.max() <= 65535
    raw = samples.astype("<u2")
    assert hashlib.sha256(raw.tobytes()).hexdigest() == BASELINE_SHA256
    return raw


def lookup(elevation, vertices):
    indices = np.array(
        [
            (vertices[:, 1] - BOUNDS["min"]["y"] / 100) / FIELD["sampleSpacingMetres"],
            (vertices[:, 0] - BOUNDS["min"]["x"] / 100) / FIELD["sampleSpacingMetres"],
        ]
    )
    assert (indices >= -1e-8).all()
    assert (indices <= np.array(elevation.shape)[:, None] - 1 + 1e-8).all()
    return map_coordinates(
        elevation, np.clip(indices, 0, np.array(elevation.shape)[:, None] - 1), order=1
    )


def region(vertices, margin):
    """Grid slices and north/east sample coordinates around vertices plus a margin."""
    north, east = axes()
    low, high = vertices.min(axis=0) - margin, vertices.max(axis=0) + margin
    c0, c1 = np.searchsorted(north, [low[0], high[0]])
    r0, r1 = np.searchsorted(east, [low[1], high[1]])
    x, y = np.meshgrid(north[c0:c1], east[r0:r1])
    return (slice(r0, r1), slice(c0, c1)), x, y


def inside(vertices, x, y):
    path = PolygonPath(vertices)
    return path.contains_points(np.column_stack((x.ravel(), y.ravel()))).reshape(x.shape)


def signed_distance(mask):
    """Metres to a footprint edge half a sample beyond the mask; negative inside."""
    spacing = FIELD["sampleSpacingMetres"]
    outside = distance_transform_edt(~mask, sampling=spacing)
    within = distance_transform_edt(mask, sampling=spacing)
    return np.where(mask, spacing / 2 - within, outside - spacing / 2)


def corridors(vertices, radius):
    for i, (a, b) in enumerate(zip(vertices, vertices[1:])):
        window, x, y = region(np.array([a, b]), radius)
        delta = b - a
        t = np.clip(((x - a[0]) * delta[0] + (y - a[1]) * delta[1]) / np.dot(delta, delta), 0, 1)
        d = np.hypot(x - a[0] - t * delta[0], y - a[1] - t * delta[1])
        yield i, window, d, t


def line_field(vertices, profile, radius):
    """Nearest segment distance and linearly interpolated bed height inside a corridor."""
    distance = np.full((ROWS, COLUMNS), radius, dtype=float)
    target = np.zeros((ROWS, COLUMNS), dtype=float)
    for i, region, d, t in corridors(vertices, radius):
        chosen = d < distance[region]
        distance[region][chosen] = d[chosen]
        target[region][chosen] = (profile[i] + t * (profile[i + 1] - profile[i]))[chosen]
    return distance, target


def densify(vertices, step):
    arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(vertices, axis=0), axis=1))]
    stations = np.linspace(0, arc[-1], int(np.ceil(arc[-1] / step)) + 1)
    return stations, np.column_stack([np.interp(stations, arc, vertices[:, a]) for a in (0, 1)])


def blend(distance, radius):
    return np.cos(np.minimum(distance / radius, 1) * np.pi / 2) ** 2


def smoothstep(low, high, value):
    t = np.clip((value - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


def channel_lift(distance):
    """Height above the bed at a distance from the centreline, and the bank-top distance.

    Smoothstep banks span several samples, so diagonal reaches do not alias into steps; their
    steepest point plus the swale trend equals the bank angle.
    """
    tan_bank, tan_swale = (np.tan(np.radians(CHANNEL[k])) for k in ("bankDegrees", "swaleDegrees"))
    half = CHANNEL["bedM"] / 2
    top = half + 1.5 * CHANNEL["bankM"] / (tan_bank - tan_swale)
    lift = (
        CHANNEL["bankM"] * smoothstep(half, top, distance)
        + np.maximum(distance - half, 0) * tan_swale
    )
    return lift, top


def smooth_min(a, b, width):
    h = np.maximum(width - np.abs(a - b), 0) / width
    return np.minimum(a, b) - h * h * width / 4


def terrace(height, interval, phase):
    """Positive heights cut into flat treads and steep risers every interval metres."""
    assert np.all(interval > 0)
    level = np.maximum(height, 0) / interval + phase
    return np.maximum(np.floor(level) + smoothstep(0.6, 1, level % 1) - phase, 0) * interval


def noise(shape, seed, sigma_m):
    field = gaussian_filter(
        np.random.default_rng(seed).standard_normal(shape), sigma_m / FIELD["sampleSpacingMetres"]
    )
    return field / field.std()


def jitter(shape, seed, sigma_m, spread):
    """Multiplicative noise clipped to 1 +- 2 spread."""
    assert 0 < spread < 0.5
    return np.clip(1 + spread * noise(shape, seed, sigma_m), 1 - 2 * spread, 1 + 2 * spread)


def bearing(x, y, centre):
    return np.degrees(np.arctan2(y - centre[1], x - centre[0])) % 360


def periodic(degrees, knots, values, sigma):
    """Periodic linear interpolation in bearing, smoothed by a circular Gaussian (degrees)."""
    fine = np.arange(720) / 2
    curve = gaussian_filter1d(np.interp(fine, knots, values, period=360), 2 * sigma, mode="wrap")
    return np.interp(degrees, fine, curve, period=360)


def sector(degrees, values):
    """Design values evenly spaced clockwise from north around a centre, without radial creases."""
    return periodic(degrees, np.arange(len(values)) * 360 / len(values), values, 90 / len(values))


def triangle_slopes(elevation, spacing_m):
    """Maximum slope per cell across all four right triangles, covering both diagonals."""
    assert elevation.ndim == 2 and min(elevation.shape) >= 2
    assert np.isfinite(elevation).all() and spacing_m > 0
    north = np.diff(elevation, axis=1) / spacing_m
    east = np.diff(elevation, axis=0) / spacing_m
    squared_gradient = np.maximum(north[:-1] ** 2, north[1:] ** 2) + np.maximum(
        east[:, :-1] ** 2, east[:, 1:] ** 2
    )
    return np.degrees(np.arctan(np.sqrt(squared_gradient)))


def limit_slope(terrain, window, surface):
    """Write a designed surface lowered until every grid triangle is within the shaped slope.

    Each steeper triangle, on either diagonal, is scaled vertically about its lowest vertex and
    samples take the lowest proposal. The window border must keep the surrounding terrain.
    """
    rise = np.tan(np.radians(SHAPED_SLOPE_DEGREES)) * FIELD["sampleSpacingMetres"]
    rows, columns = surface.shape
    for _ in range(4 * max(surface.shape)):
        padded = np.pad(surface, 1, mode="edge")
        bound = np.full(padded.shape, np.inf)
        steepest = 0.0
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            windows = (
                (slice(1, rows + 1), slice(1, columns + 1)),
                (slice(1, rows + 1), slice(1 + dc, columns + 1 + dc)),
                (slice(1 + dr, rows + 1 + dr), slice(1, columns + 1)),
            )
            corner, north, east = (padded[w] for w in windows)
            gradient = np.hypot(north - corner, east - corner)
            steepest = max(steepest, float(gradient.max()))
            scale = np.minimum(rise / np.maximum(gradient, rise), 1)
            lowest = np.minimum(np.minimum(corner, north), east)
            for w, vertex in zip(windows, (corner, north, east)):
                np.minimum(bound[w], lowest + scale * (vertex - lowest), out=bound[w])
        if steepest <= rise * (1 + 1e-12):
            frame = np.ones(surface.shape, dtype=bool)
            frame[1:-1, 1:-1] = False
            assert np.array_equal(surface[frame], terrain[window][frame]), (
                "Shaping reached its window"
            )
            terrain[window] = surface
            return
        surface = np.minimum(surface, bound[1:-1, 1:-1])
    raise AssertionError("Slope limit did not converge")


def dome(terrain, item):
    """Roche moutonnee: whaleback stoss, ledged lee face plucked along joint blocks, exfoliation
    sheets broken at the joints, joint grooves, and a talus apron below the lee face."""
    vertices = points(item)
    centre = np.array(DOME["summitM"])
    offsets = vertices - centre
    angles = np.degrees(np.arctan2(offsets[:, 1], offsets[:, 0]))
    turns = np.diff(np.unwrap(np.radians(np.r_[angles, angles[0]])))
    assert np.all(turns > 0) or np.all(turns < 0), (
        "Dome footprint must be star-shaped at its summit"
    )
    assert 0 < DOME["leeDegrees"] < SHAPED_SLOPE_DEGREES and DOME["crestM"] < DOME["apronM"]
    assert DOME["leeBaseM"] < np.hypot(*offsets.T).min()
    window, x, y = region(vertices, DOME["marginM"])
    rays = bearing(x, y, centre)
    radius = periodic(rays, angles % 360, np.hypot(*offsets.T), 6)
    rho = np.hypot(x - centre[0], y - centre[1]) / radius
    exponent = sector(rays, DOME["exponents"])
    whaleback = DOME["heightM"] * np.maximum(0, 1 - rho**exponent) ** DOME["toeExponent"]
    # Joint grooves stay on the gentler upper dome: on capped flanks they would trench the face.
    gentle = 1 - smoothstep(
        0.35, 0.5, np.hypot(*np.gradient(whaleback, FIELD["sampleSpacingMetres"]))
    )
    blocks, joints = [], np.zeros(x.shape)
    for seed, (strike, spacing) in enumerate(DOME["joints"], 7):
        strike = np.radians(strike)
        across = (y - centre[1]) * np.cos(strike) - (x - centre[0]) * np.sin(strike)
        across = across / spacing + 0.15 * noise(x.shape, seed, 30)
        blocks.append(np.floor(across).astype(int))
        gap = np.abs(across - np.rint(across)) * spacing
        opening = np.clip(0.3 + noise(x.shape, seed + 10, 15), 0, 1)
        joints += DOME["jointM"] * np.clip(1 - gap / DOME["jointWidthM"], 0, 1) * opening * gentle
    first, second = (index - index.min() for index in blocks)
    table = np.random.default_rng(3).random((2, first.max() + 1, second.max() + 1))
    phase, scale = table[:, first, second]
    # A plane across the ice flow cuts the lee face; each joint block plucks back and ledges apart.
    lee = np.radians(DOME["leeBearing"])
    along = (x - centre[0]) * np.cos(lee) + (y - centre[1]) * np.sin(lee)
    lateral = (y - centre[1]) * np.cos(lee) - (x - centre[0]) * np.sin(lee)
    inset = DOME["leeBaseM"] - along - DOME["pluckM"] * phase + 4 * noise(x.shape, 5, 10)
    rise = np.tan(np.radians(DOME["leeDegrees"])) * inset
    shell = smooth_min(
        whaleback, DOME["apronM"] + terrace(rise, DOME["ledgeM"], phase), DOME["crestM"]
    )
    # Outer sheets spalled off between steps that follow the shell, offset at every joint.
    interval = DOME["sheetM"] * (0.7 + 0.6 * scale)
    sheets = terrace(shell, interval, phase + 0.5 * noise(x.shape, 1, 20)) - shell
    strength = np.clip(0.4 + 0.9 * noise(x.shape, 2, 25), 0, 1) * smoothstep(
        2, 0, whaleback - shell
    )
    rock = shell + (strength * sheets - joints) * smoothstep(1, 0.92, rho)
    assert rock.min() >= 0, "Dome surface dips below its floor"
    reach = 1 - (along - DOME["leeBaseM"]) / (DOME["apronOutM"] * jitter(x.shape, 4, 15, 0.2))
    side = smoothstep(1.3, 0.7, np.abs(lateral) / DOME["apronHalfM"]) * smoothstep(0, 20, along)
    apron = DOME["apronM"] * side * np.clip(reach, 0, 1) ** 2.4
    limit_slope(terrain, window, terrain[window] + np.maximum(rock, apron))


def basin(terrain, item, extension):
    """Designed floor below a flat surface; outside, a rim merges into the terrain below walls."""
    spec = BASINS[item["id"]]
    surface = spec["surfaceM"]
    assert 0 <= min(spec["slabs"]) and max(spec["slabs"]) < 1, "Slabs must keep shores dry"
    assert min(spec["floorM"]) >= 0 and min(spec["mergeM"]) > 0
    vertices = points(item)
    window, x, y = region(vertices, BASIN_MARGIN_M)
    rays = bearing(x, y, vertices.mean(axis=0))
    spacing = FIELD["sampleSpacingMetres"]

    def tangent(key):
        return np.tan(np.radians(sector(rays, spec[key])))

    water = inside(vertices, x, y)
    shore = signed_distance(water)
    # Bearing-keyed values are singular at the centre; smooth them over the basin.
    steepness = gaussian_filter(tangent("shoreDegrees"), 15 / spacing)
    depth = gaussian_filter(sector(rays, spec["depthM"]), 25 / spacing)
    assert depth.min() > 0
    # Smoothing across the medial axis keeps bays and points from creasing the floor.
    inward = np.minimum(np.maximum(shore, gaussian_filter(shore, 6 / spacing)), shore / 2)
    level = surface - depth * (1 - np.exp(np.minimum(inward, 0) * steepness / depth))
    lip = sector(rays, spec["lipM"]) * jitter(x.shape, 6, 25, 0.25)
    floor = water | (shore < sector(rays, spec["floorM"]) * jitter(x.shape, 8, 10, 0.08))
    cone = 0
    if extension:
        talus = points(extension)
        assert not (inside(talus, x, y) & water).any() and np.all(floor[inside(talus, x, y)])
        apex = np.unravel_index(np.argmax(np.where(floor, shore, -np.inf)), shore.shape)
        reach = np.hypot(x - x[apex], y - y[apex])
        cone = spec["talus"][1] * np.clip(1 - reach / reach[water].min(), 0, 1) ** 2
    toe = np.minimum(shore * tangent("riseDegrees"), lip)
    level = np.where(water, level, surface + np.maximum(toe, cone))
    delta = signed_distance(floor)
    edge = np.maximum(level[tuple(distance_transform_edt(~floor, return_indices=True)[1])], surface)
    rim = surface + np.minimum(delta * tangent("riseDegrees"), lip)
    cut = edge + delta * tangent("wallDegrees")
    assert np.all(rim[~floor] <= cut[~floor]), f"{item['id']} bench rises above its walls"
    bench = sector(rays, spec["benchM"]) * jitter(x.shape, 5, 40, 0.35)
    merge = sector(rays, spec["mergeM"]) * jitter(x.shape, 9, 40, 0.25)
    fill = np.maximum(rim - terrain[window], 0) * smoothstep(bench + merge, bench, delta)
    shaped = np.minimum(terrain[window] + fill, cut)
    height = shaped - surface
    steps = terrace(height, SLAB_M, 0.5 * noise(x.shape, 10, 15)) - np.maximum(height, 0)
    slabs = sector(rays, spec["slabs"]) * smoothstep(25, 10, delta)
    shaped += slabs * steps + 0.3 * noise(x.shape, 11, 4) * smoothstep(3, 12, delta) * np.minimum(
        fill, 1
    )
    limit_slope(terrain, window, np.where(floor, level, shaped))


def creek_profile(elevation, item, receiver, accumulation, named):
    """Designed bed and water along the lake-to-boundary chain; the natural reach follows D8."""
    vertices = points(item)
    spacing = FIELD["sampleSpacingMetres"] * DRAINAGE_STRIDE
    origin = np.array([BOUNDS["min"]["x"], BOUNDS["min"]["y"]]) / 100
    node = np.rint((np.array(CREEK_HEAD_M) - origin) / spacing).astype(int)
    assert np.linalg.norm(origin + node * spacing - CREEK_HEAD_M) < 0.01
    head = int(np.argmin(np.linalg.norm(vertices - origin - node * spacing, axis=1)))
    assert np.linalg.norm(vertices[head] - origin - node * spacing) < 1e-6, (
        "Creek must pass its head"
    )
    index = node[1] * receiver.shape[1] + node[0]
    upstream = int(accumulation.ravel()[index])
    path = []
    while index != -1:
        path.append(origin + spacing * np.array(divmod(index, receiver.shape[1]))[::-1])
        index = receiver.ravel()[index]
    gap = np.linalg.norm(vertices[head:, None] - np.array(path)[None], axis=2)
    deviation = float(max(gap.min(axis=1).max(), gap.min(axis=0).max()))
    assert deviation < NATURAL_REACH_M, f"Natural reach leaves computed drainage by {deviation} m"
    assert upstream > 1000 and np.allclose(path[-1], vertices[-1], atol=1e-6)
    assert vertices[-1][1] == BOUNDS["max"]["y"] / 100, "Creek must leave the east boundary"
    lake, pond = named["granite-lake"], named["beaver-pond"]
    assert np.isclose(points(lake), vertices[0], rtol=0, atol=1e-6).all(axis=1).any(), (
        "Creek must start at the lake spillway"
    )
    span = np.flatnonzero(inside(points(pond), *vertices.T))
    assert span.size and np.all(np.diff(span) == 1), "Creek must cross the pond once"
    dam = span[-1] + 1
    aspen = np.flatnonzero(inside(points(named["aspen-bend"]), *vertices.T))
    bridge = int(np.argmin(np.linalg.norm(vertices - points(named["footbridge"]).mean(0), axis=1)))
    assert aspen.size and aspen[-1] < head < bridge < span[0], "Chain order: lake, aspens, bridge"
    lake_surface = BASINS["granite-lake"]["surfaceM"]
    pond_surface = BASINS["beaver-pond"]["surfaceM"]
    (pond_depth,) = BASINS["beaver-pond"]["depthM"]
    length = np.r_[0, np.cumsum(np.linalg.norm(np.diff(vertices, axis=0), axis=1))]
    candidate = lookup(elevation, vertices) - CHANNEL_DEPTH_M
    candidate[0] = lake_surface - CHANNEL["waterM"]
    candidate[span] = pond_surface - pond_depth
    bed = np.minimum.accumulate(candidate + CHANNEL_GRADE * length) - CHANNEL_GRADE * length
    pool = np.where(np.arange(len(bed)) < dam, pond_surface, -np.inf)
    water = np.maximum(bed + CHANNEL["waterM"], pool)
    assert np.all(np.diff(bed) < 0) and np.all(np.diff(water) <= 0) and water[0] == lake_surface
    displacement = vertices[-1] - vertices[0]
    assert abs(displacement[1]) > abs(displacement[0]), "Creek must run broadly east-west"
    return (
        vertices,
        bed,
        water,
        {
            "id": item["id"],
            "routeNodes": len(vertices),
            "lengthM": float(length[-1]),
            "eastDisplacementM": float(displacement[1]),
            "northDisplacementM": float(displacement[0]),
            "downstreamAzimuthDegrees": float(
                np.degrees(np.arctan2(displacement[1], displacement[0])) % 360
            ),
            "naturalReachStartNode": head,
            "naturalReachMaxDeviationFromD8M": deviation,
            "upstreamSamplesAtHead": upstream,
            "lakeSurfaceM": lake_surface,
            "spillSillM": float(bed[0]),
            "pondSurfaceM": pond_surface,
            "pondBedM": pond_surface - pond_depth,
            "damNode": int(dam),
            "damStepM": float(water[dam - 1] - water[dam]),
            "exitBedM": float(bed[-1]),
            "arcLengthM": length.tolist(),
            "designBedM": bed.tolist(),
            "waterSurfaceM": water.tolist(),
        },
    )


def route_slopes(elevation, slope, item, decks):
    """Steepest ground triangle and 3 m station grade along a route, excluding bridge decks."""
    spacing = FIELD["sampleSpacingMetres"]
    origin = [BOUNDS["min"]["x"] / 100, BOUNDS["min"]["y"] / 100]

    def ground(vertices):
        return ~np.any([inside(points(deck), *vertices.T) for deck in decks], 0)

    fine = densify(points(item), spacing / 2)[1]
    cells = np.minimum(
        np.floor((fine - origin) / spacing).astype(int), np.array(slope.shape[::-1]) - 1
    )
    north, east = cells[ground(fine)].T
    stations, coarse = densify(points(item), 3)
    grade = np.abs(np.diff(lookup(elevation, coarse))) / np.diff(stations)
    walked = ground(coarse)
    return (
        float(slope[east, north].max()),
        float(grade[walked[1:] & walked[:-1]].max()),
        float(stations[-1]),
    )


def layers(elevation, named, creek, water):
    """Landscape weights in MASKS order plus dry meadow; each sample sums to exactly 255."""
    north, east = axes()
    x, y = np.meshgrid(north, east)
    slope = np.pad(
        triangle_slopes(elevation, FIELD["sampleSpacingMetres"]), ((0, 1), (0, 1)), "edge"
    )
    steep = smoothstep(22, 30, slope)
    channel, level = line_field(creek, water, CHANNEL_BANK_M)
    edge = (CHANNEL["bedM"] / 2 + channel_lift(0)[1]) / 2
    band = np.clip(10 + 4 * noise(x.shape, 3, 25), 5, 15)
    cover = {
        "cobble-bed": (1 - smoothstep(edge, edge + 1, channel)) * (1 - steep),
        "granite": steep.copy(),
        "wet-meadow": (1 - smoothstep(band - 2, band + 2, channel))
        * (1 - smoothstep(0.6, 1.4, elevation - level)),
    }
    for identifier, spec in BASINS.items():
        item, surface = named[identifier], spec["surfaceM"]
        window, wx, wy = region(points(item), 50)
        delta = signed_distance(inside(points(item), wx, wy))
        wet = (1 - smoothstep(6, 10, delta)) * (
            1 - smoothstep(0.6, 1.4, elevation[window] - surface)
        )
        if identifier == "granite-lake":
            slabs = smoothstep(-0.2, 0.4, np.cos(np.radians(bearing(wx, wy, points(item).mean(0)))))
            cover["granite"][window] = np.maximum(cover["granite"][window], slabs * (delta < 45))
            beach = (1 - slabs) * (1 - smoothstep(3, 6, delta))
            cover["cobble-bed"][window] = np.maximum(cover["cobble-bed"][window], beach)
        if identifier == "lookout-tarn":
            cover["granite"][window] = np.maximum(cover["granite"][window], delta < 0)
        if identifier == "beaver-pond":
            wet = np.maximum(wet, delta < 0)
        cover["wet-meadow"][window] = np.maximum(cover["wet-meadow"][window], wet)
    for identifier in GRANITE:
        window, wx, wy = region(points(named[identifier]), 1)
        footprint = inside(points(named[identifier]), wx, wy)
        cover["granite"][window] = np.maximum(cover["granite"][window], footprint)
    road = [item for item in named.values() if item["kind"] == "road"]
    trails = [item for item in named.values() if item["kind"] == "trail"]
    cover["gravel-road"] = 1 - smoothstep(
        1.75, 2.5, np.min([line_field(points(i), np.zeros(len(points(i))), 4)[0] for i in road], 0)
    )
    cover["trail-tread"] = 1 - smoothstep(
        0.6, 1.1, np.min([line_field(points(i), np.zeros(len(points(i))), 2)[0] for i in trails], 0)
    )
    needle = np.zeros(x.shape)
    for identifier in LODGEPOLES:
        window, wx, wy = region(points(named[identifier]), 20)
        depth = -signed_distance(inside(points(named[identifier]), wx, wy))
        needle[window] = smoothstep(-3, 5, depth + 6 * noise(wx.shape, 4, 8))
    cover["needle-floor"] = needle
    weights, remaining = [], np.ones(x.shape)
    for name in MASKS:
        assert cover[name].shape == x.shape and 0 <= cover[name].min() <= cover[name].max() <= 1
        weights.append(cover[name] * remaining)
        remaining = remaining - weights[-1]
    weights = np.stack(weights + [remaining])
    assert weights.min() >= 0 and np.allclose(weights.sum(axis=0), 1, rtol=0, atol=1e-12)
    total = np.rint(np.cumsum(weights, axis=0) * 255)
    assert np.all(total[-1] == 255)
    quantized = np.diff(total, axis=0, prepend=0).astype(np.uint8)
    assert np.all(quantized.sum(axis=0, dtype=np.int64) == 255)
    return quantized


def build(document):
    started = time.perf_counter()
    landmarks = catalogue(document)
    named = {item["id"]: item for item in landmarks}
    original = baseline_samples()
    baseline = (original.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    terrain = baseline.copy()
    assert (ROWS - 1) % DRAINAGE_STRIDE == (COLUMNS - 1) % DRAINAGE_STRIDE == 0
    receiver, accumulation, filled = drainage(
        baseline[::DRAINAGE_STRIDE, ::DRAINAGE_STRIDE],
        FIELD["sampleSpacingMetres"] * DRAINAGE_STRIDE,
    )
    north, east = axes()
    x, y = np.meshgrid(north, east)
    hill = importlib.import_module("world.unreal.scripts.generate-open-meadow").hill(x, y)
    reshaped = hill > DOME["capM"] - DOME["capSoftM"]
    terrain[reshaped] += (smooth_min(hill, DOME["capM"], DOME["capSoftM"]) - hill)[reshaped]
    (creek,) = [item for item in landmarks if item["kind"] == "creek"]
    assert all(sigma > 0 and 0 < std <= 0.5 for sigma, std in MEADOW_RELIEF)
    (n0, n1), (e0, e1) = MEADOW_M
    outside = np.hypot(
        np.maximum(np.maximum(x - n1, n0 - x), 0), np.maximum(np.maximum(y - e1, e0 - y), 0)
    )
    stream = line_field(points(creek), np.zeros(len(points(creek))), CHANNEL_BANK_M + 20)[0]
    relief = sum(
        std * noise(x.shape, seed, sigma) for seed, (sigma, std) in enumerate(MEADOW_RELIEF, 12)
    )
    terrain += (
        relief * (1 - smoothstep(0, 60, outside)) * smoothstep(20, CHANNEL_BANK_M + 20, stream)
    )
    dome(terrain, named[DOME["id"]])
    for identifier, spec in BASINS.items():
        basin(terrain, named[identifier], spec["talus"] and named[spec["talus"][0]])
    landform = terrain.copy()
    for item in landmarks:
        if item["kind"] not in GRADE_BANK_M:
            continue
        dense = densify(points(item), 3)[1]
        profile = gaussian_filter1d(lookup(landform, dense), 2, mode="nearest")
        radius = GRADE_BANK_M[item["kind"]]
        distance, target = line_field(dense, profile, radius)
        terrain += (target - terrain) * blend(distance, radius)
    for item in landmarks:
        if item["kind"] == "structure":
            window, wx, wy = region(points(item), PAD_BLEND_M)
            beyond = signed_distance(inside(points(item), wx, wy))
            local = terrain[window]
            level = np.median(local[beyond < 0])
            local += (level - local) * blend(np.maximum(beyond, 0), PAD_BLEND_M)
    vertices, bed, water, creek_report = creek_profile(
        terrain, creek, receiver, accumulation, named
    )
    channel, channel_bed = line_field(vertices, bed, CHANNEL_BANK_M)
    lift, bank_top = channel_lift(channel)
    terrain = np.minimum(terrain, np.where(channel < CHANNEL_BANK_M, channel_bed + lift, np.inf))
    limit_slope(terrain, np.s_[:, :], terrain.copy())
    samples = np.rint(FIELD["zeroSample"] + terrain * FIELD["samplesPerMetre"])
    assert np.isfinite(samples).all() and samples.min() >= 0 and samples.max() <= 65535
    raw = samples.astype("<u2")
    south = north <= north[0] + SOUTH_BUFFER_M
    different_south = int(np.count_nonzero(raw[:, south] != original[:, south]))
    assert different_south == 0, "Southern terrain buffer changed"
    quantized = (raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    spacing = FIELD["sampleSpacingMetres"]
    dy, dx = np.gradient(quantized, spacing)
    central_slope = float(np.degrees(np.arctan(np.hypot(dx, dy))).max())
    slope = triangle_slopes(quantized, spacing)
    maximum_slope = float(slope.max())
    row, column = np.unravel_index(np.argmax(slope), slope.shape)
    axis_gradient = max(float(np.abs(np.diff(quantized, axis=a)).max()) / spacing for a in (0, 1))
    relief = float(np.ptp(quantized))
    assert maximum_slope <= MAX_SLOPE_DEGREES, (
        f"Slope cap exceeded: {maximum_slope} degrees at north/east {north[column]}, {east[row]} m"
    )
    assert relief < 65, f"Valley relief limit exceeded: {relief} metres"
    routes = {}
    for item in landmarks:
        if item["kind"] in GRADE_BANK_M:
            decks = [i for i in landmarks if i["kind"] == "bridge"]
            steepest, grade, length = route_slopes(quantized, slope, item, decks)
            routes[item["id"]] = {"lengthM": length, "maxSlopeDegrees": steepest, "maxGrade": grade}
            if item["kind"] == "trail":
                assert steepest <= TRAIL_SLOPE_DEGREES, (
                    f"{item['id']} unwalkable: {steepest} degrees"
                )
            else:
                assert grade <= ROAD_GRADE, f"{item['id']} grade {grade} exceeds {ROAD_GRADE}"
    sampled = lookup(quantized, vertices)
    rise = np.diff(sampled)
    assert rise.max() <= 1 / FIELD["samplesPerMetre"] + 1e-9, f"Creek bed rises {rise.max()} m"
    assert np.all(sampled <= water + 1e-9), "Creek bed above its water surface"
    tangent = np.gradient(vertices, axis=0)
    normal = (
        np.column_stack((-tangent[:, 1], tangent[:, 0])) / np.linalg.norm(tangent, axis=1)[:, None]
    )
    sides = [vertices + side * bank_top * normal for side in (-1, 1)]
    low, high = (np.array([BOUNDS[end]["x"], BOUNDS[end]["y"]]) / 100 for end in ("min", "max"))
    near = np.array(
        [min(_nearest(named[i], v)[0] for i in ("granite-lake", "beaver-pond")) for v in vertices]
    )
    checked = (near > 10) & np.all([((s >= low) & (s <= high)).all(axis=1) for s in sides], 0)
    banks = np.minimum(*(lookup(quantized, s[checked]) for s in sides))
    margin = banks - water[checked]
    freeboard = float(margin.min())
    assert freeboard > 0, (
        f"Creek bank {-freeboard} m under water at {vertices[checked][margin.argmin()]}"
    )
    basins = {}
    clear = line_field(vertices, bed, 12)[0] >= 12
    for identifier, spec in BASINS.items():
        surface = spec["surfaceM"]
        window, wx, wy = region(points(named[identifier]), 10)
        delta = signed_distance(inside(points(named[identifier]), wx, wy))
        local = quantized[window]
        ring = (delta >= 0.4) & (delta <= 3) & clear[window]
        basins[identifier] = {
            "surfaceM": surface,
            "deepestM": float(surface - local[delta < 0].min()),
            "highestFloorM": float(local[delta <= -0.8].max()),
            "lowestRingM": float(local[ring].min()),
        }
        assert basins[identifier]["highestFloorM"] < surface, f"{identifier} floor emerges"
        assert basins[identifier]["lowestRingM"] > surface, f"{identifier} bank below water line"
    window, wx, wy = region(points(named[DOME["id"]]), 1)
    summit = float(quantized[window][inside(points(named[DOME["id"]]), wx, wy)].max())
    (n0, n1), (e0, e1) = MEADOW_M
    meadow = float(
        np.median(quantized[np.ix_((east >= e0) & (east <= e1), (north >= n0) & (north <= n1))])
    )
    assert summit - meadow <= 60, f"Dome summit {summit - meadow} m above the meadow"
    port = WORLD["port"]
    pad = np.array([port["centerUeCm"]["x"], port["centerUeCm"]["y"]]) / 100
    half_pad = np.array(port["dimensionsMetres"][:2]) / 2
    gap = np.hypot(
        *np.maximum(np.abs(np.stack((x, y)) - pad[:, None, None]) - half_pad[:, None, None], 0)
    )
    top = port["centerUeCm"]["z"] / 100
    perimeter = [
        pad + half_pad * [np.cos(a), np.sin(a)] / max(abs(np.cos(a)), abs(np.sin(a)))
        for a in np.linspace(0, 2 * np.pi, 400, endpoint=False)
    ]
    pad_report = {
        "padTopM": top,
        "terrainAbovePadWithin20M": float(quantized[gap <= 20].max() - top),
        "terrainAbovePadWithin40M": float(quantized[gap <= 40].max() - top),
        "tallestLandmarkWithin20M": max(
            [i["heightM"] for i in landmarks if min(_nearest(i, p)[0] for p in perimeter) <= 20],
            default=0,
        ),
        "tallestLandmarkWithin40M": max(
            [i["heightM"] for i in landmarks if min(_nearest(i, p)[0] for p in perimeter) <= 40],
            default=0,
        ),
    }
    assert (
        pad_report["terrainAbovePadWithin20M"] <= 0.3
        and pad_report["terrainAbovePadWithin40M"] <= 4
    )
    assert (
        pad_report["tallestLandmarkWithin20M"] <= 0.3
        and pad_report["tallestLandmarkWithin40M"] <= 4
    )
    masks = layers(quantized, named, vertices, water)
    creek_report["sampledFinalBedM"] = sampled.tolist()
    creek_report["sampledFinalBedMaximumDownstreamRiseM"] = float(rise.max())
    creek_report["minimumBankFreeboardM"] = freeboard
    report = {
        "environmentAssetVersion": WORLD["environmentAssetVersion"],
        "heightfieldSha256": hashlib.sha256(raw.tobytes()).hexdigest(),
        "baselineSha256": BASELINE_SHA256,
        "baselineSource": "Tracked generate-open-meadow.height, quantized on world.json axes",
        "shapeRowsColumns": [ROWS, COLUMNS],
        "rowAxis": FIELD["rowAxis"],
        "columnAxis": FIELD["columnAxis"],
        "sampleSpacingM": spacing,
        "minElevationM": float(quantized.min()),
        "maxElevationM": float(quantized.max()),
        "reliefM": relief,
        "maxSlopeDegrees": maximum_slope,
        "slopeMethod": "Maximum quantized grid-cell triangle gradient across both diagonals",
        "slopeCapDegrees": MAX_SLOPE_DEGREES,
        "shapedSlopeDegrees": SHAPED_SLOPE_DEGREES,
        "maxAxisSampleGradient": axis_gradient,
        "maxCentralDifferenceSlopeDegrees": central_slope,
        "changedSamples": int(np.count_nonzero(raw != original)),
        "maximumCutM": float((baseline - quantized).max()),
        "maximumFillM": float((quantized - baseline).max()),
        "southPreservation": {
            "bufferM": SOUTH_BUFFER_M,
            "columns": int(south.sum()),
            "samples": int(original[:, south].size),
            "differentSamples": different_south,
        },
        "drainage": {
            "method": "Boundary priority flood, D8 steepest descent, upstream sample accumulation",
            "stride": DRAINAGE_STRIDE,
            "shape": list(receiver.shape),
            "maximumDepressionFillM": float(
                (filled - baseline[::DRAINAGE_STRIDE, ::DRAINAGE_STRIDE]).max()
            ),
            "creek": creek_report,
        },
        "basins": basins,
        "dome": {
            "summitM": summit,
            "meadowMedianM": meadow,
            "aboveMeadowM": summit - meadow,
            "design": DOME,
        },
        "routes": {
            "walkerSlopeLimitDegrees": TRAIL_SLOPE_DEGREES,
            "roadGradeLimit": ROAD_GRADE,
            "measured": routes,
        },
        "pad": pad_report,
        "masks": {
            "layers": [*MASKS, "dry-meadow"],
            "layout": "uint8 weights summing to 255 per sample, height.r16 row/column layout",
            "meanWeight": {
                name: float(layer.mean() / 255)
                for name, layer in zip([*MASKS, "dry-meadow"], masks)
            },
        },
        "grading": {
            "trailBankM": GRADE_BANK_M["trail"],
            "structurePadBlendM": PAD_BLEND_M,
            "roadBankM": GRADE_BANK_M["road"],
            "channel": CHANNEL,
            "channelCorridorM": CHANNEL_BANK_M,
            "channelMinimumCutM": CHANNEL_DEPTH_M,
            "channelGrade": CHANNEL_GRADE,
            "basins": BASINS,
        },
        "landmarkCount": len(landmarks),
        "landmarkValidation": {
            "worldBoundsAndSchema": True,
            "heightEnvelopes": True,
            "uniqueNamesAndAliases": True,
            "sameKindPolygonsDisjointAndNotTouching": True,
            "allRouteCreekCrossingsBridged": True,
        },
        "heightMMeaning": "Authored design maxima above terrain; not measured mesh dimensions",
        "pythonVersion": platform.python_version(),
        "dependencies": {
            name: version(name) for name in ("numpy", "scipy", "matplotlib", "pillow")
        },
        "generationSeconds": time.perf_counter() - started,
    }
    return raw, report, masks


def generate(evidence_dir):
    load_env()
    out = (ROOT / FIELD["path"]).parent
    assert out == ROOT / ".local/world/valley/terrain"
    assert not out.exists(), f"Terrain destination already exists: {out}"
    assert not evidence_dir.exists(), f"Evidence destination already exists: {evidence_dir}"
    document = json.loads(LANDMARKS_PATH.read_text())
    raw, report, masks = build(document)
    report["landmarksSha256"] = hashlib.sha256(LANDMARKS_PATH.read_bytes()).hexdigest()
    assert report["heightfieldSha256"] == WORLD["heightfieldSha256"], (
        "Record the generated terrain hash in world.json"
    )
    elevation = (raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    for item in document["landmarks"]:
        expected = lookup(elevation, points(item)) * 100
        actual = np.array([p["z"] for p in item["geometry"]["points"]])
        np.testing.assert_allclose(actual, expected, atol=0.001)
    out.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    raw.tofile(out / "height.r16")
    report["masks"]["files"] = {}
    for name, layer in zip(report["masks"]["layers"], masks):
        path = out / f"mask-{name}.png"
        Image.fromarray(layer).save(path)
        assert np.array_equal(np.asarray(Image.open(path)), layer)
        report["masks"]["files"][name] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    for destination in (out, evidence_dir):
        (destination / "terrain.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "drainage"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    generate(parser.parse_args().evidence_dir)
