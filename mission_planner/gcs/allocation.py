"""Equal-time sectors, departure lanes and altitude layers that satisfy separation by construction."""

import math

from shapely import LineString, Point, Polygon, union_all

from mission_planner.gcs.battery import available_seconds
from mission_planner.gcs.geodesy import MAX_ALTITUDE_M, MIN_ALTITUDE_M, NAMES, ORIGIN, polygon_error
from mission_planner.gcs.planner import MAX_POLYGON_VERTICES, survey_points
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.timing import PARAMETERS, landing_seconds, launch_seconds, motion_seconds
from mission_planner.gcs.validation import CLEARANCE_M
from world.cosys.terrain import terrain_height

APPROVAL_HOVER_S = 180
# An airborne drone with less searchable time than this returns instead of taking a strip.
MIN_SECTOR_S = 180.0
LAYER_STEP_M = 8.0
LANE_ROW_OFFSET_M = 20.0
LANE_ROW_SPACING_M = 12.0
# Front pads fly the low layers so rear pads cross their columns above them.
LANE_ORDER = ("Drone1", "Drone3", "Drone2", "Drone4")
TIME_TOLERANCE_S = 2.0
PAD_RADIUS_M = 15.0
# Share of survey time spent on rows rather than turns and speed ramps at the default spacing.
TURN_EFFICIENCY = 0.8
MAX_VERTICES = MAX_POLYGON_VERTICES
SIMPLIFY_STEPS_M = (1.0, 2.0, 4.0, 8.0)


def rotate(north, east, bearing_deg):
    """World (north, east) → (along, cross) for legs running along bearing_deg."""
    c, s = math.cos(math.radians(bearing_deg)), math.sin(math.radians(bearing_deg))
    return north * c + east * s, -north * s + east * c


def unrotate(along, cross, bearing_deg):
    c, s = math.cos(math.radians(bearing_deg)), math.sin(math.radians(bearing_deg))
    return along * c - cross * s, along * s + cross * c


def points_of(polygon):
    assert polygon.geom_type == "Polygon" and polygon.is_valid and not polygon.interiors
    return [{"north_m": float(n), "east_m": float(e)} for n, e in polygon.exterior.coords[:-1]]


def simplified(polygon, area=None):
    """The band polygon within the plan schema's vertex limit, as a single valid polygon kept
    inside the searchable area (simplification can push an edge outward). A band wrapping an
    already searched core keeps that core as a hole; its strips stay hole-free."""
    for tolerance in SIMPLIFY_STEPS_M:
        shape = polygon.simplify(tolerance, preserve_topology=True)
        if area is not None:
            shape = shape.intersection(area)
        if shape.geom_type == "Polygon" and len(shape.exterior.coords) - 1 <= MAX_VERTICES:
            assert shape.is_valid
            return shape
    raise PlanRejected(
        "disconnected_sector",
        f"The search band cannot be expressed with {MAX_VERTICES} vertices",
        detail={"vertices": len(polygon.exterior.coords) - 1},
    )


def long_axis_bearing(polygon):
    box = polygon.minimum_rotated_rectangle
    coords = list(box.exterior.coords)
    (a, b), (_, c) = coords[0:2], coords[1:3]
    first = math.dist(a, b)
    second = math.dist(b, c)
    start, end = (a, b) if first >= second else (b, c)
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 360


def sweep_bearings(polygon, direction_deg):
    """Candidate leg bearings: the long axis first (fewest turns), its normal, then the creeping
    line across the direction of travel when one exists."""
    axis = long_axis_bearing(polygon)
    candidates = [axis, (axis + 90) % 360]
    if direction_deg is not None:
        candidates.append((direction_deg - 90) % 360)
    unique = []
    for bearing in candidates:
        if all(abs((bearing - other + 90) % 180 - 90) > 1e-9 for other in unique):
            unique.append(bearing)
    return unique


def allocate(band, drones, origins, settings, direction_deg):
    """Partition along the first feasible candidate bearing with the shortest longest flight."""
    best, rejection = None, None
    for bearing in sweep_bearings(band, direction_deg):
        try:
            chosen, sectors = partition(band, drones, origins, settings, bearing)
        except PlanRejected as error:
            rejection = error
            continue
        longest = max(sector["total_s"] for sector in sectors)
        if best is None or longest < best[0]:
            best = (longest, chosen, sectors)
    if best is None:
        raise rejection
    return best[1], best[2]


