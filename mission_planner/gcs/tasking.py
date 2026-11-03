"""Wonder states a task; code builds, sizes and proves the flight plan."""

import copy
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from shapely import LineString, Point, Polygon, union_all

from mission_planner.gcs.allocation import (
    APPROVAL_HOVER_S,
    LANE_ORDER,
    MIN_SECTOR_S,
    allocate,
    branches,
    drone_budgets,
    fit,
    fly,
    horizontal_seconds,
    pattern_points,
    point,
    points_of,
    simplified,
    survey_sectors,
)
from mission_planner.gcs.battery import available_seconds
from mission_planner.gcs.fences import BOUNDARY_CLEARANCE_M, read_fences
from mission_planner.gcs.geodesy import MAX_SPEED_MPS, MIN_SPEED_MPS, NAMES
from mission_planner.gcs.planner import (
    PLAN_SCHEMA,
    SEARCH_SCHEMA,
    compile_branch,
    compile_plan,
    object_schema,
    validate_search,
)
from mission_planner.gcs.region import (
    DISTANCE_KM,
    SOURCES,
    bands,
    cells,
    check_direction,
    compass_degrees,
    ipp_geometry,
    placement,
    polygon_points,
    rationale,
    remaining,
    searchable_area,
)
from mission_planner.gcs.rejection import PlanRejected
from world.cosys.landmarks import COMPASS

TASKS = (
    "area_search",
    "datum_search",
    "route_search",
    "boundary_check",
    "point_inspection",
    "transit",
    "waypoint_route",
)
SUBJECT_TASKS = ("area_search", "datum_search")
CATEGORIES = ("child", "hiker", "dementia", "elderly", "unknown")
LINEAR_KINDS = ("trail", "road", "creek", "ridge")
# Strips drop slivers below one row pitch squared, so a fully assigned band measures just under 100 %.
COVERAGE_PERCENT = 99.5
SIZING_PASSES = 6
# With automatic proposals the first band takes this share of the sortie so the fleet can
# expand in flight under approval instead of returning to base first.
SORTIE_BANDS = 2
# Band areas are quantised to whole cells, so a corrected budget aims slightly under the allowance.
SIZING_MARGIN = 0.98
# Strip parts are simplified by up to 5 cm and the perimeter by up to 1 m; the perimeter is grown
# past both so every strip edge stays inside the observation area.
PERIMETER_MARGIN_M = 1.5
# Operator map clicks reach the chat as text rounded to centimetres.
CLICK_TOLERANCE_M = 0.01
# The model API documents no numeric, length or pattern keywords for strict schemas.
CONSTRAINT_KEYWORDS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
)
NUMBER = {"type": "number"}
POINT = object_schema({"north_m": NUMBER, "east_m": NUMBER})
SPEED = {"type": "number", "minimum": MIN_SPEED_MPS, "maximum": MAX_SPEED_MPS}


def nullable(schema):
    return {"anyOf": [{"type": "null"}, schema]}


SETTINGS_SCHEMA = object_schema(
    {
        "height_m": nullable({"type": "number", "exclusiveMinimum": 0, "maximum": 150}),
        "speed_mps": nullable(SPEED),
        "transit_speed_mps": nullable(SPEED),
        "spacing_m": nullable({"type": "number", "exclusiveMinimum": 0}),
        "zoom": nullable(SEARCH_SCHEMA["properties"]["zoom"]),
        "look_ahead_m": nullable({"type": "number", "exclusiveMinimum": 0}),
        "stand_off_m": nullable({"type": "number", "exclusiveMinimum": 0}),
        "max_lock_seconds": nullable({"type": "number", "exclusiveMinimum": 0}),
        "dwell_seconds": nullable({"type": "integer", "minimum": 1, "maximum": 65535}),
    }
)
TASKING_SCHEMA = object_schema(
    {
        "task": {"type": "string", "enum": list(TASKS)},
        "subject": nullable(
            object_schema(
                {
                    key: SEARCH_SCHEMA["properties"][key]
                    for key in ("detector_prompt", "threshold", "look_for")
                }
            )
        ),
        "references": object_schema(
            {
                "landmark_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 64},
                    "maxItems": 20,
                },
                "points": {"type": "array", "items": POINT, "maxItems": 20},
                "polygon": nullable(
                    {"type": "array", "items": POINT, "minItems": 3, "maxItems": 100}
                ),
                "direction_of_travel": {
                    "anyOf": [
                        {"type": "null"},
                        object_schema({"compass": {"type": "string", "enum": list(COMPASS)}}),
                        object_schema(
                            {"degrees": {"type": "number", "minimum": 0, "exclusiveMaximum": 360}}
                        ),
                    ]
                },
                "subject_category": {"type": "string", "enum": list(CATEGORIES)},
            }
        ),
        "fleet": object_schema(
            {
                "drone_ids": nullable(
                    {
                        "type": "array",
                        "items": {"type": "string", "enum": list(NAMES)},
                        "minItems": 1,
                        "maxItems": len(NAMES),
                    }
                ),
                "count": nullable({"type": "integer", "minimum": 1, "maximum": len(NAMES)}),
            }
        ),
        "settings": SETTINGS_SCHEMA,
        "aftermath": {"type": "string", "enum": ["return_home", "hover", "land"]},
        "region": object_schema(
            {
                "band_index": nullable({"type": "integer", "minimum": 1}),
                "accept_partial": {"type": "boolean"},
                "radius_m": nullable({"type": "number", "exclusiveMinimum": 0, "maximum": 3000}),
                "half_angle_deg": nullable(
                    {"type": "number", "exclusiveMinimum": 0, "maximum": 180}
                ),
            }
        ),
        "expansion": object_schema(
            {
                "automatic_proposals": {"type": "boolean"},
                "lead_seconds": nullable({"type": "number", "exclusiveMinimum": 0}),
            }
        ),
    }
)
VALIDATOR = Draft202012Validator(TASKING_SCHEMA)
SEARCH_DEFAULTS = {
    "height_m": 30,
    "speed_mps": 5,
    "transit_speed_mps": 10,
    "spacing_m": 20,
    "zoom": 1,
    "look_ahead_m": 20,
    "stand_off_m": 20,
    "max_lock_seconds": 180,
}
MOVE_DEFAULTS = {"height_m": 40, "speed_mps": 10, "transit_speed_mps": 10, "dwell_seconds": None}
# A task's defaults list its applicable settings; a None default is optional without a default.
TASK_DEFAULTS = {
    "area_search": SEARCH_DEFAULTS,
    "datum_search": SEARCH_DEFAULTS,
    "route_search": SEARCH_DEFAULTS,
    "boundary_check": SEARCH_DEFAULTS,
    "point_inspection": {
        **{key: value for key, value in SEARCH_DEFAULTS.items() if key != "spacing_m"},
        "height_m": 40,
        "dwell_seconds": None,
    },
    "transit": MOVE_DEFAULTS,
    "waypoint_route": MOVE_DEFAULTS,
}


