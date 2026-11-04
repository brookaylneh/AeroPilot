"""Measure fresh-conversation Wonder tool selection without executing flight."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from mission_planner.agents.models import Wonder
from mission_planner.agents.tools import AGENT_INSTRUCTIONS, TOOLS, validate_arguments
from mission_planner.environment import load_env


def call(tool_name, **arguments):
    assert tool_name in {tool["name"] for tool in TOOLS}, f"Unavailable Wonder tool: {tool_name}"
    validate_arguments(tool_name, arguments)
    return {"name": tool_name, "arguments": arguments}


CASES = [
    ("Read me the current mission briefing and alerts.", [call("get_mission")]),
    ("How is the whole fleet doing right now?", [call("get_fleet")]),
    ("Give me Drone3's battery, GPS and flight mode.", [call("get_drone", drone_id="Drone3")]),
    ("Centre the map on Drone2.", [call("show_on_map", target="drone", drone_id="Drone2")]),
    (
        "Show the point 150 metres north, 80 metres west on the map.",
        [call("show_on_map", target="point", north_m=150, east_m=-80)],
    ),
    (
        "Show a 100 metre radius area centred 200 metres north and 50 metres east.",
        [call("show_on_map", target="area", north_m=200, east_m=50, radius_m=100)],
    ),
    ("Open Drone2's live forward camera.", [call("open_feed", drone_id="Drone2")]),
    (
        "Read the measured terrain height at 100 metres north and 20 metres east.",
        [call("ground_height", north_m=100, east_m=20)],
    ),
    (
        "Read the terrain profile from north 100 east 20 to north 200 east 20, in that order.",
        [
            call(
                "ground_profile",
                points=[{"north_m": 100, "east_m": 20}, {"north_m": 200, "east_m": 20}],
            )
        ],
    ),
    ("Brake and hold Drone1 now.", [call("hold", drone_id="Drone1")]),
    ("Pause Drone2 in place.", [call("hold", drone_id="Drone2")]),
    ("Stop Drone4 immediately.", [call("stop", drone_id="Drone4")]),
    ("Send Drone3 back to its launch point.", [call("return_fleet", drone_ids=["Drone3"])]),
    (
        "Return Drone1 and Drone2 to their launch points together.",
        [call("return_fleet", drone_ids=["Drone1", "Drone2"])],
    ),
    ("Land Drone4 where it is.", [call("land", drone_id="Drone4")]),
    ("Hold Drone1 and Drone2.", [call("hold", drone_id="Drone1"), call("hold", drone_id="Drone2")]),
    (
        "Preview version 3 succeeded and is ready for launch approval. Request launch of that version now.",
        [call("request_launch", version=3)],
    ),
    (
        "Preview version 4 succeeded for the already-airborne participants with no new aircraft. Apply that remaining-plan change now.",
        [call("apply_plan_change", version=4)],
    ),
    (
        "A search expansion proposal for plan version 6 is in your context: band 2 north-east of the campsite, all four drones have endurance. Apply it so the operator can approve.",
        [call("apply_proposal", version=6)],
    ),
    (
        "Drone2 is healthy and airborne on a verified plan with no active search or inspection. Inspect its current forward view for 'house' at threshold 0.3 for 20 seconds.",
        [call("start_inspection", drone_id="Drone2", prompt="house", threshold=0.3, duration_s=20)],
    ),
    (
        "Stop Drone2's current inspection without changing flight.",
        [call("stop_inspection", drone_id="Drone2")],
    ),
    (
        "Drone2 has no active inspection. Aim its camera at north 100 east 20 at target height zero metres above terrain; keep its flight unchanged.",
        [call("set_camera_view", drone_id="Drone2", north_m=100, east_m=20, height_m=0, zoom=1)],
    ),
    (
        "Read the current candidate image from active inspection inspection-17. Its temporal track is healthy; do not adjust the camera or flight.",
        [call("get_candidate", inspection_id="inspection-17")],
    ),
    (
        "Turn airborne Drone1 to heading 90 degrees clockwise from north while preserving its verified AUTO route.",
        [call("face_heading", drone_id="Drone1", heading_deg=90)],
    ),
    (
        "Schedule a timer-only wake after 10 seconds, with the exact description 'Check mission progress'. Make this the last call.",
        [call("wait_for", conditions=[], timeout_s=10, description="Check mission progress")],
    ),
    (
        "Resolve the named place 'FOOTBRIDGE' using the landmark catalogue.",
        [call("find_place", name="FOOTBRIDGE")],
    ),
    (
        "Look up the exact alias 'crossing'. It may name more than one place; show all matching candidates.",
        [call("find_place", name="crossing")],
    ),
    (
        "Look up 'Unlisted Saddle' exactly. If unknown, show the known names.",
        [call("find_place", name="Unlisted Saddle")],
    ),
    (
        "Give landmark directions for the point 38 metres north and 12 metres west of mission origin.",
        [call("describe_location", north_m=38, east_m=-12)],
    ),
    (
        "The find_place result for 'crossing' returned two candidates: bridge-a, North Bridge; bridge-b, South Bridge. Ask me which destination I mean. No new lookup is needed.",
        [],
    ),
    (
        "The find_place result for 'Unlisted Saddle' has no matches and known_names ['North Bridge', 'Creek Loop']. Present those names and ask me to choose or mark the map; keep this response to that clarification.",
        [],
    ),
    ("Show Drone9's live video.", []),
    ("Thanks, that's helpful.", []),
]


def run(output, first_case):
    assert 1 <= first_case <= len(CASES)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    model = Wonder()
    try:
        for index, (request, expected) in enumerate(CASES[first_case - 1 :], first_case):
            started = time.perf_counter()
            trace = output / f"{index:02d}"
            response = model.respond(
                request,
                [],
                "low",
                TOOLS,
                instructions=AGENT_INSTRUCTIONS,
                trace_path=trace,
                cache_key="bench_tools",
            )
            elapsed = time.perf_counter() - started
            http = json.loads(trace.with_suffix(".http.json").read_text())
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
                "http_status_code": http["status_code"],
                "request_body_bytes": http["request_body_bytes"],
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
        "first_case": first_case,
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
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-case", type=int, default=1)
    args = parser.parse_args()
    run(args.output, args.first_case)