def horizontal_seconds(a, b, speed):
    length = math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"])
    return motion_seconds(length, speed, PARAMETERS["WP_ACC"], PARAMETERS["WP_JERK"])


def survey_step(polygon, settings, bearing_deg):
    return {
        "kind": "survey",
        "polygon": points_of(polygon),
        "spacing_m": settings["spacing_m"],
        "bearing_deg": bearing_deg,
        "height_m": settings["height_m"],
        "speed_mps": settings["speed_mps"],
        "observe": True,
    }


def survey_seconds(points, speed):
    return sum(horizontal_seconds(a, b, speed) for a, b in zip(points, points[1:]))


def strip(polygon, bearing_deg, along_low, along_high, cross_low, cross_high):
    corners = [
        unrotate(a, c, bearing_deg)
        for a, c in (
            (along_low, cross_low),
            (along_high, cross_low),
            (along_high, cross_high),
            (along_low, cross_high),
        )
    ]
    piece = polygon.intersection(Polygon(corners))
    return piece


def partition(band, drones, origins, settings, bearing_deg):
    """Cut the band parallel to the sweep legs into one equal-flight-time strip per drone.

    Strips are cross-track bands, so neighbouring rows stay a full spacing apart. The strip
    nearest the aircraft goes to the first drone, whose lane layer is the lowest, so later lanes
    cross earlier entry columns above their tops; the sweep bearing flips when needed to put
    the near side first. origins maps each drone to its start point (home or current position)
    and each strip's time counts transit there and back. Returns (bearing_deg, sectors).
    """
    assert 1 <= len(drones) <= len(NAMES) and len(set(drones)) == len(drones)
    centroid_n = sum(o["north_m"] for o in origins.values()) / len(origins)
    centroid_e = sum(o["east_m"] for o in origins.values()) / len(origins)
    crosses = [rotate(n, e, bearing_deg)[1] for n, e in band.exterior.coords]
    if rotate(centroid_n, centroid_e, bearing_deg)[1] > (min(crosses) + max(crosses)) / 2:
        bearing_deg = (bearing_deg + 180) % 360
    rotated = [rotate(n, e, bearing_deg) for n, e in band.exterior.coords]
    along_low, along_high = min(a for a, _ in rotated) - 1, max(a for a, _ in rotated) + 1
    low, high = min(c for _, c in rotated), max(c for _, c in rotated)
    speed, transit = settings["speed_mps"], settings["transit_speed_mps"]
    # One row pitch for the whole band keeps neighbouring strips' rows evenly spaced.
    count = max(1, math.ceil(round((high - low) / settings["spacing_m"], 10)))
    pitch = (high - low) / count
    pitched = {**settings, "spacing_m": pitch}
    if count < len(drones):
        raise PlanRejected(
            "disconnected_sector",
            f"The band has {count} sweep rows for {len(drones)} aircraft; use fewer aircraft",
            detail={"rows": count, "aircraft": len(drones), "bearing_deg": bearing_deg},
        )
    pieces = {}

    def sector_parts(first, last):
        """Connected parts of the strip made of rows first..last-1; a keyhole-shaped band can
        split a strip, and each part becomes its own survey step joined by transit."""
        if (first, last) not in pieces:
            c0 = low + first * pitch
            c1 = high if last == count else low + last * pitch
            piece = strip(band, bearing_deg, along_low, along_high, c0, c1)
            parts = [piece] if piece.geom_type == "Polygon" else list(piece.geoms)
            # Clipping leaves near-collinear slivers on the band edge that the plan schema's
            # simple-polygon check rejects; a strip that stays non-simple is infeasible.
            parts = [p.simplify(0.05, preserve_topology=True) for p in parts]
            parts = [p for p in parts if p.geom_type == "Polygon" and p.area >= pitch**2]
            if any(p.interiors for p in parts):
                raise PlanRejected(
                    "disconnected_sector",
                    f"Rows {first + 1}–{last} of this band surround already searched ground; "
                    "use more aircraft or a smaller band",
                    detail={"bearing_deg": bearing_deg, "rows": [first, last]},
                )
            if any(polygon_error(points_of(p)) is not None for p in parts):
                parts = []
            pieces[(first, last)] = parts
        return pieces[(first, last)]

    def ordered(name, parts):
        origin = Point(origins[name]["north_m"], origins[name]["east_m"])
        return sorted(parts, key=lambda p: p.distance(origin))

    def seconds(name, parts):
        origin = origins[name]
        total, previous = 0.0, origin
        for part in ordered(name, parts):
            points = survey_points(survey_step(part, pitched, bearing_deg))
            total += horizontal_seconds(
                previous, points[0], transit if previous is origin else speed
            )
            total += survey_seconds(points, speed)
            previous = points[-1]
        return total + horizontal_seconds(previous, origin, transit)

    def cost(name, first, last):
        parts = sector_parts(first, last)
        return math.inf if not parts else seconds(name, parts)

    # Contiguous row groups in cross order, one per drone in lane order, minimising the longest
    # flight measured on the exact strip geometry.
    best = {0: (0.0, [])}
    for index, name in enumerate(drones):
        later = {}
        remaining_drones = len(drones) - index - 1
        for start, (longest, boundaries) in best.items():
            for end in range(start + 1, count - remaining_drones + 1):
                candidate = max(longest, cost(name, start, end))
                if end not in later or candidate < later[end][0]:
                    later[end] = (candidate, [*boundaries, end])
        best = later
    if count not in best:
        raise PlanRejected(
            "disconnected_sector",
            f"No partition of this band's {count} rows into {len(drones)} simple strips exists; "
            "use fewer aircraft or a smaller band",
            detail={"rows": count, "aircraft": len(drones), "bearing_deg": bearing_deg},
        )
    boundaries = [0, *best[count][1]]
    assert boundaries[-1] == count and len(boundaries) == len(drones) + 1

    covered, result = [], []
    for name, first, last in zip(drones, boundaries, boundaries[1:]):
        parts = ordered(name, sector_parts(first, last))
        if not parts:
            raise PlanRejected(
                "disconnected_sector",
                f"{name}: rows {first + 1}–{last} of this band are narrower than one sweep row; "
                "choose fewer aircraft or a smaller band",
                drone_id=name,
                detail={"bearing_deg": bearing_deg, "rows": [first, last]},
            )
        covered.extend(parts)
        result.append(
            {
                "drone_id": name,
                "polygons": parts,
                "spacing_m": pitch,
                "rows": last - first,
                "total_s": seconds(name, parts),
            }
        )
    # Slivers below one row pitch squared are dropped; everything else tiles the band.
    assert union_all(covered).area >= 0.99 * band.area, "Strips must tile the band"
    return bearing_deg, result