def meta_schema():
    """The tasking schema for the model API with strict-mode keywords only; validate_tasking
    enforces the constraints locally."""

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in CONSTRAINT_KEYWORDS}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(TASKING_SCHEMA)


def require(condition, message, **detail):
    if not condition:
        raise PlanRejected("schema", message, detail=detail)


def validate_tasking(tasking, landmarks, operator_points):
    """The tasking with defaults filled, or a structured rejection naming the offending field."""
    error = best_match(VALIDATOR.iter_errors(tasking))
    if error is not None:
        location = ".".join(["tasking", *(str(part) for part in error.absolute_path)])
        raise PlanRejected(
            "schema",
            f"{location}: {error.message}",
            detail={"path": [str(part) for part in error.absolute_path]},
        )
    json.dumps(tasking, allow_nan=False)
    task = tasking["task"]
    defaults = TASK_DEFAULTS[task]
    settings = {}
    for key, value in tasking["settings"].items():
        require(
            value is None or key in defaults,
            f"settings.{key} does not apply to {task}",
            setting=key,
            task=task,
        )
        settings[key] = value if value is not None or key not in defaults else defaults[key]
    result = {**copy.deepcopy(tasking), "settings": settings}
    require(
        task not in SUBJECT_TASKS or result["subject"] is not None,
        f"{task} needs a subject: detector_prompt, threshold and look_for",
        task=task,
    )
    require(
        task not in ("transit", "waypoint_route") or result["subject"] is None,
        f"{task} flies without observation; omit the subject or choose point_inspection",
        task=task,
    )
    fleet = result["fleet"]
    require(
        fleet["drone_ids"] is None or fleet["count"] is None,
        "Name the aircraft in fleet.drone_ids or give fleet.count, not both",
    )
    require(
        fleet["drone_ids"] is None or len(set(fleet["drone_ids"])) == len(fleet["drone_ids"]),
        "fleet.drone_ids repeats an aircraft",
    )
    references = result["references"]
    known = {landmark["id"]: landmark for landmark in landmarks}
    for identifier in references["landmark_ids"]:
        if identifier not in known:
            raise PlanRejected(
                "reference",
                f"Unknown landmark id {identifier!r}; find_place returns catalogue ids",
                detail={"landmark_id": identifier},
            )
    for clicked in references["points"]:
        if not any(
            math.dist((clicked["north_m"], clicked["east_m"]), (given["north_m"], given["east_m"]))
            <= CLICK_TOLERANCE_M
            for given in operator_points
        ):
            raise PlanRejected(
                "reference",
                f"Point N {clicked['north_m']:.2f}, E {clicked['east_m']:.2f} was not clicked by "
                "the operator; ask them to choose it on the map",
                detail={"point": clicked, "clicked": list(operator_points)},
            )
    sources = {
        "landmarks": len(references["landmark_ids"]),
        "points": len(references["points"]),
        "polygon": int(references["polygon"] is not None),
    }
    used = [key for key, count in sources.items() if count]
    region = result["region"]
    directed = references["direction_of_travel"] is not None
    if task in ("area_search", "datum_search"):
        require(
            len(used) == 1
            and sources["landmarks"] <= 1
            and sources["points"] <= 1
            and (task == "area_search" or not sources["polygon"]),
            f"{task} needs exactly one initial planning point: one landmark id or one clicked "
            "point" + (", or one operator polygon" if task == "area_search" else ""),
            sources=sources,
        )
        if task == "datum_search":
            require(
                region["radius_m"] is not None,
                "datum_search needs region.radius_m, the radius searched around the datum",
            )
            require(
                not directed and region["half_angle_deg"] is None,
                "datum_search searches the whole disc; omit direction_of_travel and half_angle_deg",
            )
        if sources["polygon"]:
            require(
                not directed and region["half_angle_deg"] is None and region["band_index"] is None,
                "An operator polygon is searched whole; omit direction_of_travel, half_angle_deg "
                "and band_index",
            )
    elif task == "route_search":
        require(
            used == ["landmarks"],
            "route_search follows named trails, roads, creeks or ridges; give landmark ids only",
            sources=sources,
        )
        for identifier in references["landmark_ids"]:
            require(
                known[identifier]["kind"] in LINEAR_KINDS,
                f"{identifier} is a {known[identifier]['kind']}, not a linear feature to follow",
                landmark_id=identifier,
            )
    elif task == "boundary_check":
        require(
            len(used) == 1 and "points" not in used,
            "boundary_check follows polygon landmark outlines or one operator polygon",
            sources=sources,
        )
        for identifier in references["landmark_ids"]:
            require(
                known[identifier]["geometry"]["type"] == "polygon",
                f"{identifier} has no outline to follow",
                landmark_id=identifier,
            )
    elif task in ("point_inspection", "transit"):
        require(
            len(used) == 1 and sources["landmarks"] + sources["points"] == 1,
            f"{task} needs exactly one target: a landmark id or a clicked point",
            sources=sources,
        )
    else:
        assert task == "waypoint_route", task
        require(
            len(used) == 1 and "polygon" not in used and sources[used[0]] >= 2 if used else False,
            "waypoint_route needs two or more waypoints: landmark ids or clicked points, not both",
            sources=sources,
        )
    if task != "area_search":
        require(
            region["band_index"] is None
            and region["half_angle_deg"] is None
            and not result["expansion"]["automatic_proposals"],
            "band_index, half_angle_deg and automatic_proposals apply to area_search only",
            task=task,
        )
        require(
            task == "datum_search" or region["radius_m"] is None,
            "region.radius_m applies to area_search and datum_search only",
            task=task,
        )
    require(
        region["half_angle_deg"] is None or directed,
        "half_angle_deg needs a direction_of_travel",
    )
    return result


