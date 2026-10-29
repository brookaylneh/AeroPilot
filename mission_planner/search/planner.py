"""Deterministic separated-track allocation by marginal expected POS per minute."""

import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

import numpy as np

from mission_planner.gcs.geodesy import NAMES, ORIGIN
from mission_planner.gcs.validation import (
    validate_ground_column,
    validate_route,
    validate_separation,
)
from mission_planner.search.detection import AGL_M, CAPTURE_SPACING_M, track_detection
from mission_planner.search.grid import inside
from mission_planner.search.occlusion import aerial_visibility
from mission_planner.search.terrain import terrain_leg
from mission_planner.search.timing import (
    PARAMETERS,
    SPEED_MPS,
    extension_seconds,
    landing_seconds,
    launch_seconds,
    travel_seconds,
)
from world.cosys.terrain import WORLD

AIRBORNE_SECONDS = 1800.0
RESERVE_SECONDS = 300.0
SPACINGS_M = (12.0, 16.0, 20.0, 24.0, 32.0, 40.0)
# The launch-column order keeps rear pads above the forward pads during ingress.
SECTOR_DRONES = ("Drone1", "Drone3", "Drone2", "Drone4")
SECTOR_INSET_M = 2.0
TRANSIT_HEIGHTS_M = (70, 78, 86, 94)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def point(n, e, h):
    return {"north_m": float(n), "east_m": float(e), "altitude_above_home_m": float(h)}


