"""One operator-visible plan, mechanically compiled to checked Copter missions."""

import copy
import json
import math
import struct

from jsonschema import Draft202012Validator
from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.gcs.battery import (
    CUTOFF_PERCENT,
    LOW_VOLTAGE_V,
    MAX_CURRENT_A,
    VOLTAGE_FLOOR_PERCENT,
    available_seconds,
    required_percent,
)
from mission_planner.gcs.geodesy import (
    MAX_SPEED_MPS,
    MIN_SPEED_MPS,
    NAMES,
    ORIGIN,
    point_in_polygon,
    segment_in_polygon,
    to_geodetic,
    to_local,
)
from mission_planner.gcs.validation import (
    RouteConflict,
    fence_polygon,
    validate_ground_column,
    validate_position,
    validate_route,
    validate_separation,
)
from mission_planner.search.terrain import terrain_leg, waypoint
from mission_planner.search.timing import (
    PARAMETERS,
    landing_seconds,
    launch_seconds,
    motion_seconds,
)
from world.cosys.landmarks import _intersects
from world.cosys.terrain import terrain_height


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


NUMBER = {"type": "number"}
POINT = object_schema({"north_m": NUMBER, "east_m": NUMBER})
HEIGHT = {"type": "number", "exclusiveMinimum": 0, "maximum": 150}
SPEED = {"type": "number", "minimum": MIN_SPEED_MPS, "maximum": MAX_SPEED_MPS}
TRAVEL = {"height_m": HEIGHT, "speed_mps": SPEED}
SEARCH_SCHEMA = object_schema(
    {
        "perimeter": {"type": "array", "items": POINT, "minItems": 4, "maxItems": 4},
        "detector_prompt": {"type": "string", "minLength": 1, "maxLength": 100},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "look_for": object_schema(
            {
                "objective": {"type": "string", "minLength": 1, "maxLength": 200},
                "target": {"type": "string", "minLength": 1, "maxLength": 200},
                "attributes": {
                    "type": "array",
                    "maxItems": 8,
                    "items": object_schema(
                        {
                            "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$"},
                            "text": {"type": "string", "minLength": 1, "maxLength": 120},
                        }
                    ),
                },
                "ignore": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                },
            }
        ),
        "stand_off_m": {"type": "number", "exclusiveMinimum": 0},
        "zoom": {"type": "number", "minimum": 1, "maximum": 4},
        "look_ahead_m": {"type": "number", "exclusiveMinimum": 0},
        "speed_mps": SPEED,
        "max_lock_seconds": {"type": "number", "exclusiveMinimum": 0},
    }
)
STEP_FIELDS = {
    "takeoff": {"height_m": HEIGHT},
    "fly": {**POINT["properties"], **TRAVEL},
    "hover": {"seconds": {"type": ["integer", "null"], "minimum": 1, "maximum": 65535}},
    "circle": {
        **POINT["properties"],
        "radius_m": {"type": "number", "minimum": 3.5},
        **TRAVEL,
        "turns": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
        "clockwise": {"type": "boolean"},
    },
    "survey": {
        "polygon": {"type": "array", "items": POINT, "minItems": 3, "maxItems": 100},
        "spacing_m": {"type": "number", "exclusiveMinimum": 0},
        "bearing_deg": {"type": "number", "minimum": 0, "exclusiveMaximum": 360},
        **TRAVEL,
    },
    "follow": {
        "points": {"type": "array", "items": POINT, "minItems": 2, "maxItems": 1000},
        **TRAVEL,
    },
    "land": {},
    "return_home": TRAVEL,
}
PLAN_SCHEMA = object_schema(
    {
        "version": {"type": "integer", "minimum": 1},
        "drones": {
            "type": "array",
            "minItems": 1,
            "maxItems": len(NAMES),
            "items": object_schema(
                {
                    "drone_id": {"type": "string", "enum": list(NAMES)},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "anyOf": [
                                object_schema(
                                    {"kind": {"type": "string", "enum": [kind]}, **fields}
                                )
                                for kind, fields in STEP_FIELDS.items()
                            ]
                        },
                    },
                }
            ),
        },
    }
)
PLAN_SCHEMA["properties"]["search"] = SEARCH_SCHEMA


