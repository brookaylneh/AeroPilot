"""Twelve real Wonder conversations; pure previews, recorded state, no aircraft commands."""

import argparse
import asyncio
import copy
import hashlib
import json
import time
from itertools import count
from pathlib import Path

from mission_planner.agents.map_context import compact_map, ground_height, ground_profile
from mission_planner.agents.models import Wonder
from mission_planner.agents.tools import AGENT_INSTRUCTIONS, TOOLS, validate_arguments
from mission_planner.gcs.fences import default_fences, fence_items, read_fences
from mission_planner.gcs.geodesy import NAMES, homes
from mission_planner.gcs.planner import compile_plan, expanding_square
from mission_planner.gcs.service import flight_constraints
from world.cosys.landmarks import directions, find, load

CHANGE = "Go to the Granite Dome instead. Keep Drone1, 40 m above ground, 5 m/s and hover until I instruct you; no return yet, zero stand-off over its centre."
SCRIPTS = [
    (
        "cabin",
        "Go to the Ranger Cabin.",
        "Use Drone1 alone, 40 m above ground, 5 m/s, directly over the cabin centre with zero stand-off; hover until I instruct you, then we will decide what happens next.",
        "high",
        False,
    ),
    (
        "offset",
        "Send Drone1 200 m north of the footbridge centre, at 40 m above ground and 5 m/s. Hover there for 60 seconds, then return home at 40 m above ground and 5 m/s and land. Zero stand-off.",
        None,
        "high",
        False,
    ),
    (
        "ambiguous",
        "Take Drone1 to the trail at 40 m above ground, 5 m/s; hover until I instruct you, zero stand-off and no return yet.",
        "I mean the southern endpoint of Lake Trail.",
        "high",
        False,
    ),
    (
        "map_click",
        "Take Drone1 to that unnamed spot at 40 m above ground, 5 m/s; hover until I instruct you, zero stand-off and no return yet.",
        "I clicked the map point north -600 m, east -200 m.",
        "high",
        False,
    ),
    (
        "impossible",
        "Send Drone1 to north 2000 m, east 0 m at 5 m above home, 5 m/s; hover for 60 seconds then land. I require those exact coordinates and height.",
        None,
        "high",
        False,
    ),
    (
        "circle",
        "Use Drone1 to circle Granite Dome's centre once clockwise, radius 80 m, 40 m above ground, 5 m/s, then return home at the same height and speed and land.",
        None,
        "high",
        False,
    ),
    (
        "follow",
        "Use Drone1 to follow the complete Creek Loop in the map's listed point order, at 40 m above ground, 5 m/s and zero lateral stand-off. Hover at the end until I instruct you; no return yet.",
        None,
        "high",
        False,
    ),
    (
        "survey",
        "Use Drone1 to survey the rectangle with corners north/east (-600,-200), (-500,-200), (-500,-100), (-600,-100) metres, with north-south passes 20 m apart at 40 m above ground and 5 m/s. Then return home at the same height and speed and land.",
        None,
        "high",
        False,
    ),
    (
        "two_drones",
        "Send Drone1 above the northernmost shore point of Granite Lake and Drone2 above its southernmost shore point, both 40 m above ground, 5 m/s and zero stand-off. Hover until instructed; no return yet. Choose and show separated transit routes.",
        None,
        "high",
        False,
    ),
    ("change_high", CHANGE, None, "high", True),
    ("change_medium", CHANGE, None, "medium", True),
    (
        "addition",
        "Also check Granite Lake's centre afterwards for 60 seconds before coming home. Keep Drone1's agreed cabin visit first and keep 40 m above ground, 5 m/s and zero stand-off throughout.",
        None,
        "high",
        True,
    ),
]