def distance(a, b):
    return math.dist(
        [a[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
        [b[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
    )


def line_segments(vertices, axis, offset, west, east):
    along, cross = ("north_m", "east_m") if axis == 0 else ("east_m", "north_m")
    cuts = []
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        if (a[cross] <= offset < b[cross]) or (b[cross] <= offset < a[cross]):
            cuts.append(
                a[along] + (b[along] - a[along]) * (offset - a[cross]) / (b[cross] - a[cross])
            )
    cuts.sort()
    assert len(cuts) % 2 == 0
    result = []
    for low, high in zip(cuts[::2], cuts[1::2]):
        if axis == 1:
            low, high = max(low, west), min(high, east)
        if high - low >= 12:
            a, b = (
                (point(low, offset, 0), point(high, offset, 0))
                if axis == 0
                else (point(offset, low, 0), point(offset, high, 0))
            )
            result.append((a, b))
    return result


def track_pod(centers, a, b, model, mask, detect):
    start = np.array([a["north_m"], a["east_m"]])
    end = np.array([b["north_m"], b["east_m"]])
    direction = end - start
    length = float(np.linalg.norm(direction))
    assert length > 0
    unit = direction / length
    radius = model["width_m"] / 2
    low, high = np.minimum(start, end) - radius, np.maximum(start, end) + radius
    ids = np.flatnonzero(
        mask
        & (centers[:, 0] >= low[0])
        & (centers[:, 0] <= high[0])
        & (centers[:, 1] >= low[1])
        & (centers[:, 1] <= high[1])
    )
    delta = centers[ids] - start
    along = delta[:, 0] * unit[0] + delta[:, 1] * unit[1]
    lateral = delta[:, 0] * unit[1] - delta[:, 1] * unit[0]
    eligible = (abs(lateral) <= radius) & (along >= 0) & (along <= length)
    return ids[eligible], detect(lateral[eligible], along[eligible], length)


def transit(home, first, last, sector):
    h = TRANSIT_HEIGHTS_M[sector]
    row = -795 + 12 * sector
    launch = point(home["north_m"], home["east_m"], h)
    ingress = [
        launch,
        point(row, home["east_m"], h),
        point(row, first["east_m"], h),
        point(first["north_m"], first["east_m"], h),
    ]
    egress = [
        point(last["north_m"], last["east_m"], h),
        point(row, last["east_m"], h),
        point(row, home["east_m"], h),
        launch,
    ]
    return ingress, egress


def candidate(
    grid, probability, vertices, homes, model, spacing, axis, prioritized, path=terrain_leg
):
    centers = grid["centers"]
    area_mask = inside(centers, vertices)
    detect = track_detection(model, CAPTURE_SPACING_M)
    west = min(p["east_m"] for p in vertices)
    east = max(p["east_m"] for p in vertices)
    north_min = min(p["north_m"] for p in vertices)
    north_max = max(p["north_m"] for p in vertices)
    bounds = np.linspace(west, east, 5)
    routes = {}
    cumulative = np.zeros(len(centers))
    timeline = []
    for sector, name in enumerate(SECTOR_DRONES):
        low, high = bounds[sector] + SECTOR_INSET_M, bounds[sector + 1] - SECTOR_INSET_M
        offsets = (
            np.arange(low + spacing / 2, high, spacing)
            if axis == 0
            else np.arange(north_min + spacing / 2, north_max, spacing)
        )
        legs = [
            leg
            for offset in offsets
            for leg in line_segments(vertices, axis, float(offset), low, high)
        ]
        if not legs:
            return None
        z = WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
        from mission_planner.search.terrain import waypoint

        legs = [
            (waypoint(a["north_m"], a["east_m"], z), waypoint(b["north_m"], b["east_m"], z))
            for a, b in legs
        ]
        pods = [
            (
                track_pod(centers, a, b, model, area_mask, detect),
                track_pod(centers, b, a, model, area_mask, detect),
            )
            for a, b in legs
        ]
        chosen = []
        prefix = []
        now = 0.0
        current = None
        first = None
        while legs:
            ranked = []
            for i, (a, b) in enumerate(legs):
                reverse = current is not None and distance(current, b) < distance(current, a)
                if reverse:
                    a, b = b, a
                ids, pod = pods[i][int(reverse)]
                gain = float(probability[ids] @ np.maximum(pod - cumulative[ids], 0))
                lower = (
                    math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"]) / SPEED_MPS
                )
                if current is not None:
                    lower += (
                        math.hypot(
                            current["north_m"] - a["north_m"], current["east_m"] - a["east_m"]
                        )
                        / SPEED_MPS
                    )
                else:
                    ingress, _ = transit(homes[name], a, b, sector)
                    lower += launch_seconds(max(TRANSIT_HEIGHTS_M)) + travel_seconds(ingress)
                    lower += abs(
                        ingress[-1]["altitude_above_home_m"] - a["altitude_above_home_m"]
                    ) / max(PARAMETERS["WP_SPD_UP"], PARAMETERS["WP_SPD_DN"])
                # Each incoming blend removes at most that segment's acceleration ramp.
                ranked.append(
                    (gain / lower if prioritized else -i, -i, i, a, b, reverse, gain, lower)
                )
            best = None
            for upper, tie, i, a, b, reverse, gain, lower in sorted(
                ranked, key=lambda v: v[:2], reverse=True
            ):
                if best is not None and (upper, tie) <= best[:2]:
                    break
                if now + lower + RESERVE_SECONDS > AIRBORNE_SECONDS:
                    continue
                leg_path = path(a, b, z)[0]
                duration = travel_seconds(leg_path)
                if current is None:
                    ingress, _ = transit(homes[name], a, b, sector)
                    waypoints = [*ingress, *leg_path]
                    cost = launch_seconds(max(TRANSIT_HEIGHTS_M)) + travel_seconds(waypoints)
                else:
                    connector = path(current, a, z)[0] if distance(current, a) > 1e-8 else [current]
                    extension = [*connector[1:], *leg_path[1:]]
                    waypoints = [*prefix, *extension]
                    cost = extension_seconds(prefix, extension)
                assert cost >= lower - 1e-8, "Cruise lower bound exceeds terrain travel time"
                _, egress = transit(homes[name], first if first is not None else a, b, sector)
                candidate_return_time = extension_seconds(waypoints, egress) + landing_seconds(
                    egress[-1]["altitude_above_home_m"]
                )
                if now + cost + max(RESERVE_SECONDS, candidate_return_time) <= AIRBORNE_SECONDS:
                    option = (
                        gain / cost if prioritized else -i,
                        -i,
                        i,
                        a,
                        b,
                        cost,
                        candidate_return_time,
                        duration,
                        waypoints,
                        pods[i][int(reverse)],
                    )
                    if best is None or option[:2] > best[:2]:
                        best = option
            if best is None:
                break
            _, _, i, a, b, cost, return_time, duration, prefix, selected_pod = best
            if first is None:
                first = a
            start_time = now + cost - duration
            assert start_time >= now - 1e-8, "Terrain connector produced overlapping search legs"
            now += cost
            ids, pod = selected_pod
            cumulative[ids] = np.maximum(cumulative[ids], pod)
            chosen.append({"start": a, "end": b, "starts_s": start_time, "ends_s": now})
            timeline.append({"drone_id": name, **chosen[-1]})
            current = b
            legs.pop(i)
            pods.pop(i)
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
    duration = max(r["airborne_seconds"] for r in routes.values())
    return {
        "spacing_m": spacing,
        "axis": axis,
        "prioritized": prioritized,
        "routes": routes,
        "pos": pos,
        "duration_s": duration,
        "pos_per_minute": pos / (duration / 60),
        "timeline": timeline,
    }


def validate_batch(routes, states, readbacks, landmarks):
    reservations = [
        (name, [states[name], *route, point(states[name]["north_m"], states[name]["east_m"], 0)])
        for name, route in routes.items()
    ]
    for name, route in routes.items():
        validate_ground_column(states[name], landmarks)
        validate_ground_column(states[name]["home"], landmarks)
        validate_route(
            route,
            readbacks[name],
            states[name]["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"],
            landmarks,
        )
        validate_separation(
            name,
            [states[name], *route, point(states[name]["north_m"], states[name]["east_m"], 0)],
            reservations,
        )
        validate_separation(name, route, [(other, [state]) for other, state in states.items()])


def materialize(result, states, readbacks, landmarks, path=terrain_leg):
    routes = {}
    max_error = 0.0
    for name, spec in result["routes"].items():
        z = WORLD["vehicles"][name]["spawnUeCm"]["z"] / 100
        route = list(spec["ingress"])
        first = spec["legs"][0]["start"]
        route.append(first)
        for leg in spec["legs"]:
            if distance(route[-1], leg["start"]) > 1e-8:
                points, error = path(route[-1], leg["start"], z)
                route.extend(points[1:])
                max_error = max(max_error, error)
            points, error = path(leg["start"], leg["end"], z)
            assert math.isclose(
                leg["ends_s"] - leg["starts_s"], travel_seconds(points), rel_tol=0, abs_tol=1e-8
            ), "Planned search-leg timing differs from materialized geometry"
            route.extend(points[1:])
            max_error = max(max_error, error)
        route.extend(spec["egress"])
        routes[name] = [
            p for i, p in enumerate(route) if i == 0 or distance(p, route[i - 1]) > 1e-8
        ]
        spec["waypoints"] = routes[name]
        spec["length_m"] = sum(distance(a, b) for a, b in zip(routes[name], routes[name][1:]))
        spec["speed_mps"] = SPEED_MPS
        expected_airborne = (
            launch_seconds(max(TRANSIT_HEIGHTS_M))
            + travel_seconds(routes[name])
            + landing_seconds(routes[name][-1]["altitude_above_home_m"])
        )
        assert math.isclose(spec["airborne_seconds"], expected_airborne, rel_tol=0, abs_tol=1e-8), (
            "Planned airborne timing differs from materialized geometry"
        )
    validate_batch(routes, states, readbacks, landmarks)
    result["max_sampled_agl_error_m"] = max_error
    return result


def curve(result, grid, probability, vertices, model):
    mask = inside(grid["centers"], vertices)
    detect = track_detection(model, CAPTURE_SPACING_M)
    times = sorted(
        {
            0.0,
            300.0,
            600.0,
            900.0,
            1200.0,
            1800.0,
            result["duration_s"],
            *[leg["ends_s"] for leg in result["timeline"]],
        }
    )
    values = []
    completed = np.zeros(len(probability))
    finished = set()
    for time in times:
        active = []
        for index, leg in enumerate(result["timeline"]):
            if index in finished:
                continue
            if time <= leg["starts_s"]:
                continue
            if time >= leg["ends_s"]:
                ids, probabilities = track_pod(
                    grid["centers"], leg["start"], leg["end"], model, mask, detect
                )
                completed[ids] = np.maximum(completed[ids], probabilities)
                finished.add(index)
            else:
                active.append(leg)
        pod = completed.copy()
        for leg in active:
            fraction = min(1.0, (time - leg["starts_s"]) / (leg["ends_s"] - leg["starts_s"]))
            end = {
                k: leg["start"][k] + fraction * (leg["end"][k] - leg["start"][k])
                for k in leg["start"]
            }
            ids, probabilities = track_pod(grid["centers"], leg["start"], end, model, mask, detect)
            pod[ids] = np.maximum(pod[ids], probabilities)
        values.append({"seconds": time, "pos": float(probability @ pod)})
    return values


def plan(grid, probability, vertices, states, readbacks, model, inputs, landmarks):
    assert landmarks is not None, "Landmark catalogue required for obstacle clearance"
    assert model["agl_m"] == AGL_M, "No calibration for requested height above ground"
    assert tuple(sorted(states)) == NAMES
    assert all(
        not s["armed"] and s["landed_state"] == 1 and not s["link_lost"] and s["ekf_healthy"]
        for s in states.values()
    ), "Planning requires healthy landed fleet"
    homes = {n: s["home"] for n, s in states.items()}
    probability = probability * aerial_visibility(grid, landmarks)
    paths = {}

    def cached_path(a, b, home_z):
        key = (a["north_m"], a["east_m"], b["north_m"], b["east_m"], home_z)
        if key not in paths:
            paths[key] = terrain_leg(a, b, home_z)
        return paths[key]

    candidates = []
    alternatives = []
    choices = [(spacing, axis) for spacing in SPACINGS_M for axis in (0, 1)]
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        pending = [
            pool.submit(candidate, grid, probability, vertices, homes, model, spacing, axis, True)
            for spacing, axis in choices
        ]
        values = [future.result() for future in pending]
    for (spacing, axis), value in zip(choices, values):
        if value is None:
            alternatives.append(
                {
                    "spacing_m": spacing,
                    "axis": axis,
                    "status": "no four-drone allocation within endurance",
                }
            )
        else:
            alternatives.append(
                {k: value[k] for k in ("spacing_m", "axis", "pos", "duration_s", "pos_per_minute")}
            )
            candidates.append(value)
    assert candidates, "No feasible four-drone candidate"
    selected = max(
        candidates,
        key=lambda c: (c["pos_per_minute"], -c["duration_s"], -c["spacing_m"], -c["axis"]),
    )
    selected = materialize(selected, states, readbacks, landmarks, cached_path)
    baseline = candidate(
        grid,
        probability,
        vertices,
        homes,
        model,
        selected["spacing_m"],
        selected["axis"],
        False,
        cached_path,
    )
    assert baseline is not None
    baseline = materialize(baseline, states, readbacks, landmarks, cached_path)
    selected["comparison"] = {
        "label": "Conservative phase-averaged maximum-look expectation; no aerial credit in structure or forest footprints; stationary target, conditional on in-world presence",
        "planned": curve(selected, grid, probability, vertices, model),
        "uniform_lawnmower": curve(baseline, grid, probability, vertices, model),
    }
    selected["alternatives"] = alternatives
    selected["capture_spacing_m"] = CAPTURE_SPACING_M
    selected["inputs"] = inputs
    selected["provisional"] = True
    selected["plan_id"] = hashlib.sha256(encode(selected).encode()).hexdigest()[:24]
    return selected