def rectangle_bounds(points, label):
    north = [point["north_m"] for point in points]
    east = [point["east_m"] for point in points]
    low_n, high_n, low_e, high_e = min(north), max(north), min(east), max(east)
    assert low_n < high_n and low_e < high_e, f"{label}: rectangle has no area"
    assert len(points) == 4 and set(zip(north, east)) == {
        (n, e) for n in (low_n, high_n) for e in (low_e, high_e)
    }, f"{label}: requires four axis-aligned rectangle corners"
    assert all(
        (a["north_m"] == b["north_m"]) != (a["east_m"] == b["east_m"])
        for a, b in zip(points, [*points[1:], points[0]])
    ), f"{label}: rectangle corners must follow the perimeter"
    return low_n, high_n, low_e, high_e


def validate_search(plan, fences, *, initial_search=True):
    assert type(initial_search) is bool
    if "search" not in plan:
        return
    search = plan["search"]
    assert search["detector_prompt"].strip(), "Search detector prompt is empty"
    brief = search["look_for"]
    assert all(text.strip() for text in [brief["objective"], brief["target"], *brief["ignore"]]), (
        "Search reviewer brief contains empty text"
    )
    attributes = brief["attributes"]
    assert len({item["id"] for item in attributes}) == len(attributes), (
        "Search reviewer attribute ids must be unique"
    )
    assert all(item["text"].strip() for item in attributes), "Search reviewer attribute is empty"
    if initial_search:
        assert {branch["drone_id"] for branch in plan["drones"]} == set(NAMES), (
            "Initial search requires an assignment for all four drones"
        )
    perimeter = search["perimeter"]
    outer = rectangle_bounds(perimeter, "Search perimeter")
    sectors = []
    for branch in plan["drones"]:
        name = branch["drone_id"]
        surveys = [
            (index, step) for index, step in enumerate(branch["steps"]) if step["kind"] == "survey"
        ]
        if initial_search:
            assert len(surveys) == 1, f"{name}: initial search requires exactly one survey sector"
        if not surveys:
            continue
        for index, survey in surveys:
            label = f"{name} step {index} search sector"
            # A 90-degree nadir view is 2h/zoom wide; retain one-third nominal overlap.
            maximum_spacing = 4 * survey["height_m"] / (3 * search["zoom"])
            assert survey["spacing_m"] <= maximum_spacing, (
                f"{label}: spacing {survey['spacing_m']:g} m exceeds {maximum_spacing:g} m "
                f"at {survey['height_m']:g} m AGL and {search['zoom']:g}x zoom "
                "for one-third nominal camera overlap"
            )
            sector = rectangle_bounds(survey["polygon"], label)
            assert all(outer[i] <= sector[i] for i in (0, 2)) and all(
                sector[i] <= outer[i] for i in (1, 3)
            ), f"{label} is outside the perimeter"
            for other_name, other in sectors:
                assert min(sector[1], other[1]) <= max(sector[0], other[0]) or min(
                    sector[3], other[3]
                ) <= max(sector[2], other[2]), f"{label} overlaps {other_name}"
            sectors.append((label, sector))
        fence = fence_polygon(fences[name])
        assert all(
            segment_in_polygon(a, b, fence)
            for a, b in zip(perimeter, [*perimeter[1:], perimeter[0]])
        ), f"{name}: search perimeter exits the fence"
    if initial_search:
        area = sum((b - a) * (d - c) for _, (a, b, c, d) in sectors)
        assert math.isclose(area, (outer[1] - outer[0]) * (outer[3] - outer[2]), rel_tol=1e-12), (
            "Search sectors do not cover the complete perimeter"
        )


