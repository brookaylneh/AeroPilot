"""One approved fleet search: flight through existing MAVLink handlers and measured completion."""

import asyncio
import json
import math
import time

from mission_planner.search.planner import AIRBORNE_SECONDS, validate_batch


def flight_label(name, arguments):
    return "Fleet search" if name == "start_search" else arguments["drone_id"]


async def until(mission, predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, message
        mission.fleet._check_tasks()
        await asyncio.sleep(0.2)


async def guarded(search, coroutine):
    try:
        return await coroutine
    except Exception as error:
        search.status = "failed"
        search.changed()
        raise RuntimeError(f"Search execution failed: {error}") from error


async def start_search(search, plan_id):
    return await guarded(search, begin(search, plan_id))


async def begin(search, plan_id):
    mission = search.mission
    value = search.require_plan(plan_id)
    states = {v["id"]: v for v in mission.vehicles()}
    assert all(
        not s["armed"] and s["landed_state"] == 1 and s["ekf_healthy"] and not s["link_lost"]
        for s in states.values()
    )
    routes = {n: r["waypoints"] for n, r in value["routes"].items()}
    validate_batch(routes, states, {n: v["fence"] for n, v in mission.fleet.vehicles.items()})
    search.status = "starting"
    search.changed()
    started = time.monotonic()
    for name, route in routes.items():
        await mission.fleet.execute(
            "take_off",
            {"drone_id": name, "altitude_above_home_m": route[0]["altitude_above_home_m"]},
        )
    await until(
        mission,
        lambda: all(
            v["landed_state"] == 2
            and abs(v["altitude_above_home_m"] - routes[v["id"]][0]["altitude_above_home_m"]) < 1.5
            for v in mission.vehicles()
        ),
        180,
        "Fleet did not reach approved launch altitudes",
    )
    acknowledgements = []
    for name, spec in value["routes"].items():
        state = await mission.fleet.execute(
            "fly_route",
            {"drone_id": name, "waypoints": spec["waypoints"], "speed_mps": spec["speed_mps"]},
        )
        acknowledgements.append(
            {"drone_id": name, "mode": state["mode"], "mission_total": state["mission_total"]}
        )
        mission.directory.joinpath(name + ".plan").write_text(
            json.dumps(mission.fleet.export_plan(name), indent=2) + "\n"
        )
    search.status = "running"
    search.changed()
    mission.launch(guarded(search, finish(search, started)))
    return {"acknowledged_aircraft": acknowledgements, **search.summary()}


async def finish(search, started):
    mission = search.mission
    pending = set(search.plan["routes"])
    deadline = started + AIRBORNE_SECONDS
    while pending:
        assert time.monotonic() < deadline, "Search exceeded approved endurance budget"
        mission.fleet._check_tasks()
        for state in mission.vehicles():
            name = state["id"]
            if name not in pending:
                continue
            assert not state["link_lost"] and state["ekf_healthy"], (
                f"{name} search telemetry unhealthy"
            )
            last = search.plan["routes"][name]["waypoints"][-1]
            error = math.dist(
                [state[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
                [last[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
            )
            if state["mission_current"] == state["mission_total"] - 1 and error < 2:
                await mission.fleet.execute("return_home", {"drone_id": name})
                pending.remove(name)
        await asyncio.sleep(0.2)
    await until(
        mission,
        lambda: all(not s["armed"] and s["landed_state"] == 1 for s in mission.vehicles()),
        max(1, deadline - time.monotonic()),
        "Fleet did not complete approved return and landing",
    )
    search.status = "complete"
    search.changed()
    mission.event(
        "operator",
        "Search flight completed; all four aircraft landed. "
        + search.coverage_summary()["message"],
        source="operator",
        result={"elapsed_s": time.monotonic() - started},
    )