def fixture(recorded, airborne, landmarks):
    states = [
        {
            "id": name,
            "drone_id": name,
            "north_m": home["north_m"],
            "east_m": home["east_m"],
            "altitude_above_home_m": 0,
            "home": home,
            "battery_percent": 100,
            "ground_speed_mps": 0,
            "armed": False,
            "landed_state": 1,
            "mode": "STABILIZE",
            "link_lost": False,
            "ekf_healthy": True,
            "gps_fix": 6,
            "mission_current": None,
            "mission_total": 0,
        }
        for name, home in homes().items()
    ]
    active = None
    progress = {}
    if airborne:
        states[0] = {**copy.deepcopy(recorded["vehicle"]), "drone_id": "Drone1"}
        for state in states[1:]:
            state["home"]["altitude_msl_m"] = recorded["vehicle"]["home"]["altitude_msl_m"]
        cabin = next(p for p in compact_map(landmarks)["places"] if p["id"] == "ranger-cabin")
        active = {
            "version": 1,
            "drones": [
                {
                    "drone_id": "Drone1",
                    "steps": [
                        {"kind": "takeoff", "height_m": 40},
                        {
                            "kind": "fly",
                            "north_m": -790,
                            "east_m": -2.5,
                            "height_m": 40,
                            "speed_mps": 5,
                        },
                        {
                            "kind": "fly",
                            "north_m": cabin["anchor_ne_m"][0],
                            "east_m": cabin["anchor_ne_m"][1],
                            "height_m": 40,
                            "speed_mps": 5,
                        },
                        {"kind": "hover", "seconds": 60},
                        {"kind": "return_home", "height_m": 40, "speed_mps": 5},
                    ],
                }
            ],
        }
        progress = {
            "Drone1": {"current_step": 2, "completed_steps": [0, 1], "time_remaining_s": None}
        }
    fence = fence_items(default_fences())
    return (
        states,
        {name: fence for name in NAMES},
        {"active": active, "progress": progress, "point": None},
    )


async def conversation(model, script, recorded, directory):
    name, request, reply, effort, airborne = script
    directory.mkdir()
    landmarks = load()
    states, fences, planner = fixture(recorded, airborne, landmarks)
    constraints = flight_constraints(
        [{**state, "fence": read_fences(fences[state["id"]])} for state in states]
    )
    context = {
        "flight_constraints": constraints,
        "execution": "Offline test. Telemetry for Drone1 is recorded when airborne; other drones are landed fixtures. The current task plan is a scripted fixture. No command reaches an aircraft.",
        "map": compact_map(landmarks),
        "current_vehicles": states,
        "planner": planner,
    }
    (directory / "context.json").write_text(json.dumps(context, indent=2) + "\n")
    turns, events, preview = [], [], None
    history = []
    started = time.monotonic()

    def tool(name, arguments):
        nonlocal preview
        validate_arguments(name, arguments)
        if name == "preview_plan":
            minimum = planner["active"]["version"] if planner["active"] is not None else 0
            assert arguments["plan"]["version"] > minimum, "Plan version must advance"
            preview = compile_plan(arguments["plan"], states, fences, landmarks)
            return preview
        if name in ("request_launch", "apply_plan_change"):
            assert preview is not None and preview["plan"]["version"] == arguments["version"], (
                "No matching successful preview"
            )
            assert (name == "apply_plan_change") == airborne, "Wrong launch/change operation"
            checked = compile_plan(preview["plan"], states, fences, landmarks)
            return {
                "dry_run": True,
                "approval_requested": name == "request_launch",
                "plan": checked["plan"],
                "missions": checked["missions"],
            }
        if name == "get_fleet":
            return states
        if name == "get_drone":
            return next(v for v in states if v["id"] == arguments["drone_id"])
        if name == "get_mission":
            return {"planner": planner, "flight_constraints": constraints, "alerts": []}
        if name == "find_place":
            return find(landmarks, arguments["name"])
        if name == "describe_location":
            return directions(landmarks, **arguments)
        if name == "expanding_square":
            return expanding_square(**arguments)
        if name == "ground_height":
            return ground_height(**arguments)
        if name == "ground_profile":
            return ground_profile(**arguments)
        if name == "show_on_map":
            return arguments
        raise AssertionError(f"Tool is outside this offline mission script: {name}")

    try:
        for phase, message in enumerate([request] + ([reply] if reply is not None else [])):
            if name == "map_click" and phase == 1:
                planner["point"] = {"north_m": -600, "east_m": -200}
            history.append({"role": "user", "content": [{"type": "input_text", "text": message}]})
            for turn in count():
                trace = directory / f"{phase}-{turn}"
                call_started = time.monotonic()
                pending = asyncio.create_task(
                    asyncio.to_thread(
                        model.respond,
                        None,
                        [],
                        effort,
                        TOOLS,
                        instructions=AGENT_INSTRUCTIONS
                        + "\nCurrent mission context: "
                        + json.dumps(context, separators=(",", ":")),
                        trace_path=trace,
                        history=tuple(history),
                    )
                )
                await asyncio.wait([pending])
                error = pending.exception()
                if error is not None:
                    usage = None
                    if trace.with_suffix(".http.json").is_file():
                        http = json.loads(trace.with_suffix(".http.json").read_text())
                        if http["status_code"] == 200:
                            usage = json.loads(trace.with_suffix(".response.json").read_text())[
                                "usage"
                            ]
                    turns.append(
                        {
                            "phase": phase,
                            "turn": turn,
                            "seconds": time.monotonic() - call_started,
                            "error": f"{type(error).__name__}: {error}",
                            "trace": str(trace),
                            "usage": usage,
                        }
                    )
                    raise error
                response = pending.result()
                history.extend(response["output"])
                calls = [v for v in response["output"] if v["type"] == "function_call"]
                text = "\n".join(
                    c["text"]
                    for v in response["output"]
                    if v["type"] == "message"
                    for c in v["content"]
                    if c["type"] == "output_text"
                )
                usage = response["usage"]
                turns.append(
                    {
                        "phase": phase,
                        "turn": turn,
                        "seconds": time.monotonic() - call_started,
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                        "reasoning_tokens": usage["output_tokens_details"]["reasoning_tokens"],
                        "text": text,
                        "calls": calls,
                    }
                )
                if not calls:
                    break
                for call in calls:
                    arguments = json.loads(call["arguments"])
                    pending = asyncio.create_task(asyncio.to_thread(tool, call["name"], arguments))
                    await asyncio.wait([pending])
                    error = pending.exception()
                    result = (
                        {"ok": False, "error": f"{type(error).__name__}: {error}"}
                        if error is not None
                        else {"ok": True, "state": pending.result()}
                    )
                    events.append(
                        {
                            "phase": phase,
                            "name": call["name"],
                            "arguments": arguments,
                            "result": result,
                        }
                    )
                    history.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps(result, separators=(",", ":")),
                        }
                    )
                    if error is not None and call["name"] != "preview_plan":
                        raise error
        terminal = "apply_plan_change" if airborne else "request_launch"
        final_phase = int(reply is not None)
        score = {
            "asked_before_unsettled_plan": None
            if reply is None
            else any(t["phase"] == 0 and "?" in t["text"] for t in turns)
            and not any(e["phase"] == 0 and e["name"] == "preview_plan" for e in events),
            "no_extra_clarification_after_settled_request": not any(
                t["phase"] == final_phase and not t["calls"] and "?" in t["text"] for t in turns
            ),
            "final_plan_passed_preview": preview is not None
            if name != "impossible"
            else preview is None,
            "correct_launch_or_change_tool": any(
                e["name"] == terminal and e["result"]["ok"] for e in events
            )
            if name != "impossible"
            else not any(e["name"] in ("request_launch", "apply_plan_change") for e in events),
            "plan_matches_request_manual_review": None,
        }
        return {
            "name": name,
            "effort": effort,
            "seconds": time.monotonic() - started,
            "score": score,
            "automatic_passed": all(v for v in score.values() if v is not None),
            "turns": turns,
            "events": events,
            "plan": preview["plan"] if preview is not None else None,
        }
    finally:
        (directory / "conversation.json").write_text(
            json.dumps({"turns": turns, "events": events}, indent=2) + "\n"
        )