def mission_item(point, altitude, command, param1=0):
    assert math.isfinite(altitude), "Mission altitude must be finite metres"
    latitude, longitude = to_geodetic(point["north_m"], point["east_m"])
    return {
        "frame": MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        "command": command,
        "param1": param1,
        "param2": 0,
        "param3": 1 if command == MAV.MAV_CMD_NAV_LOITER_TIME else 0,
        "param4": 1 if command == MAV.MAV_CMD_NAV_LAND else 0,
        "x": round(latitude * 1e7),
        "y": round(longitude * 1e7),
        "z": altitude,
    }


def mission_home(home):
    return {
        **mission_item(home, home["altitude_msl_m"], MAV.MAV_CMD_NAV_WAYPOINT),
        "frame": MAV.MAV_FRAME_GLOBAL,
    }


def float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def mission_readback(item, home=False):
    expected = dict(item)
    for key in ("param1", "param2", "param3", "param4", "z"):
        expected[key] = float32(item[key])
    if item["command"] == MAV.MAV_CMD_DO_CHANGE_SPEED:
        assert not home and item["frame"] == MAV.MAV_FRAME_GLOBAL
        return expected
    assert item["command"] in (
        MAV.MAV_CMD_NAV_WAYPOINT,
        MAV.MAV_CMD_NAV_LOITER_UNLIM,
        MAV.MAV_CMD_NAV_LOITER_TIME,
        MAV.MAV_CMD_NAV_TAKEOFF,
        MAV.MAV_CMD_NAV_LAND,
    ), "Unsupported mission readback command"
    assert item["frame"] in (
        (MAV.MAV_FRAME_GLOBAL,)
        if home
        else (MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT, MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT)
    ), "Unexpected altitude reference frame"
    # AP_Mission.cpp stores float32 metres as integer cm; item zero comes directly from AHRS.
    centimetres = round(item["z"] * 100) if home else int(float32(expected["z"] * 100))
    expected["z"] = float32(centimetres * float32(0.01))
    expected["frame"] = MAV.MAV_FRAME_GLOBAL if home else MAV.MAV_FRAME_GLOBAL_RELATIVE_ALT
    return expected


def mission_point(item):
    stored = mission_readback(item)
    north, east = to_local(stored["x"] / 1e7, stored["y"] / 1e7)
    return {"north_m": north, "east_m": east, "altitude_above_home_m": stored["z"]}


def survey_points(step):
    angle = math.radians(step["bearing_deg"])
    along, cross = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
    polygon = [
        (
            p["north_m"] * along[0] + p["east_m"] * along[1],
            p["north_m"] * cross[0] + p["east_m"] * cross[1],
        )
        for p in step["polygon"]
    ]
    low, high = min(p[1] for p in polygon), max(p[1] for p in polygon)
    assert len(set(polygon)) == len(polygon), "Survey vertices must be unique; closure is implicit"
    edges = list(zip(polygon, [*polygon[1:], polygon[0]]))
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    assert high > low and abs(area) > 1e-6, "Survey polygon has no area"
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j > i + 1 and (i, j) != (0, len(edges) - 1):
                assert not _intersects(a, b, c, d), "Survey polygon self-intersects"
    count = math.ceil((high - low) / step["spacing_m"])
    assert count <= 1000, "Survey exceeds 1000 scan lines"
    result = []
    for row in range(count):
        offset = low + min(step["spacing_m"], high - low) / 2 + row * step["spacing_m"]
        if offset >= high:
            break
        cuts = sorted(
            a[0] + (b[0] - a[0]) * (offset - a[1]) / (b[1] - a[1])
            for a, b in edges
            if (a[1] <= offset < b[1]) or (b[1] <= offset < a[1])
        )
        assert len(cuts) % 2 == 0, "Survey polygon has unmatched intersections"
        if row % 2:
            cuts.reverse()
        result.extend(
            {
                "north_m": value * along[0] + offset * cross[0],
                "east_m": value * along[1] + offset * cross[1],
            }
            for value in cuts
        )
    assert result, "Survey has no scan lines"
    return result


