"""Score recorded rescue missions: did launched plans contain and assign the subject's position?"""

import argparse
import json
from pathlib import Path

from shapely import Point, Polygon, union_all

from mission_planner.environment import ROOT, load_env
from mission_planner.evaluation import truth

MISSIONS = ROOT / ".local/data/missions"
PLAN_TOOLS = ("preview_plan", "plan_task")


def events_of(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def case_of(events, briefings):
    """The case id from the mission start event, else the briefing whose text the operator pasted."""
    for event in events:
        if event["kind"] == "mode" and event["result"] is not None and "case_id" in event["result"]:
            return event["result"]["case_id"]
    texts = {brief.strip(): case_id for case_id, _, brief in briefings}
    for event in events:
        if event["kind"] == "operator" and event["text"].strip() in texts:
            return texts[event["text"].strip()]
    return None


def launched_plans(events):
    return [
        event["result"]["receipt"]["plan"]
        for event in events
        if event["kind"] == "plan_version"
        and event["result"] is not None
        and "stage" in event["result"]
        and event["result"]["stage"] == "uploaded"
    ]


def containment(plan, subject):
    point = Point(subject["north_m"], subject["east_m"])
    if "search" not in plan:
        return {"perimeter": False, "assigned": False, "perimeter_ha": None}
    perimeter = Polygon([(p["north_m"], p["east_m"]) for p in plan["search"]["perimeter"]])
    surveys = [
        Polygon([(p["north_m"], p["east_m"]) for p in step["polygon"]])
        for branch in plan["drones"]
        for step in branch["steps"]
        if step["kind"] == "survey" and "observe" in step
    ]
    assigned = union_all(surveys) if surveys else Polygon()
    return {
        "perimeter": perimeter.covers(point),
        "assigned": assigned.covers(point),
        "perimeter_ha": perimeter.area / 1e4,
    }


def mission_row(directory, briefings):
    events = events_of(directory)
    case_id = case_of(events, briefings)
    if case_id is None:
        return None
    subject = truth.subject_position(case_id)
    operator = [e for e in events if e["kind"] == "operator"]
    uploads = [
        e
        for e in events
        if e["kind"] == "plan_version"
        and e["result"] is not None
        and "stage" in e["result"]
        and e["result"]["stage"] == "uploaded"
    ]
    attempts = [
        e
        for e in events
        if e["kind"] == "tool_result" and e["name"] in PLAN_TOOLS and e["source"] == "agent"
    ]
    first_upload = uploads[0]["timestamp_ms"] if uploads else None
    return {
        "mission_id": directory.name,
        "case_id": case_id,
        "plan_tool": "plan_task"
        if any(e["name"] == "plan_task" for e in attempts)
        else "preview_plan",
        "plan_attempts": len(attempts),
        "failed_attempts": sum(not e["result"]["ok"] for e in attempts),
        "launched_plans": len(uploads),
        "time_to_first_launch_s": None
        if first_upload is None or not operator
        else (first_upload - operator[0]["timestamp_ms"]) / 1000,
        "plans": [containment(plan, subject) for plan in launched_plans(events)],
        "evidence_events": sum(e["kind"] == "evidence" and e["source"] == "agent" for e in events),
    }


def run(output):
    load_env()
    output.mkdir(parents=True, exist_ok=False)
    briefings = truth.briefings()
    rows = [
        row for d in sorted(MISSIONS.iterdir()) if d.is_dir() and (row := mission_row(d, briefings))
    ]
    searched = [r for r in rows if r["plans"]]
    summary = {
        "rescue_missions": len(rows),
        "missions_with_launched_plans": len(searched),
        "subject_in_a_launched_perimeter": sum(
            any(p["perimeter"] for p in r["plans"]) for r in searched
        ),
        "subject_under_an_assigned_survey": sum(
            any(p["assigned"] for p in r["plans"]) for r in searched
        ),
        "failed_plan_attempts": sum(r["failed_attempts"] for r in rows),
        "plan_attempts": sum(r["plan_attempts"] for r in rows),
    }
    (output / "replay.json").write_text(
        json.dumps({"summary": summary, "missions": rows}, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