def healthy(state):
    return (
        not state["link_lost"]
        and state["ekf_healthy"]
        and state["gps_fix"] >= 3
        and state["battery_percent"] is not None
    )


def choose_drones(tasking, current):
    """Participants in lane order: the named aircraft, else the healthy ones up to the count."""
    task, fleet = tasking["task"], tasking["fleet"]
    if fleet["drone_ids"] is not None:
        names = [name for name in LANE_ORDER if name in fleet["drone_ids"]]
    else:
        available = [name for name in LANE_ORDER if healthy(current[name])]
        if not available:
            raise PlanRejected(
                "aircraft_state",
                "No aircraft has healthy telemetry, GPS/EKF and battery",
                detail={name: healthy(current[name]) for name in NAMES},
            )
        count = len(available) if fleet["count"] is None else fleet["count"]
        if count > len(available):
            raise PlanRejected(
                "aircraft_state",
                f"{count} aircraft requested; {len(available)} are healthy: "
                + ", ".join(available),
                detail={"requested": count, "available": available},
            )
        names = available[:count]
    if task in ("point_inspection", "transit", "waypoint_route") and len(names) > 1:
        raise PlanRejected(
            "participants",
            f"{task} takes one aircraft; set fleet.count to 1 or name one drone",
            detail={"task": task, "aircraft": names},
        )
    return names


def direction_degrees(direction):
    if direction is None:
        return None, None
    if "compass" in direction:
        return compass_degrees(direction["compass"]), direction["compass"]
    degrees = direction["degrees"]
    return degrees, COMPASS[int((degrees + 22.5) // 45) % 8]


def landmark_point(landmark):
    """One representative position: the point, a polyline's midpoint or a polygon's centroid."""
    geometry = ipp_geometry(landmark)
    if geometry.geom_type == "LineString":
        geometry = geometry.interpolate(0.5, normalized=True)
    centre = geometry.centroid
    return point(centre.x, centre.y)


def targets_of(references, known):
    """Referenced positions in order: landmark ids or clicked points (validation allows one kind)."""
    if references["landmark_ids"]:
        return [landmark_point(known[identifier]) for identifier in references["landmark_ids"]]
    return [point(p["north_m"], p["east_m"]) for p in references["points"]]


def reference_name(references, known):
    if references["landmark_ids"]:
        return known[references["landmark_ids"][0]]["name"]
    first = references["points"][0]
    return f"the map point N {first['north_m']:.0f}, E {first['east_m']:.0f}"


def assemble(version, plan_drones, subject, settings, perimeter):
    plan = {"version": version, "drones": plan_drones}
    if subject is not None:
        plan["search"] = {
            **subject,
            "perimeter": perimeter,
            **{
                key: settings[key]
                for key in ("stand_off_m", "zoom", "look_ahead_m", "speed_mps", "max_lock_seconds")
            },
        }
    return plan


def clipped_area(shape, area, label):
    """shape inside the searchable area as one polygon; a split or empty result is rejected."""
    inside = shape.intersection(area)
    if inside.geom_type != "Polygon" or inside.is_empty:
        raise PlanRejected(
            "fence",
            f"{label} is split or removed by the flight fence and exclusion zones; "
            "reduce it or move it inside the fence",
            detail={"searchable_m2": inside.area, "requested_m2": shape.area},
        )
    return inside


def band_status(index, chosen, status):
    if index == chosen:
        return "planned"
    if index < chosen:
        return "previous"
    return status


def exterior_points(polygon):
    return [point(n, e) for n, e in polygon.exterior.coords[:-1]]


def perimeter_of(polygon):
    return points_of(simplified(polygon.buffer(PERIMETER_MARGIN_M, join_style="mitre")))


def band_coverage(band, sectors):
    """Coverage of the planned band by its survey polygons, in survey_coverage's shape."""
    assigned = union_all(
        [
            Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]])
            for sector in sectors
            for step in sector["steps"]
            if step["kind"] == "survey"
        ]
    ).intersection(band)
    return {
        "perimeter_area_m2": band.area,
        "survey_area_m2": assigned.area,
        "unassigned_area_m2": band.difference(assigned).area,
        "survey_percent": round(100 * assigned.area / band.area, 6),
    }


