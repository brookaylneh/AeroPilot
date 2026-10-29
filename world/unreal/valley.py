"""Deterministic valley grading from the accepted meadow and public landmarks."""

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
from scipy.ndimage import distance_transform_edt, gaussian_filter1d, map_coordinates

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
POND_DEPTH_M = 0.6
POND_BANK_M = 48
GRADE_BANK_M = {"trail": 7, "road": 15}


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
        if item["kind"] == "forest":
            assert item["heightM"] <= 18, "Forest exceeds authored tree height envelope"
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


def corridors(vertices, radius):
    north, east = axes()
    for i, (a, b) in enumerate(zip(vertices, vertices[1:])):
        lo, hi = np.minimum(a, b) - radius, np.maximum(a, b) + radius
        c0, c1 = np.searchsorted(north, [lo[0], hi[0]])
        r0, r1 = np.searchsorted(east, [lo[1], hi[1]])
        x, y = np.meshgrid(north[c0:c1], east[r0:r1])
        delta = b - a
        t = np.clip(((x - a[0]) * delta[0] + (y - a[1]) * delta[1]) / np.dot(delta, delta), 0, 1)
        d = np.hypot(x - a[0] - t * delta[0], y - a[1] - t * delta[1])
        yield i, (slice(r0, r1), slice(c0, c1)), d, t


def line_field(vertices, profile, radius):
    """Nearest segment distance and linearly interpolated bed height inside a corridor."""
    distance = np.full((ROWS, COLUMNS), radius, dtype=float)
    target = np.zeros((ROWS, COLUMNS), dtype=float)
    for i, region, d, t in corridors(vertices, radius):
        chosen = d < distance[region]
        distance[region][chosen] = d[chosen]
        target[region][chosen] = (profile[i] + t * (profile[i + 1] - profile[i]))[chosen]
    return distance, target


def blend(distance, radius):
    return np.cos(np.minimum(distance / radius, 1) * np.pi / 2) ** 2


def creek_profile(elevation, item, receiver, accumulation):
    vertices = points(item)
    spacing = FIELD["sampleSpacingMetres"] * DRAINAGE_STRIDE
    indices = (vertices - [BOUNDS["min"]["x"] / 100, BOUNDS["min"]["y"] / 100]) / spacing
    np.testing.assert_allclose(indices, np.rint(indices), atol=1e-8)
    col, row = np.rint(indices).astype(int).T
    flat = row * receiver.shape[1] + col
    np.testing.assert_array_equal(receiver.ravel()[flat[:-1]], flat[1:])
    assert receiver.ravel()[flat[-1]] == -1, "Creek must complete its downstream reach"
    flow = accumulation.ravel()[flat]
    assert np.all(np.diff(flow) >= 0) and flow[0] > 1000
    displacement = vertices[-1] - vertices[0]
    assert abs(displacement[1]) > abs(displacement[0]), "Creek must run broadly east-west"
    length = np.r_[0, np.cumsum(np.linalg.norm(np.diff(vertices, axis=0), axis=1))]
    bed = (
        np.minimum.accumulate(lookup(elevation, vertices))
        - CHANNEL_DEPTH_M
        - CHANNEL_GRADE * length
    )
    assert np.all(np.diff(bed) < 0)
    return (
        vertices,
        bed,
        {
            "id": item["id"],
            "routeNodes": len(vertices),
            "lengthM": float(length[-1]),
            "eastDisplacementM": float(displacement[1]),
            "northDisplacementM": float(displacement[0]),
            "eastSpanM": float(np.ptp(vertices[:, 1])),
            "northSpanM": float(np.ptp(vertices[:, 0])),
            "downstreamAzimuthDegrees": float(
                np.degrees(np.arctan2(displacement[1], displacement[0])) % 360
            ),
            "upstreamSamplesAtStart": int(flow[0]),
            "upstreamSamplesAtOutlet": int(flow[-1]),
            "receiverEdgesMatched": len(vertices) - 1,
            "strictlyDescendingDesignProfile": True,
        },
    )


