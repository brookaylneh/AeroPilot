"""Eleven real Wonder conversations; tasking previews, recorded state, no aircraft commands."""

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
from mission_planner.environment import load_env
from mission_planner.gcs.fences import default_fences, fence_items, read_fences
from mission_planner.gcs.geodesy import NAMES, homes
from mission_planner.gcs.service import (
    MAP_POINT,
    flight_constraints,
    live_context,
    planning_instructions,
    planning_receipt,
)
from mission_planner.gcs.tasking import plan as plan_task
from world.cosys.landmarks import directions, find, load

# https://dev.meta.ai/docs/pricing-rate-limits, muse-wonder-1.0, USD per token (uncached input).
INPUT_USD_PER_TOKEN = 4.0e-6
OUTPUT_USD_PER_TOKEN = 20.0e-6
CHANGE = "Go to the Granite Dome instead. Keep Drone1, 40 m above ground, 5 m/s and hover until I instruct you; no return yet."
BRIEF = (
    "Incident summary\n{name}, {category}, has been missing for about 2 hours. "
    "The absence was reported at 12:55 local on 2026-10-31.\n\n"
    "Subject description\n{description}\n\nClothing and footwear\n{clothing}\n\n"
    "Point Last Seen and direction of travel\nA witness last saw the subject at {place} at "
    "11:00 local on 2026-10-31{direction}. This witnessed Point Last Seen (PLS) is the initial "
    "planning point (IPP); no later verified Last Known Position (LKP) has been reported.\n\n"
    "Tasking\nLocate the subject using the drone fleet operating from the launch port on the "
    "southern boundary. Search from the reported PLS and report confirmed sightings to the ground "
    "team with an observable landmark and approach information."
)
# (name, request, follow-up reply, reasoning effort, Drone1 airborne on the recorded cabin plan)
SCRIPTS = [
    (
        "trail",
        "Send a drone to the trail at 40 m above ground, 5 m/s and hover there until I instruct you.",
        "I mean Trailhead Access; use Drone1.",
        "high",
        False,
    ),
    (
        "map_click",
        "Take Drone1 to that unnamed spot at 40 m above ground, 5 m/s; hover until I instruct you.",
        "Use the map point at -600.00 m north, -200.00 m east.",
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
        "orbit",
        "Use Drone1 to circle Granite Dome's centre once clockwise at 80 m stand-off, 40 m above ground, 5 m/s, then return home and land.",
        None,
        "high",
        False,
    ),
    (
        "route",
        "Use Drone1 to fly the complete Creek Loop at 40 m above ground, 5 m/s. Hover at the end until I instruct you; no detector needed.",
        None,
        "high",
        False,
    ),
    (
        "boundary",
        "Check the shoreline of Meadow Pond for a bear with Drone1 at 30 m above ground, then return home.",
        None,
        "high",
        False,
    ),
    (
        "child",
        BRIEF.format(
            name="Maya Bennett",
            category="a typically developing child",
            description="Maya Bennett is 9 years old, female, approximately 143 cm tall, with a slim build. Blond hair.",
            clothing="Reported wearing pink shirt over a white long-sleeved top; light blue jeans; purple trainers.",
            place="Ridge Fire Road",
            direction=", travelling north-east",
        ),
        None,
        "high",
        False,
    ),
    (
        "hiker",
        BRIEF.format(
            name="Tomas Reyes",
            category="an experienced adult hiker",
            description="Tomas Reyes is 34 years old, male, approximately 180 cm tall, athletic build. Dark hair.",
            clothing="Reported wearing a yellow shell jacket, grey trousers and brown boots; carrying a blue daypack.",
            place="Trail Fork Sign",
            direction=", travelling east",
        ),
        None,
        "high",
        False,
    ),
    (
        "dementia",
        BRIEF.format(
            name="Harold Finch",
            category="an 81-year-old man living with dementia",
            description="Harold Finch is 81 years old, male, approximately 170 cm tall, slight build. White hair, walks slowly.",
            clothing="Reported wearing a green cardigan, beige trousers and black shoes.",
            place="Ranger Cabin",
            direction="; his direction of travel is unknown",
        ),
        None,
        "high",
        False,
    ),
    ("change_high", CHANGE, None, "high", True),
    ("change_medium", CHANGE, None, "medium", True),
]
SEARCH_SCRIPTS = {"child": "child", "hiker": "hiker", "dementia": "dementia"}


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
    return states, {name: fence for name in NAMES}, {"active": active, "progress": progress}


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
        "current_vehicles": states,
        "planner": planner,
        "inspections": [],
    }
    (directory / "context.json").write_text(json.dumps(context, indent=2) + "\n")
    turns, events, preview = [], [], None
    history = []
    clicks = []
    started = time.monotonic()

    def tool(name, arguments):
        nonlocal preview
        validate_arguments(name, arguments)
        if name == "plan_task":
            version = 1 if planner["active"] is None else planner["active"]["version"] + 1
            preview = plan_task(
                arguments["tasking"], version, states, fences, landmarks, (), clicks
            )
            return planning_receipt(preview)
        if name in ("request_launch", "apply_plan_change"):
            assert preview is not None and preview["plan"]["version"] == arguments["version"], (
                "No matching successful plan"
            )
            assert (name == "apply_plan_change") == airborne, "Wrong launch/change operation"
            return {
                "dry_run": True,
                "approval_requested": name == "request_launch",
                "plan": preview["plan"],
                "missions": planning_receipt(preview)["missions"],
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
        if name == "ground_height":
            return ground_height(**arguments)
        if name == "ground_profile":
            return ground_profile(**arguments)
        if name == "show_on_map":
            return arguments
        if name == "wait_for":
            # The service would end the turn here and resume on the event; the script stops.
            return {"dry_run": True, "scheduled": True}
        raise AssertionError(f"Tool is outside this offline mission script: {name}")

    try:
        for phase, message in enumerate([request] + ([reply] if reply is not None else [])):
            match = MAP_POINT.fullmatch(message.strip())
            if match is not None:
                clicks.append({"north_m": float(match[1]), "east_m": float(match[2])})
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
                        instructions=planning_instructions(AGENT_INSTRUCTIONS, landmarks),
                        trace_path=trace,
                        history=(*history, live_context(context, landmarks)),
                        cache_key=directory.name,
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
                        "cached_tokens": usage["input_tokens_details"]["cached_tokens"],
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
                    if error is not None and call["name"] != "plan_task":
                        raise error
                if any(call["name"] == "wait_for" for call in calls):
                    break
        terminal = "apply_plan_change" if airborne else "request_launch"
        final_phase = int(reply is not None)
        score = {
            # Wonder defers an unsettled request to the operator: text, no plan call, in phase 0.
            "asked_before_unsettled_plan": None
            if reply is None
            else any(t["phase"] == 0 and t["text"].strip() for t in turns)
            and not any(e["phase"] == 0 and e["name"] == "plan_task" for e in events),
            "no_extra_clarification_after_settled_request": not any(
                t["phase"] == final_phase and not t["calls"] and "?" in t["text"] for t in turns
            ),
            "final_plan_passed_preview": preview is not None
            if name != "impossible"
            else preview is None,
            "search_task_matches_briefing": None
            if name not in SEARCH_SCRIPTS
            else preview is not None
            and preview["tasking"]["task"] == "area_search"
            and preview["tasking"]["references"]["subject_category"] == SEARCH_SCRIPTS[name]
            and preview["tasking"]["fleet"]["drone_ids"] is None
            and preview["tasking"]["fleet"]["count"] is None,
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
            "tasking": preview["tasking"] if preview is not None else None,
            "rationale": preview["rationale"] if preview is not None else None,
        }
    finally:
        (directory / "conversation.json").write_text(
            json.dumps({"turns": turns, "events": events}, indent=2) + "\n"
        )


async def run(output, recorded_path, only, budget_usd):
    """Run the selected scripts in order; stop before the next script once the paid cost at
    the published uncached prices has reached budget_usd."""
    output.mkdir(parents=True, exist_ok=False)
    recorded = json.loads(recorded_path.read_text())
    assert recorded["vehicle"]["armed"] and recorded["vehicle"]["mode"] == "AUTO"
    assert recorded["vehicle"]["ground_speed_mps"] > 0
    scripts = [script for script in SCRIPTS if only is None or script[0] in only]
    assert only is None or len(scripts) == len(only), f"Unknown script names in {only}"
    model = Wonder()
    rows = []
    spent = 0.0
    try:
        for script in scripts:
            if spent >= budget_usd:
                print(f"budget reached: ${spent:.2f} >= ${budget_usd:.2f}; stopping", flush=True)
                break
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
            spent += sum(
                turn["input_tokens"] * INPUT_USD_PER_TOKEN
                + turn["output_tokens"] * OUTPUT_USD_PER_TOKEN
                for turn in (row["turns"] if "turns" in row else [])
                if "input_tokens" in turn
            )
            (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
            print(
                f"{script[0]}: {'PASS' if row['automatic_passed'] else 'FAIL'} {row['seconds']:.3f} s "
                f"spent ${spent:.2f}",
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
                "scripts_requested": [script[0] for script in scripts],
                "budget_usd": budget_usd,
                "spent_usd": spent,
                "prices_usd_per_token": {
                    "input": INPUT_USD_PER_TOKEN,
                    "output": OUTPUT_USD_PER_TOKEN,
                },
                "recorded_state": str(recorded_path),
                "prompt_sha256": hashlib.sha256(AGENT_INSTRUCTIONS.encode()).hexdigest(),
                "tools_sha256": hashlib.sha256(
                    json.dumps(TOOLS, sort_keys=True).encode()
                ).hexdigest(),
                "scoring": "Automatic structural checks; semantic request match requires explicit trace review. Clarification means phase 0 produced text and no plan_task call.",
                "scripts": rows,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recorded-state", type=Path, required=True)
    parser.add_argument("--only", help="comma-separated script names; default all")
    parser.add_argument("--budget-usd", type=float, required=True)
    args = parser.parse_args()
    asyncio.run(
        run(
            args.output,
            args.recorded_state,
            None if args.only is None else args.only.split(","),
            args.budget_usd,
        )
    )