def movement_seconds(a, b, speed):
    horizontal = math.hypot(a["north_m"] - b["north_m"], a["east_m"] - b["east_m"])
    vertical = b["altitude_above_home_m"] - a["altitude_above_home_m"]
    return max(
        motion_seconds(horizontal, speed, PARAMETERS["WP_ACC"], PARAMETERS["WP_JERK"]),
        motion_seconds(
            abs(vertical),
            PARAMETERS["WP_SPD_UP"] if vertical > 0 else PARAMETERS["WP_SPD_DN"],
            PARAMETERS["WP_ACC_Z"],
            PARAMETERS["WP_JERK"],
        ),
    )


def compile_plan(plan, states, fences, landmarks, reservations=(), *, initial_search=True):
    Draft202012Validator(PLAN_SCHEMA).validate(plan)
    json.dumps(plan, allow_nan=False)
    names = [branch["drone_id"] for branch in plan["drones"]]
    assert len(set(names)) == len(names), "A drone appears twice in the plan"
    validate_search(plan, fences, initial_search=initial_search)
    current = {state["id"]: state for state in states}
    assert set(current) == set(NAMES), "Live state must contain all four drones"
    missions = {}
    for branch in plan["drones"]:
        name, steps = branch["drone_id"], branch["steps"]
        state = current[name]
        assert not state["link_lost"], f"{name}: link lost"
        assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{name}: GPS/EKF unhealthy"
        assert state["battery_percent"] is not None, f"{name}: battery telemetry unavailable"
        assert 0 <= state["battery_percent"] <= 100, f"{name}: battery outside 0–100%"
        validate_position(name, state, fences[name])
        fence = fence_polygon(fences[name])
        airborne = state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR
        landed = not state["armed"] and state["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND
        assert airborne or landed, f"{name}: aircraft is neither ready on ground nor airborne"
        assert (steps[0]["kind"] == "takeoff") == landed, (
            f"{name}: takeoff is required only for a landed drone"
        )
        assert steps[-1]["kind"] in ("land", "return_home") or (
            steps[-1]["kind"] == "hover" and steps[-1]["seconds"] is None
        ), f"{name}: specify the aftermath with land, return_home or indefinite hover"
        home_z = state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
        route = [{key: state[key] for key in ("north_m", "east_m", "altitude_above_home_m")}]
        items = [mission_home(state["home"])]
        step_ranges = []
        finite_time = 0.0
        indefinite = False
        if airborne:
            items.append(
                mission_item(route[0], route[0]["altitude_above_home_m"], MAV.MAV_CMD_NAV_WAYPOINT)
            )
            route.append(mission_point(items[-1]))

        def travel(target, height, speed):
            start = route[-1]
            assert point_in_polygon(target, fence), (
                f"Destination N {target['north_m']:.3f}, E {target['east_m']:.3f} "
                "is outside the world fence. Transit may leave the search perimeter, "
                "but its destination must remain inside the world fence."
            )
            assert segment_in_polygon(start, target, fence), (
                f"Transit from N {start['north_m']:.3f}, E {start['east_m']:.3f} to "
                f"N {target['north_m']:.3f}, E {target['east_m']:.3f} exits the world fence. "
                "Transit may leave the search perimeter, but the southern launch apron "
                "connects to the world only through the read-back launch corridor. "
                "Specify an intermediate fly point inside that corridor."
            )
            level = waypoint(start["north_m"], start["east_m"], home_z, height)
            path = [level]
            if (start["north_m"], start["east_m"]) != (target["north_m"], target["east_m"]):
                leg, _ = terrain_leg(start, target, home_z, height)
                path.extend(leg[1:])
            duration = 0.0
            for point in path:
                item = mission_item(point, point["altitude_above_home_m"], MAV.MAV_CMD_NAV_WAYPOINT)
                encoded = mission_point(item)
                if encoded == route[-1]:
                    continue
                duration += movement_seconds(route[-1], encoded, speed)
                route.append(encoded)
                items.append(item)
            return duration

        def land():
            point = route[-1]
            item = mission_item(point, 0, MAV.MAV_CMD_NAV_LAND)
            point = {**mission_point(item), "altitude_above_home_m": point["altitude_above_home_m"]}
            route.append(point)
            ground = terrain_height(point["north_m"], point["east_m"]) - home_z
            duration = landing_seconds(point["altitude_above_home_m"] - ground)
            route.append({**point, "altitude_above_home_m": ground})
            items.append(item)
            return duration

        for index, step in enumerate(steps):
            first = 1 if index == 0 else len(items)
            first_route_segment = 0 if index == 0 else len(route) - 1
            duration = 0.0
            kind = step["kind"]
            assert not indefinite, f"{name} step {index}: a step follows indefinite hover"
            assert kind != "return_home" or index == len(steps) - 1, (
                f"{name} step {index}: return_home returns home and lands; it must be the last step. "
                "Remove the following steps."
            )
            assert kind != "land" or index == len(steps) - 1, (
                f"{name} step {index}: landing must end the mission"
            )
            try:
                if kind == "takeoff":
                    assert index == 0 and landed, "Takeoff must be the first step from the ground"
                    target = waypoint(
                        route[-1]["north_m"], route[-1]["east_m"], home_z, step["height_m"]
                    )
                    items.append(
                        mission_item(
                            target, target["altitude_above_home_m"], MAV.MAV_CMD_NAV_TAKEOFF
                        )
                    )
                    route.append(
                        {**target, "altitude_above_home_m": mission_readback(items[-1])["z"]}
                    )
                    duration = launch_seconds(
                        target["altitude_above_home_m"] - route[0]["altitude_above_home_m"]
                    )
                elif kind == "hover":
                    indefinite = step["seconds"] is None
                    point = route[-1]
                    items.append(
                        mission_item(
                            point,
                            point["altitude_above_home_m"],
                            MAV.MAV_CMD_NAV_LOITER_UNLIM
                            if indefinite
                            else MAV.MAV_CMD_NAV_LOITER_TIME,
                            0 if indefinite else step["seconds"],
                        )
                    )
                    encoded = mission_point(items[-1])
                    if encoded != route[-1]:
                        route.append(encoded)
                    duration = 0 if indefinite else step["seconds"]
                elif kind == "land":
                    duration = land()
                else:
                    speed = step["speed_mps"]
                    items.append(
                        {
                            "frame": MAV.MAV_FRAME_GLOBAL,
                            "command": MAV.MAV_CMD_DO_CHANGE_SPEED,
                            "param1": 1,
                            "param2": speed,
                            "param3": -1,
                            "param4": 0,
                            "x": 0,
                            "y": 0,
                            "z": 0,
                        }
                    )
                    if kind == "fly":
                        targets = [step]
                    elif kind == "follow":
                        targets = step["points"]
                    elif kind == "survey":
                        targets = survey_points(step)
                    elif kind == "circle":
                        start = math.atan2(
                            route[-1]["east_m"] - step["east_m"],
                            route[-1]["north_m"] - step["north_m"],
                        )
                        sweep = math.tau * step["turns"] * (1 if step["clockwise"] else -1)
                        count = math.ceil(
                            abs(sweep) / (2 * math.acos(1 - min(0.25 / step["radius_m"], 1)))
                        )
                        assert count <= 10000, "Circle exceeds 10000 segments"
                        targets = [
                            {
                                "north_m": step["north_m"]
                                + step["radius_m"] * math.cos(start + sweep * i / count),
                                "east_m": step["east_m"]
                                + step["radius_m"] * math.sin(start + sweep * i / count),
                            }
                            for i in range(count + 1)
                        ]
                    else:
                        assert kind == "return_home"
                        targets = [state["home"]]
                    for target in targets:
                        duration += travel(target, step["height_m"], speed)
                    if kind == "return_home":
                        duration += land()
            except (AssertionError, ValueError) as error:
                raise AssertionError(f"{name} step {index} ({kind}): {error}") from error
            assert len(items) > first, f"{name} step {index}: step creates no mission items"
            step_ranges.append(
                {
                    "step_index": index,
                    "first_seq": first,
                    "last_seq": len(items) - 1,
                    "first_route_segment": first_route_segment,
                    "last_route_segment": len(route) - 2,
                    "duration_s": None if indefinite else duration,
                }
            )
            finite_time += duration
        lock_allowance = (
            plan["search"]["max_lock_seconds"]
            if "search" in plan and any(step["kind"] == "survey" for step in steps)
            else 0
        )
        required = required_percent(finite_time + lock_allowance)
        assert required <= state["battery_percent"], (
            f"{name}: battery needs {required:.2f}%, available {state['battery_percent']:g}% "
            f"for {finite_time:.1f} s planned flight and {lock_allowance:g} s candidate-lock allowance; "
            f"{CUTOFF_PERCENT:g}% charge floor, voltage requires {VOLTAGE_FLOOR_PERCENT:.2f}% "
            f"remaining at {MAX_CURRENT_A:g} A to stay above {LOW_VOLTAGE_V:g} V; no added time reserve"
        )
        missions[name] = {
            "items": items,
            "route": route,
            "step_ranges": step_ranges,
            "distance_m": sum(
                math.dist(
                    [a[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
                    [b[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
                )
                for a, b in zip(route, route[1:])
            ),
            "duration_s": None if indefinite else finite_time,
            "battery_required_percent": required,
            "available_hover_s": available_seconds(state["battery_percent"])
            - finite_time
            - lock_allowance,
        }
    receipt = {"plan": copy.deepcopy(plan), "missions": missions}
    validate_missions(receipt, states, fences, landmarks, reservations)
    return receipt


def validate_missions(receipt, states, fences, landmarks, reservations):
    plan, missions = receipt["plan"], receipt["missions"]
    current = {state["id"]: state for state in states}
    home_heights = {state["id"]: state["home"]["altitude_msl_m"] for state in states}
    routes = {}
    for branch in plan["drones"]:
        name, steps = branch["drone_id"], branch["steps"]
        state = current[name]
        assert not state["link_lost"], f"{name}: link lost"
        assert state["ekf_healthy"] and state["gps_fix"] >= 3, f"{name}: GPS/EKF unhealthy"
        validate_position(name, state, fences[name])
        route = [
            {key: state[key] for key in ("north_m", "east_m", "altitude_above_home_m")},
            *missions[name]["route"][1:],
        ]
        takeoff = steps[0]["kind"] == "takeoff"
        landing = steps[-1]["kind"] in ("land", "return_home")
        home_z = home_heights[name] - ORIGIN["altitude_msl_m"]
        if takeoff:
            validate_ground_column(route[0], landmarks)
            # Copter takes off vertically at its current position, ignoring mission latitude/longitude.
            route[1] = {
                **route[1],
                "north_m": state["north_m"],
                "east_m": state["east_m"],
            }
        if landing:
            validate_ground_column(route[-1], landmarks)
            # NAV_LAND ends at terrain, independent of the home datum used during upload.
            point = route[-1]
            route[-1] = {
                **point,
                "altitude_above_home_m": terrain_height(point["north_m"], point["east_m"]) - home_z,
            }
        validate_route(
            route[int(takeoff) : -1 if landing else None], fences[name], home_z, landmarks
        )
        routes[name] = route
    reserved = [(name, route) for name, route in reservations if name not in missions]
    reserved.extend((state["id"], [state]) for state in states if state["id"] not in missions)
    reserved.extend(routes.items())
    for name, route in routes.items():
        try:
            validate_separation(name, route, reserved, home_heights)
        except RouteConflict as error:
            conflict = dict(error.conflict)
            for prefix in ("", "other_"):
                owner = conflict[prefix + "drone_id"]
                if owner in missions:
                    index = conflict[prefix + "segment_index"]
                    step_index = next(
                        r["step_index"]
                        for r in missions[owner]["step_ranges"]
                        if r["first_route_segment"] <= index <= r["last_route_segment"]
                    )
                    steps = next(b["steps"] for b in plan["drones"] if b["drone_id"] == owner)
                    conflict[prefix + "step_index"] = step_index
                    conflict[prefix + "step_kind"] = steps[step_index]["kind"]
            raise RouteConflict(conflict) from error