def build(document):
    started = time.perf_counter()
    landmarks = catalogue(document)
    original = baseline_samples()
    baseline = (original.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    terrain = baseline.copy()
    assert (ROWS - 1) % DRAINAGE_STRIDE == (COLUMNS - 1) % DRAINAGE_STRIDE == 0
    receiver, accumulation, filled = drainage(
        baseline[::DRAINAGE_STRIDE, ::DRAINAGE_STRIDE],
        FIELD["sampleSpacingMetres"] * DRAINAGE_STRIDE,
    )
    for item in landmarks:
        if item["kind"] not in GRADE_BANK_M:
            continue
        vertices = points(item)
        arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(vertices, axis=0), axis=1))]
        stations = np.linspace(0, arc[-1], int(np.ceil(arc[-1] / 3)) + 1)
        dense = np.column_stack([np.interp(stations, arc, vertices[:, axis]) for axis in (0, 1)])
        profile = gaussian_filter1d(lookup(baseline, dense), 2, mode="nearest")
        radius = GRADE_BANK_M[item["kind"]]
        distance, target = line_field(dense, profile, radius)
        terrain += (target - terrain) * blend(distance, radius)
    creek_reports = []
    for item in landmarks:
        if item["kind"] != "creek":
            continue
        vertices, profile, report = creek_profile(baseline, item, receiver, accumulation)
        source = terrain.copy()
        for i, region, distance, t in corridors(vertices, CHANNEL_BANK_M):
            target = profile[i] + t * (profile[i + 1] - profile[i])
            candidate = source[region] + np.minimum(target - source[region], 0) * blend(
                distance, CHANNEL_BANK_M
            )
            terrain[region] = np.minimum(terrain[region], candidate)
        creek_reports.append(report)
    north, east = axes()
    x, y = np.meshgrid(north, east)
    pond_reports = []
    for item in landmarks:
        if item["kind"] != "pond":
            continue
        inside = (
            PolygonPath(points(item))
            .contains_points(np.column_stack((x.ravel(), y.ravel())))
            .reshape(ROWS, COLUMNS)
        )
        assert inside.any()
        bed = float(terrain[inside].min() - POND_DEPTH_M)
        distance = distance_transform_edt(~inside, sampling=FIELD["sampleSpacingMetres"])
        terrain += np.minimum(bed - terrain, 0) * blend(distance, POND_BANK_M)
        pond_reports.append(
            {"id": item["id"], "bedElevationM": bed, "footprintSamples": int(inside.sum())}
        )
    samples = np.rint(FIELD["zeroSample"] + terrain * FIELD["samplesPerMetre"])
    assert np.isfinite(samples).all() and samples.min() >= 0 and samples.max() <= 65535
    raw = samples.astype("<u2")
    south = north <= north[0] + SOUTH_BUFFER_M
    different_south = int(np.count_nonzero(raw[:, south] != original[:, south]))
    assert different_south == 0, "Southern terrain buffer changed"
    quantized = (raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
    for item, report in zip((item for item in landmarks if item["kind"] == "creek"), creek_reports):
        rise = np.diff(lookup(quantized, points(item)))
        report["sampledFinalBedMaximumDownstreamRiseM"] = float(rise.max())
        report["sampledFinalBedRisingSegments"] = int(np.count_nonzero(rise > 1e-8))
    dy, dx = np.gradient(quantized, FIELD["sampleSpacingMetres"])
    slope = np.degrees(np.arctan(np.hypot(dx, dy)))
    maximum_slope = float(slope.max())
    row, column = np.unravel_index(np.argmax(slope), slope.shape)
    relief = float(np.ptp(quantized))
    assert maximum_slope < 12, (
        f"Valley slope limit exceeded: {maximum_slope} degrees at north/east "
        f"{north[column]}, {east[row]} m, baseline/final height "
        f"{baseline[row, column]}, {quantized[row, column]} m"
    )
    assert relief < 65, f"Valley relief limit exceeded: {relief} metres"
    report = {
        "environmentAssetVersion": WORLD["environmentAssetVersion"],
        "heightfieldSha256": hashlib.sha256(raw.tobytes()).hexdigest(),
        "baselineSha256": BASELINE_SHA256,
        "baselineSource": "Tracked generate-open-meadow.height, quantized on world.json axes",
        "shapeRowsColumns": [ROWS, COLUMNS],
        "rowAxis": FIELD["rowAxis"],
        "columnAxis": FIELD["columnAxis"],
        "sampleSpacingM": FIELD["sampleSpacingMetres"],
        "minElevationM": float(quantized.min()),
        "maxElevationM": float(quantized.max()),
        "reliefM": relief,
        "maxSlopeDegrees": maximum_slope,
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
            "creeks": creek_reports,
        },
        "ponds": pond_reports,
        "grading": {
            "trailBankM": GRADE_BANK_M["trail"],
            "roadBankM": GRADE_BANK_M["road"],
            "channelBankM": CHANNEL_BANK_M,
            "channelMinimumCutM": CHANNEL_DEPTH_M,
            "channelGrade": CHANNEL_GRADE,
            "pondBankM": POND_BANK_M,
            "pondAdditionalDepthM": POND_DEPTH_M,
        },
        "landmarkCount": len(landmarks),
        "landmarkValidation": {
            "worldBoundsAndSchema": True,
            "heightEnvelopes": True,
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
    return raw, report


def generate(evidence_dir):
    load_env()
    out = (ROOT / FIELD["path"]).parent
    assert out == ROOT / ".local/world/valley/terrain"
    assert not out.exists(), f"Terrain destination already exists: {out}"
    assert not evidence_dir.exists(), f"Evidence destination already exists: {evidence_dir}"
    document = json.loads(LANDMARKS_PATH.read_text())
    raw, report = build(document)
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
    for destination in (out, evidence_dir):
        (destination / "terrain.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    generate(parser.parse_args().evidence_dir)
