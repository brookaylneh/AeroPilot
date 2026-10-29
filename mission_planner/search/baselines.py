"""Fixed expanding-square geometry and ordered evaluation on the planner's flight clock."""

import math

import numpy as np

from mission_planner.search.detection import CAPTURE_SPACING_M, track_detection
from mission_planner.search.grid import inside
from mission_planner.search.planner import (
    AIRBORNE_SECONDS,
    RESERVE_SECONDS,
    SECTOR_DRONES,
    SECTOR_INSET_M,
    TRANSIT_HEIGHTS_M,
    distance,
    point,
    track_pod,
    transit,
)
from mission_planner.search.terrain import terrain_leg, waypoint
from mission_planner.search.timing import (
    extension_seconds,
    landing_seconds,
    launch_seconds,
    travel_seconds,
)
from world.cosys.terrain import WORLD


def _rectangle(vertices):
    assert len(vertices) == 4, "Expanding-square comparison requires an axis-aligned rectangle"
    assert all(set(p) == {"north_m", "east_m"} for p in vertices)
    points = np.array([[p["north_m"], p["east_m"]] for p in vertices])
    assert np.isfinite(points).all()
    low, high = points.min(axis=0), points.max(axis=0)
    assert (high > low).all()
    assert {tuple(p) for p in points} == {
        (n, e) for n in (low[0], high[0]) for e in (low[1], high[1])
    }, "Expanding-square comparison requires an axis-aligned rectangle"
    assert all(np.count_nonzero(a != b) == 1 for a, b in zip(points, np.roll(points, -1, axis=0)))
    return low, high


def expanding_square(datum, spacing, axis, vertices):
    low, high = _rectangle(vertices)
    assert type(axis) is int and axis in (0, 1)
    assert type(spacing) in (int, float) and math.isfinite(spacing) and spacing > 0
    assert set(datum) == {"north_m", "east_m"}
    current = np.array([datum["north_m"], datum["east_m"]], dtype=float)
    assert np.isfinite(current).all() and (low <= current).all() and (current <= high).all()
    covered_low, covered_high = current.copy(), current.copy()
    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))
    result = []
    while not ((covered_low <= low).all() and (covered_high >= high).all()):
        index = len(result)
        end = current + np.array(directions[(index + axis) % 4]) * spacing * (index // 2 + 1)
        result.append((point(*current, 0), point(*end, 0)))
        covered_low, covered_high = np.minimum(covered_low, end), np.maximum(covered_high, end)
        current = end
    return result


def partition_square(vertices, datum, spacing, axis):
    low, high = _rectangle(vertices)
    bounds = np.linspace(low[1], high[1], len(SECTOR_DRONES) + 1)
    assert np.all(np.diff(bounds) > 2 * SECTOR_INSET_M), "Search sectors have no usable width"
    raw = expanding_square(datum, spacing, axis, vertices)
    result = {}
    for sector, name in enumerate(SECTOR_DRONES):
        minimum = np.array([low[0], bounds[sector] + SECTOR_INSET_M])
        maximum = np.array([high[0], bounds[sector + 1] - SECTOR_INSET_M])
        tracks = []
        for a, b in raw:
            start = np.array([a["north_m"], a["east_m"]])
            end = np.array([b["north_m"], b["east_m"]])
            along = int(np.flatnonzero(start != end)[0])
            cross = 1 - along
            if minimum[cross] <= start[cross] <= maximum[cross]:
                clipped_start = np.clip(start, minimum, maximum)
                clipped_end = np.clip(end, minimum, maximum)
                if clipped_start[along] != clipped_end[along]:
                    tracks.append((point(*clipped_start, 0), point(*clipped_end, 0)))
        result[name] = tracks
    return result


def ordered_candidate(grid, probability, vertices, homes, model, tracks, spacing, axis):
    assert set(tracks) == set(SECTOR_DRONES)
    centers = grid["centers"]
    assert probability.shape == (len(centers),)
    assert np.isfinite(probability).all() and ((0 <= probability) & (probability <= 1)).all()
    area_mask = inside(centers, vertices)
    detect = track_detection(model, CAPTURE_SPACING_M)
    cumulative = np.zeros(len(centers))
    routes, timeline = {}, []
    for sector, name in enumerate(SECTOR_DRONES):
        home_z = WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
        chosen, prefix = [], []
        now = 0.0
        for raw_a, raw_b in tracks[name]:
            a, b = (waypoint(p["north_m"], p["east_m"], home_z) for p in (raw_a, raw_b))
            leg_path = terrain_leg(a, b, home_z)[0]
            duration = travel_seconds(leg_path)
            if not chosen:
                ingress, _ = transit(homes[name], a, b, sector)
                waypoints = [*ingress, *leg_path]
                cost = launch_seconds(max(TRANSIT_HEIGHTS_M)) + travel_seconds(waypoints)
            else:
                current = chosen[-1]["end"]
                connector = (
                    terrain_leg(current, a, home_z)[0] if distance(current, a) > 1e-8 else [current]
                )
                extension = [*connector[1:], *leg_path[1:]]
                waypoints = [*prefix, *extension]
                cost = extension_seconds(prefix, extension)
            first = chosen[0]["start"] if chosen else a
            _, egress = transit(homes[name], first, b, sector)
            candidate_return_time = extension_seconds(waypoints, egress) + landing_seconds(
                egress[-1]["altitude_above_home_m"]
            )
            if now + cost + max(RESERVE_SECONDS, candidate_return_time) > AIRBORNE_SECONDS:
                break
            start_time = now + cost - duration
            assert start_time >= now - 1e-8, "Terrain connector produced overlapping search legs"
            now += cost
            prefix = waypoints
            return_time = candidate_return_time
            ids, pod = track_pod(centers, a, b, model, area_mask, detect)
            cumulative[ids] = np.maximum(cumulative[ids], pod)
            chosen.append({"start": a, "end": b, "starts_s": start_time, "ends_s": now})
            timeline.append({"drone_id": name, **chosen[-1]})
        if not chosen:
            return None
        ingress, egress = transit(homes[name], chosen[0]["start"], chosen[-1]["end"], sector)
        routes[name] = {
            "legs": chosen,
            "ingress": ingress,
            "egress": egress,
            "search_ends_s": now,
            "return_seconds": return_time,
            "airborne_seconds": now + return_time,
            "reserve_seconds": max(RESERVE_SECONDS, return_time),
        }
        assert now + max(RESERVE_SECONDS, return_time) <= AIRBORNE_SECONDS
    pos = float(probability @ cumulative)
    duration = max(route["airborne_seconds"] for route in routes.values())
    return {
        "spacing_m": spacing,
        "axis": axis,
        "prioritized": False,
        "routes": routes,
        "pos": pos,
        "duration_s": duration,
        "pos_per_minute": pos / (duration / 60),
        "timeline": timeline,
    }