def drone_budgets(states, drones, settings, target, lock_s, hover_s):
    """Survey seconds each drone can give a band: its usable endurance less the candidate
    allowance, approval hover, launch, landing and the round trip to the target."""
    current = {state["id"]: state for state in states}
    budgets = {}
    for name in drones:
        state = current[name]
        origin = state if state["armed"] else state["home"]
        trip = 2 * horizontal_seconds(origin, target, settings["transit_speed_mps"])
        layer = settings["height_m"] + LAYER_STEP_M * len(drones)
        ground = landing_seconds(layer) + (0 if state["armed"] else launch_seconds(layer))
        usable = available_seconds(state["battery_percent"])
        budgets[name] = max(0.0, usable - lock_s - hover_s - trip - ground) * TURN_EFFICIENCY
    return budgets


def lane_layers(order, settings):
    base = settings["height_m"] + LAYER_STEP_M
    return {name: base + LAYER_STEP_M * index for index, name in enumerate(order)}


def check_ceiling(name, legs, layer_agl_m, home_z):
    """Every lane leg must stay inside the above-home altitude band at its layer."""
    for a, b in legs:
        length = math.hypot(b["north_m"] - a["north_m"], b["east_m"] - a["east_m"])
        for index in range(int(length // 10) + 2):
            t = min(1.0, index * 10 / length) if length else 0.0
            north = a["north_m"] + t * (b["north_m"] - a["north_m"])
            east = a["east_m"] + t * (b["east_m"] - a["east_m"])
            above_home = terrain_height(north, east) + layer_agl_m - home_z
            if not MIN_ALTITUDE_M <= above_home <= MAX_ALTITUDE_M:
                raise PlanRejected(
                    "transit_ceiling",
                    f"{name}: transit layer {layer_agl_m:g} m above ground reaches "
                    f"{above_home:.1f} m above home near N {north:.0f}, E {east:.0f}; "
                    f"the limit is {MAX_ALTITUDE_M:g} m. Lower the survey height or search nearer",
                    drone_id=name,
                    detail={
                        "layer_agl_m": layer_agl_m,
                        "above_home_m": above_home,
                        "point": {"north_m": north, "east_m": east},
                    },
                )


def obstacle_floor(legs, landmarks):
    """Lowest layer that clears every raised footprint under the lane legs by CLEARANCE_M."""
    floor = MIN_ALTITUDE_M
    for landmark in landmarks:
        if landmark["geometry"]["type"] != "polygon" or landmark["heightM"] <= 0:
            continue
        footprint = Polygon([(p["x"] / 100, p["y"] / 100) for p in landmark["geometry"]["points"]])
        for a, b in legs:
            if LineString([(a["north_m"], a["east_m"]), (b["north_m"], b["east_m"])]).intersects(
                footprint
            ):
                floor = max(floor, landmark["heightM"] + CLEARANCE_M)
    return floor


def point(north, east):
    return {"north_m": float(north), "east_m": float(east)}


def fly(target, height_m, speed_mps):
    return {"kind": "fly", **target, "height_m": height_m, "speed_mps": speed_mps}


def pattern_points(step):
    """Horizontal points a work step visits in order; hovers and whole-turn circles end where
    the aircraft already is and add none."""
    kind = step["kind"]
    if kind == "survey":
        points = survey_points(step)
        return points[step["start_at"] :] if "start_at" in step else points
    if kind == "follow":
        return step["points"]
    if kind == "fly":
        return [point(step["north_m"], step["east_m"])]
    assert kind in ("hover", "circle"), kind
    if kind == "circle":
        assert step["turns"] == int(step["turns"]), "Lane geometry needs whole-turn circles"
    return []


def survey_sectors(sectors, settings, bearing_deg):
    """Partition strips as plan sectors: one observed survey step per connected part."""
    return [
        {
            "drone_id": sector["drone_id"],
            "steps": [
                survey_step(part, {**settings, "spacing_m": sector["spacing_m"]}, bearing_deg)
                for part in sector["polygons"]
            ],
            "bearing_deg": bearing_deg,
            "rows": sector["rows"],
            "spacing_m": sector["spacing_m"],
            "estimate_s": sector["total_s"],
        }
        for sector in sectors
    ]


def branches(sectors, states, settings, landmarks, *, next_band, aftermath):
    """Plan branches for one task: lanes at per-drone layers, the sector's work steps, the end.

    A sector names its drone and its ordered work steps (observed surveys, follows, circles,
    hovers or plain fly steps); an optional "continuing" list holds unfinished observed steps
    the airborne drone flies first from where it is. Between consecutive surveys the drone
    climbs to its lane layer for the join, so joins never share height with other drones'
    rows. Rows and layers belong to the aircraft by its place in LANE_ORDER, not to its place
    in this plan, so plans compiled at different times never share a lane or a layer. Other
    keys pass through to the returned metadata.
    """
    current = {state["id"]: state for state in states}
    order = list(LANE_ORDER)
    layers = lane_layers(order, settings)
    rows = {
        name: LANE_ROW_OFFSET_M + LANE_ROW_SPACING_M * index for index, name in enumerate(order)
    }
    transit = settings["transit_speed_mps"]
    result, metadata = [], []
    for sector in sectors:
        name = sector["drone_id"]
        state = current[name]
        home = state["home"]
        home_z = home["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
        landed = not state["armed"]
        row = point(home["north_m"] + rows[name], home["east_m"])
        before = sector["continuing"] if "continuing" in sector else []
        assert not before or not landed, f"{name}: only an airborne drone continues work"
        work = [*before, *sector["steps"]]
        pattern = [p for step in work for p in pattern_points(step)]
        if not pattern:
            # No work: an airborne drone leaves by its lane and lands at home.
            assert not sector["steps"] and not landed, f"{name}: only an airborne drone returns"
            legs = [(point(state["north_m"], state["east_m"]), row)]
            layer = max(layers[name], obstacle_floor(legs, landmarks) + 2)
            check_ceiling(name, legs, layer, home_z)
            result.append(
                {
                    "drone_id": name,
                    "steps": [
                        fly(row, layer, transit),
                        {"kind": "return_home", "height_m": layer, "speed_mps": transit},
                    ],
                }
            )
            metadata.append(
                {
                    **{key: value for key, value in sector.items() if key != "steps"},
                    "entry": None,
                    "exit": None,
                    "lane_row": row,
                    "layer_agl_m": layer,
                }
            )
            continue
        entry, exit_ = pattern[0], pattern[-1]
        pads = [current[other]["home"] for other in current]
        if any(
            math.dist((p["north_m"], p["east_m"]), (pad["north_m"], pad["east_m"])) <= PAD_RADIUS_M
            for p in (entry, exit_)
            for pad in pads
        ):
            raise PlanRejected(
                "band_behind_pads",
                f"{name}: the task area overlaps the launch pads; exclude the pad area",
                drone_id=name,
                detail={"entry": entry, "exit": exit_},
            )
        # Departures climb at the pad, move north to the drone's own row, then fly straight to
        # the work entry at the drone's layer; returns reverse the same lane.
        ingress = [row, entry] if landed else [] if before else [entry]
        egress = [row]
        joins = [
            (pattern_points(a)[-1], pattern_points(b)[0])
            for a, b in zip(work, work[1:])
            if a["kind"] == "survey" and b["kind"] == "survey"
        ]
        legs = (
            list(zip([point(state["north_m"], state["east_m"]), *ingress], ingress))
            + joins
            + list(zip([exit_, *egress], egress))
        )
        layer = max(layers[name], obstacle_floor(legs, landmarks) + 2)
        check_ceiling(name, legs, layer, home_z)
        steps = [{"kind": "takeoff", "height_m": layer}] if landed else []
        steps.extend(fly(target, layer, transit) for target in ingress)
        for index, step in enumerate(work):
            if index and step["kind"] == "survey" and work[index - 1]["kind"] == "survey":
                steps.append(fly(pattern_points(step)[0], layer, transit))
            steps.append(step)
        if next_band:
            steps.append({"kind": "hover", "seconds": APPROVAL_HOVER_S})
        if aftermath == "hover":
            steps.append({"kind": "hover", "seconds": None})
        elif aftermath == "land":
            steps.append({"kind": "land"})
        else:
            assert aftermath == "return_home", aftermath
            steps.extend(fly(target, layer, transit) for target in egress)
            steps.append({"kind": "return_home", "height_m": layer, "speed_mps": transit})
        result.append({"drone_id": name, "steps": steps})
        metadata.append(
            {
                **{k: v for k, v in sector.items() if k not in ("steps", "continuing")},
                "continuing_steps": len(before),
                "entry": entry,
                "exit": exit_,
                "lane_row": row,
                "layer_agl_m": layer,
            }
        )
    return result, metadata


def fit(receipt, states, approval_hover_s):
    """Per-drone endurance headroom from the compiled durations and live battery."""
    current = {state["id"]: state for state in states}
    plan = receipt["plan"]
    drones = {}
    for name, mission in receipt["missions"].items():
        state = current[name]
        steps = next(b["steps"] for b in plan["drones"] if b["drone_id"] == name)
        lock = plan["search"]["max_lock_seconds"] if any("observe" in s for s in steps) else 0
        usable = available_seconds(state["battery_percent"])
        drones[name] = {
            "usable_flight_s": usable,
            "planned_s": usable - lock - mission["available_hover_s"],
            "indefinite": mission["duration_s"] is None,
            "lock_allowance_s": lock,
            "approval_hover_s": approval_hover_s,
            "headroom_s": mission["available_hover_s"],
        }
    return drones


def survey_remainder(step, start_at):
    """The unswept part of a survey step once start_at pattern points are behind the aircraft."""
    points = survey_points(step)
    assert 0 <= start_at < len(points), "start_at must index a pattern point"
    if start_at == 0:
        return Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]])
    polygon = Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]])
    bearing = step["bearing_deg"]
    rows = math.ceil(len(points) / 2)
    cross = [rotate(p["north_m"], p["east_m"], bearing)[1] for p in step["polygon"]]
    low, high = min(cross), max(cross)
    completed_rows = start_at // 2
    if completed_rows >= rows:
        return None
    boundary = low + completed_rows * (high - low) / rows
    along = [rotate(p["north_m"], p["east_m"], bearing)[0] for p in step["polygon"]]
    remainder = strip(polygon, bearing, min(along) - 1, max(along) + 1, boundary, high + 1)
    if remainder.is_empty:
        return None
    assert remainder.geom_type == "Polygon", remainder.geom_type
    return remainder