def with_continuing(sectors, continuing):
    """Unfinished rows fly first, before the drone's new strip."""
    return [
        {**sector, "continuing": continuing[sector["drone_id"]]}
        if sector["drone_id"] in continuing
        else sector
        for sector in sectors
    ]


def search_work(
    tasking,
    drones,
    states,
    current,
    settings,
    origins,
    area,
    known,
    fences,
    version,
    searched,
    continuing,
):
    """Survey sectors for a doctrine band, a datum disc or an operator polygon, sized to the fleet.

    searched polygons are removed from the doctrine region; continuing maps a drone to the
    unfinished observed steps it flies before its new strip. Airborne drones without endurance
    for a strip return home instead; landed ones are rejected.
    """
    task, references, region_spec = tasking["task"], tasking["references"], tasking["region"]
    subject, aftermath = tasking["subject"], tasking["aftermath"]
    direction_deg, direction_name = direction_degrees(references["direction_of_travel"])
    lock_s = settings["max_lock_seconds"]
    for name, steps in continuing.items():
        assert name in drones, f"{name}: continuing work for a drone outside the task"
        origins[name] = pattern_points(steps[-1])[-1]
    if references["polygon"] is not None:
        shape = Polygon([(p["north_m"], p["east_m"]) for p in references["polygon"]])
        require(shape.is_valid and shape.area > 0, "The operator polygon self-intersects")
        if not shape.within(area.buffer(CLICK_TOLERANCE_M)):
            raise PlanRejected(
                "fence",
                f"The operator polygon leaves the searchable area ({BOUNDARY_CLEARANCE_M:g} m "
                f"inside the fence and outside exclusions) by {shape.difference(area).area:.0f} m²",
                detail={"outside_m2": shape.difference(area).area},
            )
        polygon = simplified(shape, area)
        bearing, sectors = allocate(polygon, drones, origins, settings, None)
        region = {
            "kind": "operator_polygon",
            "in_flight_expansion": False,
            "ipp": None,
            "direction_deg": None,
            "category": None,
            "table": None,
            "cells_total": None,
            "budget_s": None,
            "bands": [
                {
                    "index": 1,
                    "polygon": polygon_points(polygon),
                    "area_m2": polygon.area,
                    "mass": None,
                    "status": "planned",
                }
            ],
        }
        sentences = [
            f"Stated search polygon of {polygon.area / 1e4:.1f} ha searched whole; "
            "no probability model applies."
        ]
        strips = survey_sectors(sectors, settings, bearing)
        work = branches(
            strips,
            states,
            settings,
            [known[i] for i in known],
            next_band=False,
            aftermath=aftermath,
        )
        return work, perimeter_of(polygon), region, sentences, False, band_coverage(polygon, strips)
    ipp = (
        {"landmark_id": references["landmark_ids"][0], "point": None}
        if references["landmark_ids"]
        else {"landmark_id": None, "point": references["points"][0]}
    )
    geometry = (
        ipp_geometry(known[ipp["landmark_id"]])
        if ipp["landmark_id"] is not None
        else Point(ipp["point"]["north_m"], ipp["point"]["east_m"])
    )
    name = reference_name(references, known)
    if task == "datum_search":
        radius = region_spec["radius_m"]
        disc = clipped_area(geometry.buffer(radius), area, f"The {radius:g} m disc around {name}")
        polygon = simplified(disc, area)
        bearing, sectors = allocate(polygon, drones, origins, settings, None)
        region = {
            "kind": "datum_disc",
            "in_flight_expansion": False,
            "ipp": ipp,
            "direction_deg": None,
            "category": references["subject_category"],
            "table": None,
            "cells_total": None,
            "budget_s": None,
            "bands": [
                {
                    "index": 1,
                    "polygon": polygon_points(polygon),
                    "area_m2": polygon.area,
                    "mass": None,
                    "status": "planned",
                }
            ],
        }
        outside = geometry.buffer(radius).area - disc.area
        sentences = [
            f"Disc of radius {radius:g} m around {name} ({polygon.area / 1e4:.1f} ha) with "
            "uniform density; the radius is the stated reliability of the datum."
            + (
                f" {outside / geometry.buffer(radius).area:.0%} of the disc lies outside the "
                "searchable area."
                if outside > 1
                else ""
            )
        ]
        strips = survey_sectors(sectors, settings, bearing)
        work = branches(
            strips,
            states,
            settings,
            [known[i] for i in known],
            next_band=False,
            aftermath=aftermath,
        )
        return work, perimeter_of(polygon), region, sentences, False, band_coverage(polygon, strips)
    category = references["subject_category"]
    grid = cells(
        geometry,
        direction_deg,
        category,
        region_spec["radius_m"],
        area,
        region_spec["half_angle_deg"],
    )
    if searched:
        grid = remaining(grid, searched)
    expanding = bool(searched or continuing)
    chosen = 1 if region_spec["band_index"] is None else region_spec["band_index"]
    proposals = tasking["expansion"]["automatic_proposals"]
    centre = geometry.centroid
    target = point(centre.x, centre.y)
    trip_s = max(
        2
        * horizontal_seconds(
            current[name] if current[name]["armed"] else current[name]["home"],
            target,
            settings["transit_speed_mps"],
        )
        for name in drones
    )
    in_flight = proposals
    budgets = drone_budgets(
        states, drones, settings, target, lock_s, APPROVAL_HOVER_S if proposals else 0
    )
    if proposals and not searched:
        # Half a sortie must buy more search than the round trip costs; otherwise the first band
        # takes the whole sortie and expansion resumes from the pads.
        in_flight = min(budgets.values()) / SORTIE_BANDS >= trip_s
        if in_flight:
            budgets = {name: budget / SORTIE_BANDS for name, budget in budgets.items()}
        else:
            budgets = drone_budgets(states, drones, settings, target, lock_s, 0)
    returning = [name for name in drones if budgets[name] < MIN_SECTOR_S]
    for name in returning:
        if not current[name]["armed"]:
            raise PlanRejected(
                "endurance",
                f"{name} has {budgets[name]:.0f} s of searchable endurance at this distance, below "
                f"the {MIN_SECTOR_S:g} s minimum strip"
                + (
                    f" with the sortie split into {SORTIE_BANDS} bands; set "
                    "expansion.automatic_proposals false to give one band the whole sortie, or "
                    "leave the aircraft out of the fleet"
                    if in_flight and not searched
                    else "; leave it out of the fleet"
                ),
                drone_id=name,
                detail={"budget_s": budgets[name], "minimum_s": MIN_SECTOR_S},
            )
    searching = [name for name in drones if name not in returning]
    if not searching:
        raise PlanRejected(
            "endurance",
            "No participating aircraft has endurance for a strip of the next band",
            detail={"budgets_s": budgets},
        )
    budget = min(budgets[name] for name in searching)
    rate = len(searching) * settings["spacing_m"] * settings["speed_mps"]
    landmarks = [known[i] for i in known]
    previous = None
    for _ in range(SIZING_PASSES):
        all_bands = bands(grid, budget, rate, searched)
        if chosen > len(all_bands):
            raise PlanRejected(
                "reference",
                f"The region has {len(all_bands)} bands at this budget; band {chosen} does not "
                "exist",
                detail={"bands": len(all_bands), "band_index": chosen},
            )
        band = all_bands[chosen - 1]
        polygon = simplified(band["polygon"], area)
        bearing, sectors = allocate(polygon, searching, origins, settings, direction_deg)
        next_band = in_flight and chosen < len(all_bands)
        strips = survey_sectors(sectors, settings, bearing)
        # Only the new strips scale with the budget; unfinished rows and transit are fixed.
        scalable = {id(step) for sector in strips for step in sector["steps"]}
        sectors = [
            *with_continuing(strips, continuing),
            *[{"drone_id": name, "steps": [], "returning": True} for name in returning],
        ]
        work = branches(
            sectors, states, settings, landmarks, next_band=next_band, aftermath=aftermath
        )
        observed = union_all(
            [
                polygon,
                *[
                    Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]])
                    for steps in continuing.values()
                    for step in steps
                    if step["kind"] == "survey"
                ],
            ]
        )
        # An expansion hulls the new band with the unfinished rows of the previous one.
        perimeter = points_of(
            simplified(
                (observed.convex_hull if expanding else polygon).buffer(
                    PERIMETER_MARGIN_M, join_style="mitre"
                )
            )
        )
        factor = sizing_factor(
            assemble(version, work[0], subject, settings, perimeter), current, fences, scalable
        )
        if factor >= 1:
            break
        budget *= factor * SIZING_MARGIN
        cells_kept = [(c["ring"], c["sector"], c["inner_m"]) for c in band["cells"]]
        if cells_kept == previous:
            # Bands are whole cells: a budget that keeps the same cells must drop the smallest.
            budget = (
                band["area_m2"]
                - min(
                    c["polygon"].area
                    for c in grid
                    if (c["ring"], c["sector"], c["inner_m"]) in cells_kept
                )
                - 1
            ) / rate
        previous = cells_kept
    else:
        raise PlanRejected(
            "endurance",
            f"Band {chosen} could not be sized to the fleet's endurance in {SIZING_PASSES} "
            f"passes; the last attempt needed {1 / factor:.2f}× the available survey time",
            detail={"band_index": chosen, "budget_s": budget, "factor": factor},
        )
    if (
        direction_deg is not None
        and region_spec["half_angle_deg"] is None
        and chosen == 1
        and not searched
    ):
        check_direction(geometry, direction_deg, category, band, chosen)
    distance_m, bearing_deg, off_deg = placement(geometry, direction_deg, band["polygon"])
    region = {
        "kind": "doctrine",
        "in_flight_expansion": in_flight,
        "ipp": ipp,
        "direction_deg": direction_deg,
        "category": category,
        "table": None
        if category not in DISTANCE_KM
        else {
            "source": SOURCES[DISTANCE_KM[category][0]]["url"],
            "population": SOURCES[DISTANCE_KM[category][0]]["population"],
            "n": DISTANCE_KM[category][1],
            "quartiles_km": list(DISTANCE_KM[category][2]),
        },
        "cells_total": len(grid),
        "budget_s": budget,
        # Bands already searched come first so band numbers continue across expansions.
        "bands": [
            *[
                {
                    "index": index + 1,
                    "polygon": points,
                    "area_m2": Polygon([(p["north_m"], p["east_m"]) for p in points]).area,
                    "mass": None,
                    "status": "previous",
                }
                for index, points in enumerate(searched)
            ],
            *[
                {
                    "index": b["index"] + len(searched),
                    # The flown band carries the exact outline its strips were cut from.
                    "polygon": exterior_points(polygon)
                    if b is band
                    else polygon_points(b["polygon"]),
                    "area_m2": b["area_m2"],
                    "mass": b["mass"],
                    "status": band_status(b["index"], chosen, b["status"]),
                }
                for b in all_bands
            ],
        ],
    }
    sentences = rationale(
        category,
        region_spec["radius_m"],
        direction_deg,
        direction_name,
        region_spec["half_angle_deg"],
        {**band, "index": chosen + len(searched)},
        len(grid),
    )
    sentences.append(
        f"Band {chosen + len(searched)} centres {distance_m:.0f} m from the initial planning point "
        f"at bearing {bearing_deg:.0f}°"
        + ("." if off_deg is None else f", {off_deg:.0f}° off the stated direction.")
    )
    sentences.append(
        f"Band {chosen + len(searched)} is sized to {len(searching)} aircraft from their live battery after the "
        f"{lock_s:g} s candidate allowance"
        + (f" and a {APPROVAL_HOVER_S} s approval hover" if next_band else "")
        + "; strips equalise flight time."
        + (
            f" It takes 1/{SORTIE_BANDS} of the sortie so the fleet can expand in flight."
            if in_flight and not searched
            else f" It takes the whole sortie: the {trip_s:.0f} s round trip to the initial "
            "planning point exceeds half the searchable endurance, so expansion resumes from "
            "the pads."
            if proposals and not searched
            else ""
        )
        + (f" {', '.join(returning)} return home without a strip." if returning else "")
        + (
            f" {', '.join(sorted(continuing))} finish their current rows first."
            if continuing
            else ""
        )
    )
    return work, perimeter, region, sentences, next_band, band_coverage(polygon, sectors)


