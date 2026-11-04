"""Progressive search: the next band as drones near completion, and strip reassignment."""

import copy

from shapely import Polygon

from mission_planner.gcs.allocation import points_of, survey_remainder
from mission_planner.gcs.battery import available_seconds
from mission_planner.gcs.planner import survey_points

EXPANSION_LEAD_S = 240.0
WORK_KINDS = ("survey", "follow", "circle")


def last_work_index(steps):
    return max(index for index, step in enumerate(steps) if step["kind"] in WORK_KINDS)


def work_remaining_s(branch, mission, row):
    """Seconds until this drone finishes its work steps, from live progress; None when the
    remaining time is not measurable (manual mode or an indefinite step ahead)."""
    if row["time_remaining_s"] is None:
        return None
    last = last_work_index(branch["steps"])
    tail = sum(r["duration_s"] for r in mission["step_ranges"] if r["step_index"] > last)
    return max(0.0, row["time_remaining_s"] - tail)


def expandable(receipt):
    """The active plan is a doctrine area search that asked for proposals and has bands left."""
    if "tasking" not in receipt or receipt["tasking"]["task"] != "area_search":
        return False
    if not receipt["tasking"]["expansion"]["automatic_proposals"]:
        return False
    if not receipt["region"]["in_flight_expansion"]:
        return False
    return any(band["status"] == "provisional" for band in receipt["region"]["bands"])


def due(receipt, progress, states, lead_s):
    """True once any airborne participant with measurable progress is within lead_s of the
    end of its work; a plan without airborne participants is never due."""
    if not expandable(receipt):
        return False
    current = {state["id"]: state for state in states}
    airborne = [b for b in receipt["plan"]["drones"] if current[b["drone_id"]]["armed"]]
    if not airborne or any(b["drone_id"] not in progress for b in airborne):
        return False
    remaining = [
        work_remaining_s(b, receipt["missions"][b["drone_id"]], progress[b["drone_id"]])
        for b in airborne
    ]
    return any(r is not None and r <= lead_s for r in remaining)


def remaining_steps(branch, row):
    """The steps this drone has not completed; a current survey continues at its live point."""
    index = row["current_step"]
    assert index is not None, f"{branch['drone_id']}: no current step"
    steps = copy.deepcopy(branch["steps"][index:])
    if steps[0]["kind"] == "survey" and "survey_start_at" in row:
        steps[0]["start_at"] = row["survey_start_at"]
    return steps


def expansion_request(receipt, progress, states):
    """Tasking, searched polygons and continuing work for the next band of the active search."""
    current = {state["id"]: state for state in states}
    tasking = copy.deepcopy(receipt["tasking"])
    airborne = [b["drone_id"] for b in receipt["plan"]["drones"] if current[b["drone_id"]]["armed"]]
    assert airborne, "An expansion needs airborne participants"
    tasking["fleet"] = {"drone_ids": airborne, "count": None}
    tasking["region"]["band_index"] = None
    searched = [
        band["polygon"]
        for band in receipt["region"]["bands"]
        if band["status"] in ("planned", "previous")
    ]
    continuing = {}
    for branch in receipt["plan"]["drones"]:
        name = branch["drone_id"]
        if name not in airborne:
            continue
        last = last_work_index(branch["steps"])
        unfinished = [
            step for step in remaining_steps(branch, progress[name]) if step["kind"] in WORK_KINDS
        ]
        unfinished = unfinished[
            : len([s for s in branch["steps"][: last + 1] if s["kind"] in WORK_KINDS])
        ]
        if unfinished and progress[name]["current_step"] <= last:
            continuing[name] = unfinished
    return tasking, searched, continuing


def failed_remainder(branch, row):
    """Polygons of the failed drone's unfinished survey rows beyond the row it stopped on.

    The failed drone holds on its current row, and observed rows reserve space independent of
    timing, so that row cannot be handed over; the proposal names it as unswept.
    """
    polygons = []
    for step in remaining_steps(branch, row):
        if step["kind"] != "survey":
            continue
        start_at = step["start_at"] if "start_at" in step else 0
        handover = 2 * (start_at // 2 + 1) if start_at else 0
        if handover >= len(survey_points(step)):
            continue
        remainder = survey_remainder(step, handover)
        if remainder is not None:
            polygons.append((step, remainder))
    return polygons


def reassignment(receipt, progress, states, failed):
    """A complete remaining plan where `failed` flies its exit lane home and its unfinished
    survey rows join the airborne drone with the most endurance headroom."""
    plan = receipt["plan"]
    current = {state["id"]: state for state in states}
    airborne = [b for b in plan["drones"] if current[b["drone_id"]]["armed"]]
    assert any(b["drone_id"] == failed for b in airborne), f"{failed} is not airborne"
    lock = plan["search"]["max_lock_seconds"]
    others = [b for b in airborne if b["drone_id"] != failed]
    assert others, "Reassignment needs another airborne drone"

    def headroom(branch):
        name = branch["drone_id"]
        remaining = progress[name]["time_remaining_s"]
        assert remaining is not None, f"{name}: remaining time is not measurable"
        return available_seconds(current[name]["battery_percent"]) - remaining - lock

    receiver = max(others, key=headroom)["drone_id"]
    failed_branch = next(b for b in airborne if b["drone_id"] == failed)
    extra = [
        {**{k: v for k, v in step.items() if k != "start_at"}, "polygon": points_of(remainder)}
        for step, remainder in failed_remainder(failed_branch, progress[failed])
    ]
    drones = []
    for branch in airborne:
        name = branch["drone_id"]
        if name == failed:
            last = last_work_index(branch["steps"])
            steps = [
                copy.deepcopy(step)
                for step in branch["steps"][last + 1 :]
                if not (step["kind"] == "hover" and step["seconds"] is not None)
            ]
            assert steps, f"{name}: the failed branch has no exit steps"
        else:
            steps = remaining_steps(branch, progress[name])
            if name == receiver and extra:
                last = max(
                    (i for i, step in enumerate(steps) if step["kind"] in WORK_KINDS), default=-1
                )
                layer = next(s["height_m"] for s in branch["steps"] if s["kind"] == "fly")
                joins = [
                    {
                        "kind": "fly",
                        **survey_points(step)[0],
                        "height_m": layer,
                        "speed_mps": plan["search"]["speed_mps"],
                    }
                    for step in extra
                ]
                handed = [item for pair in zip(joins, extra) for item in pair]
                steps = [*steps[: last + 1], *handed, *steps[last + 1 :]]
        drones.append({"drone_id": name, "steps": steps})
    perimeter = Polygon([(p["north_m"], p["east_m"]) for p in plan["search"]["perimeter"]])
    for step in extra:
        assert Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]]).within(
            perimeter.buffer(0.01)
        ), "A reassigned remainder lies inside the active perimeter"
    assert any(step["kind"] in WORK_KINDS for branch in drones for step in branch["steps"]), (
        "A reassignment keeps some work in the air"
    )
    return {"version": plan["version"] + 1, "search": plan["search"], "drones": drones}
