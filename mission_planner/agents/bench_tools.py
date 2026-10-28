"""Measure fresh-conversation Wonder tool selection without executing flight."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from mission_planner.agents.models import Wonder
from mission_planner.agents.tools import AGENT_INSTRUCTIONS, TOOLS


def call(name, **arguments):
    return {"name": name, "arguments": arguments}


def focus(target, drone_id=None, north_m=None, east_m=None, radius_m=None):
    arguments = {"target": target}
    if target == "drone":
        arguments["drone_id"] = drone_id
    else:
        arguments.update(north_m=north_m, east_m=east_m)
        if target == "area":
            arguments["radius_m"] = radius_m
    return call("show_on_map", **arguments)


CASES = [
    ("Read me the current rescue briefing and mission alerts.", [call("get_mission")]),
    ("How is the whole fleet doing right now?", [call("get_fleet")]),
    ("Give me Drone3's battery, GPS and flight mode.", [call("get_drone", drone_id="Drone3")]),
    ("Centre the map on Drone2.", [focus("drone", drone_id="Drone2")]),
    (
        "Show the point 150 metres north, 80 metres west on the map.",
        [focus("point", north_m=150, east_m=-80)],
    ),
    (
        "Show a 100 metre radius area centred 200 metres north and 50 metres east.",
        [focus("area", north_m=200, east_m=50, radius_m=100)],
    ),
    (
        "Take off Drone1 to 30 metres above home.",
        [call("take_off", drone_id="Drone1", altitude_above_home_m=30)],
    ),
    (
        "Launch Drone4 to 40 metres above its home.",
        [call("take_off", drone_id="Drone4", altitude_above_home_m=40)],
    ),
    (
        "Send Drone2 in GUIDED to 120 metres north and 60 metres east, 30 metres above home, at 5 metres per second.",
        [
            call(
                "fly_to",
                drone_id="Drone2",
                north_m=120,
                east_m=60,
                altitude_above_home_m=30,
                speed_mps=5,
            )
        ],
    ),
    (
        "Move Drone3 to 50 metres south, 100 metres west, at 45 metres above home and 4 metres per second.",
        [
            call(
                "fly_to",
                drone_id="Drone3",
                north_m=-50,
                east_m=-100,
                altitude_above_home_m=45,
                speed_mps=4,
            )
        ],
    ),
    (
        "Fly Drone1 an AUTO route through (north 100, east 20) then (north 200, east 20), all at 30 metres above home and 5 metres per second.",
        [
            call(
                "fly_route",
                drone_id="Drone1",
                waypoints=[
                    {"north_m": 100, "east_m": 20, "altitude_above_home_m": 30},
                    {"north_m": 200, "east_m": 20, "altitude_above_home_m": 30},
                ],
                speed_mps=5,
            )
        ],
    ),
    (
        "Drone4 should follow waypoints north -200 east 100 then north -100 east 150. Use 50 metres above home at both and 6 metres per second.",
        [
            call(
                "fly_route",
                drone_id="Drone4",
                waypoints=[
                    {"north_m": -200, "east_m": 100, "altitude_above_home_m": 50},
                    {"north_m": -100, "east_m": 150, "altitude_above_home_m": 50},
                ],
                speed_mps=6,
            )
        ],
    ),
    ("Brake and hold Drone1 now.", [call("hold", drone_id="Drone1")]),
    ("Pause Drone2 in place.", [call("hold", drone_id="Drone2")]),
    (
        "Resume Drone1's uploaded mission from its current item.",
        [call("resume", drone_id="Drone1")],
    ),
    ("Send Drone3 back to its launch point.", [call("return_home", drone_id="Drone3")]),
    ("Land Drone4 where it is.", [call("land", drone_id="Drone4")]),
    ("Hold Drone1 and Drone2.", [call("hold", drone_id="Drone1"), call("hold", drone_id="Drone2")]),
    ("Thanks, that's helpful.", []),
    ("Explain the difference between Supervised and Autonomous mode.", []),
    (
        "Fly Drone1 to 900 metres north, zero east, 30 metres above home, at 5 metres per second.",
        [],
    ),
    ("Take off Drone9 to 30 metres above home.", []),
    ("Fly Drone2 to north 100 east 100 at 150 metres above home and 5 metres per second.", []),
    ("Show me the person detections from Drone1's camera.", []),
    ("Take off Drone2.", []),
]
CASES += [
    (
        "The subject profile, operator last known point and search area/domain are already recorded; fleet is healthy and landed. Compute the search plan now.",
        [call("plan_search")],
    ),
    (
        "Show the current spatial probability map and top probability areas.",
        [call("get_probability_map")],
    ),
    ("How much ground have the cameras actually covered?", [call("get_coverage")]),
    (
        "Request approval for existing search plan 0123456789abcdef01234567.",
        [call("start_search", plan_id="0123456789abcdef01234567")],
    ),
    (
        "Define a hiker search area using Australian mixed statistics with vertices in this order: north -700 east -100, north -400 east -100, north -400 east 100, north -700 east 100.",
        [
            call(
                "define_search_area",
                domain="australian_mixed",
                vertices=[
                    {"north_m": n, "east_m": e}
                    for n, e in ((-700, -100), (-400, -100), (-400, 100), (-700, 100))
                ],
            )
        ],
    ),
    (
        "The briefing description, reportedCircumstances and lastKnownLocation are all empty. Record the subject profile with category unknown, all other facts null and no evidence or prompts.",
        [
            call(
                "record_subject_profile",
                category={"value": "unknown", "evidence": []},
                **{
                    key: {"value": None, "evidence": []}
                    for key in (
                        "age_years",
                        "hours_missing",
                        "last_known_location",
                        "clothing",
                        "carried_items",
                    )
                },
                person_prompts=[],
                clue_prompts=[],
            )
        ],
    ),
]
assert len(CASES) == 31


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    model = Wonder()
    try:
        for index, (request, expected) in enumerate(CASES, 1):
            started = time.perf_counter()
            response = model.respond(request, [], "low", TOOLS, instructions=AGENT_INSTRUCTIONS)
            elapsed = time.perf_counter() - started
            (output / f"{index:02d}.json").write_text(json.dumps(response, indent=2) + "\n")
            actual = [
                {"name": item["name"], "arguments": json.loads(item["arguments"])}
                for item in response["output"]
                if item["type"] == "function_call"
            ]

            remaining = list(actual)
            for wanted in expected:
                if wanted in remaining:
                    remaining.remove(wanted)
            passed = len(actual) == len(expected) and not remaining
            row = {
                "case": index,
                "request": request,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "seconds": elapsed,
                "usage": response["usage"],
            }
            rows.append(row)
            print(f"{index:02d} {'PASS' if passed else 'MISS'} {elapsed:.3f} s", flush=True)
            (output / "scores.json").write_text(json.dumps(rows, indent=2) + "\n")
    finally:
        model.http.close()
    correct = sum(row["passed"] for row in rows)
    result = {
        "schema_version": 1,
        "model": "muse-wonder-1.0",
        "effort": "low",
        "created_at_s": time.time(),
        "fresh_conversation_each_case": True,
        "flight_execution": False,
        "scoring": "Exact unordered list of tool names and JSON arguments; no-call cases require zero calls. No-tool prose correctness is not scored.",
        "prompt_sha256": hashlib.sha256(AGENT_INSTRUCTIONS.encode()).hexdigest(),
        "tools_sha256": hashlib.sha256(json.dumps(TOOLS, sort_keys=True).encode()).hexdigest(),
        "correct": correct,
        "total": len(rows),
        "accuracy": correct / len(rows),
        "misses": [row["case"] for row in rows if not row["passed"]],
        "raw_directory": str(output),
        "cases": rows,
        "scoring_output": f"{correct}/{len(rows)} exact tool selections ({100 * correct / len(rows):.1f}%). Misses: {[row['case'] for row in rows if not row['passed']]}",
    }
    (output / "measurement.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["scoring_output"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