def sizing_factor(plan, current, fences, scalable):
    """Smallest ratio over searching drones of allowed to planned time on the new strips (the
    step objects in scalable), from the compiler's own step timing; below one the band exceeds
    the fleet's endurance."""
    Draft202012Validator(PLAN_SCHEMA).validate(plan)
    validate_search(plan, fences)
    factor = math.inf
    for branch in plan["drones"]:
        name = branch["drone_id"]
        state = current[name]
        mission, finite_s, lock_s = compile_branch(plan, branch, state, fences[name])
        survey_s = sum(
            r["duration_s"]
            for r in mission["step_ranges"]
            if id(branch["steps"][r["step_index"]]) in scalable
        )
        if survey_s == 0:
            continue
        allowance = available_seconds(state["battery_percent"]) - lock_s - (finite_s - survey_s)
        if allowance <= 0:
            raise PlanRejected(
                "endurance",
                f"{name} cannot reach the band and return within its usable "
                f"{available_seconds(state['battery_percent']) / 60:.1f} min after the "
                f"{lock_s:g} s candidate allowance; search nearer or with fresher aircraft",
                drone_id=name,
                detail={"usable_s": available_seconds(state["battery_percent"]), "lock_s": lock_s},
            )
        factor = min(factor, allowance / survey_s)
    return factor