async def run(output, recorded_path):
    output.mkdir(parents=True, exist_ok=False)
    recorded = json.loads(recorded_path.read_text())
    assert recorded["vehicle"]["armed"] and recorded["vehicle"]["mode"] == "AUTO"
    assert recorded["vehicle"]["ground_speed_mps"] > 0
    model = Wonder()
    rows = []
    try:
        for script in SCRIPTS:
            started = time.monotonic()
            pending = asyncio.create_task(conversation(model, script, recorded, output / script[0]))
            await asyncio.wait([pending])
            error = pending.exception()
            row = (
                {
                    "name": script[0],
                    "effort": script[3],
                    "seconds": time.monotonic() - started,
                    "automatic_passed": False,
                    "error": f"{type(error).__name__}: {error}",
                }
                if error is not None
                else pending.result()
            )
            rows.append(row)
            (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
            print(
                f"{script[0]}: {'PASS' if row['automatic_passed'] else 'FAIL'} {row['seconds']:.3f} s",
                flush=True,
            )
    finally:
        model.http.close()
    (output / "measurement.json").write_text(
        json.dumps(
            {
                "model": "muse-wonder-1.0",
                "flight_execution": False,
                "script_count": len(rows),
                "recorded_state": str(recorded_path),
                "prompt_sha256": hashlib.sha256(AGENT_INSTRUCTIONS.encode()).hexdigest(),
                "tools_sha256": hashlib.sha256(
                    json.dumps(TOOLS, sort_keys=True).encode()
                ).hexdigest(),
                "scoring": "Automatic structural checks; semantic request match requires explicit trace review. Question detection is literal punctuation and is retained for review.",
                "scripts": rows,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recorded-state", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output, args.recorded_state))