def outline(landmark):
    geometry = ipp_geometry(landmark)
    return LineString(geometry.exterior.coords) if geometry.geom_type == "Polygon" else geometry


def oriented(coords, origin):
    """Route vertices ordered to start nearest the aircraft; closed outlines rotate to it."""
    closed = coords[0] == coords[-1]
    if closed:
        ring = coords[:-1]
        start = min(range(len(ring)), key=lambda i: math.dist(ring[i], origin))
        rotated = ring[start:] + ring[:start]
        return [*rotated, rotated[0]]
    return (
        list(reversed(coords))
        if math.dist(coords[-1], origin) < math.dist(coords[0], origin)
        else coords
    )


def route_work(tasking, drones, states, settings, origins, area, known, fence):
    """One observed follow step per route, assigned in lane order, inside a shared perimeter."""
    references = tasking["references"]
    if references["polygon"] is not None:
        ring = [(p["north_m"], p["east_m"]) for p in references["polygon"]]
        lines = [("operator polygon", "operator polygon", LineString([*ring, ring[0]]))]
    else:
        lines = [
            (identifier, known[identifier]["name"], outline(known[identifier]))
            for identifier in references["landmark_ids"]
        ]
    fleet = tasking["fleet"]
    if fleet["drone_ids"] is None and fleet["count"] is None:
        # An unnamed fleet takes one aircraft per route in lane order.
        drones = drones[: len(lines)]
    if len(lines) != len(drones):
        raise PlanRejected(
            "participants",
            f"{len(lines)} route(s) for {len(drones)} aircraft; one aircraft follows one route, "
            f"so name {len(lines)} aircraft in fleet.drone_ids or set fleet.count {len(lines)}",
            detail={"routes": [label for label, _, _ in lines], "aircraft": drones},
        )
    for label, name, line in lines:
        if not line.within(area.buffer(CLICK_TOLERANCE_M)):
            raise PlanRejected(
                "fence",
                f"{name} leaves the searchable area ({BOUNDARY_CLEARANCE_M:g} m inside the "
                "fence and outside exclusions); it cannot be followed whole",
                detail={"landmark_id": label},
            )
    observed = tasking["subject"] is not None
    sectors = []
    for drone, (label, name, line) in zip(drones, lines):
        origin = (origins[drone]["north_m"], origins[drone]["east_m"])
        coords = oriented(list(line.coords), origin)
        step = {
            "kind": "follow",
            "points": [point(n, e) for n, e in coords],
            "height_m": settings["height_m"],
            "speed_mps": settings["speed_mps"],
        }
        if observed:
            step["observe"] = True
        sectors.append(
            {"drone_id": drone, "steps": [step], "landmark_id": label, "length_m": line.length}
        )
    perimeter = None
    if observed:
        corridor = union_all([line.buffer(settings["spacing_m"]) for _, _, line in lines])
        perimeter = points_of(
            simplified(clipped_area(corridor.convex_hull, fence, "The route corridor"))
        )
    work = branches(
        sectors,
        states,
        settings,
        [known[i] for i in known],
        next_band=False,
        aftermath=tasking["aftermath"],
    )
    region = {
        "kind": "route_corridor",
        "routes": [
            {"landmark_id": label, "name": name, "length_m": line.length}
            for label, name, line in lines
        ],
        "corridor_half_width_m": settings["spacing_m"],
    }
    sentences = [
        f"{'Hasty search' if observed else 'Pass'} along {', '.join(name for _, name, _ in lines)} "
        f"({sum(line.length for _, _, line in lines):.0f} m) at {settings['height_m']:g} m above "
        "ground"
        + (
            f"; the observed corridor is {2 * settings['spacing_m']:g} m wide and is not "
            "credited as surveyed area."
            if observed
            else "."
        )
    ]
    return work, perimeter, region, sentences


def inspection_work(tasking, drone, states, settings, origins, known, fence):
    """A fly to the stand-off point facing the aircraft, one orbit and an optional dwell."""
    references, subject = tasking["references"], tasking["subject"]
    target = targets_of(references, known)[0]
    name = reference_name(references, known)
    origin = origins[drone]
    stand_off = settings["stand_off_m"]
    north, east = origin["north_m"] - target["north_m"], origin["east_m"] - target["east_m"]
    distance = math.hypot(north, east)
    unit = (north / distance, east / distance) if distance else (-1.0, 0.0)
    entry = point(target["north_m"] + stand_off * unit[0], target["east_m"] + stand_off * unit[1])
    orbit = {
        "kind": "circle",
        **target,
        "radius_m": stand_off,
        "height_m": settings["height_m"],
        "speed_mps": settings["speed_mps"],
        "turns": 1,
        "clockwise": True,
    }
    if subject is not None:
        orbit["observe"] = True
    steps = [fly(entry, settings["height_m"], settings["speed_mps"]), orbit]
    if settings["dwell_seconds"] is not None:
        steps.append({"kind": "hover", "seconds": settings["dwell_seconds"]})
    sectors = [{"drone_id": drone, "steps": steps, "target": target, "stand_off_m": stand_off}]
    work = branches(
        sectors,
        states,
        settings,
        [known[i] for i in known],
        next_band=False,
        aftermath=tasking["aftermath"],
    )
    perimeter = None
    if subject is not None:
        disc = Point(target["north_m"], target["east_m"]).buffer(
            stand_off + settings["look_ahead_m"]
        )
        perimeter = points_of(simplified(clipped_area(disc, fence, "The orbit")))
    region = {"kind": "point", "target": target, "stand_off_m": stand_off}
    sentences = [
        f"One orbit of radius {stand_off:g} m around {name} at {settings['height_m']:g} m above "
        "ground"
        + (
            f", then a {settings['dwell_seconds']} s dwell"
            if settings["dwell_seconds"] is not None
            else ""
        )
        + ("; the orbit observes its centre." if subject is not None else ".")
    ]
    return work, perimeter, region, sentences


def transit_work(tasking, drone, states, settings, known):
    """Plain fly steps through the referenced positions at the task height."""
    references = tasking["references"]
    targets = targets_of(references, known)
    steps = [fly(target, settings["height_m"], settings["speed_mps"]) for target in targets]
    if settings["dwell_seconds"] is not None:
        steps.append({"kind": "hover", "seconds": settings["dwell_seconds"]})
    sectors = [{"drone_id": drone, "steps": steps, "targets": targets}]
    work = branches(
        sectors,
        states,
        settings,
        [known[i] for i in known],
        next_band=False,
        aftermath=tasking["aftermath"],
    )
    names = (
        [known[i]["name"] for i in references["landmark_ids"]]
        if references["landmark_ids"]
        else [f"N {p['north_m']:.0f}, E {p['east_m']:.0f}" for p in references["points"]]
    )
    region = {"kind": "waypoints", "targets": targets}
    sentences = [
        f"{'Transit' if len(targets) == 1 else 'Waypoint route'} via the departure lane to "
        f"{', '.join(names)} at {settings['height_m']:g} m above ground"
        + (
            f", a {settings['dwell_seconds']} s dwell"
            if settings["dwell_seconds"] is not None
            else ""
        )
        + f"; aftermath {tasking['aftermath'].replace('_', ' ')}."
    ]
    return work, None, region, sentences


def plan(
    tasking,
    version,
    states,
    fences,
    landmarks,
    reservations,
    operator_points,
    *,
    searched=(),
    continuing=None,
):
    """A compiled receipt for the tasking, extended with tasking, region, sectors, fit and rationale.

    searched and continuing serve search expansions: polygons already searched and, per drone,
    the unfinished observed steps it flies before its new strip.
    """
    assert type(version) is int and version >= 1, "Plan version must be a positive integer"
    continuing = {} if continuing is None else continuing
    tasking = validate_tasking(tasking, landmarks, operator_points)
    assert not (searched or continuing) or tasking["task"] == "area_search", (
        "Only area searches expand"
    )
    current = {state["id"]: state for state in states}
    assert set(current) == set(NAMES), "Live state must contain all four drones"
    drones = choose_drones(tasking, current)
    settings, subject, task = tasking["settings"], tasking["subject"], tasking["task"]
    known = {landmark["id"]: landmark for landmark in landmarks}
    boundaries = {name: read_fences(fences[name]) for name in drones}
    area = searchable_area(boundaries[drones[0]])
    fence = Polygon([(p["north_m"], p["east_m"]) for p in boundaries[drones[0]]["inclusion"]])
    for name in drones[1:]:
        area = area.intersection(searchable_area(boundaries[name]))
        fence = fence.intersection(
            Polygon([(p["north_m"], p["east_m"]) for p in boundaries[name]["inclusion"]])
        )
    assert area.geom_type == "Polygon" and fence.geom_type == "Polygon", (
        "The participants' fences leave no common searchable area"
    )
    origins = {
        name: point(current[name]["north_m"], current[name]["east_m"])
        if current[name]["armed"]
        else point(current[name]["home"]["north_m"], current[name]["home"]["east_m"])
        for name in drones
    }
    next_band, coverage = False, None
    if task in ("area_search", "datum_search"):
        work, perimeter, region, sentences, next_band, coverage = search_work(
            tasking,
            drones,
            states,
            current,
            settings,
            origins,
            area,
            known,
            fences,
            version,
            list(searched),
            continuing,
        )
    elif task in ("route_search", "boundary_check"):
        work, perimeter, region, sentences = route_work(
            tasking, drones, states, settings, origins, area, known, fence
        )
    elif task == "point_inspection":
        work, perimeter, region, sentences = inspection_work(
            tasking, drones[0], states, settings, origins, known, fence
        )
    else:
        work, perimeter, region, sentences = transit_work(
            tasking, drones[0], states, settings, known
        )
    plan_drones, metadata = work
    receipt = compile_plan(
        assemble(version, plan_drones, subject, settings, perimeter),
        states,
        fences,
        landmarks,
        reservations,
    )
    if coverage is not None:
        # The perimeter may hull unfinished rows of the previous band; coverage is of the band.
        receipt["coverage"] = coverage
    if task in ("area_search", "datum_search") and not tasking["region"]["accept_partial"]:
        percent = receipt["coverage"]["survey_percent"]
        if percent < COVERAGE_PERCENT:
            raise PlanRejected(
                "coverage",
                f"Survey strips cover {percent:.1f} % of the band; set region.accept_partial to "
                "search it knowingly incomplete",
                detail=receipt["coverage"],
            )
    extras = {
        "coverage": receipt["coverage"],
        "tasking": tasking,
        "region": region,
        "sectors": metadata,
        "fit": fit(receipt, states, APPROVAL_HOVER_S if next_band else 0),
        "rationale": sentences,
    }
    json.dumps(extras, allow_nan=False)
    receipt.update(extras)
    return receipt
