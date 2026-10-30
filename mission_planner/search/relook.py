"""Approved GUIDED re-look preserving the paused AUTO mission and its progress."""

import asyncio
import copy
import math

from mission_planner.gcs.geodesy import ORIGIN
from mission_planner.search.execution import until
from mission_planner.search.terrain import terrain_leg, waypoint
from mission_planner.search.timing import SPEED_MPS, travel_seconds
from world.cosys.terrain import terrain_height


def vehicle(search, drone_id):
    return next(v for v in search.mission.vehicles() if v["id"] == drone_id)


async def follow(search, drone_id, route):
    for point in route:
        await search.mission.fleet.execute(
            "fly_to", {"drone_id": drone_id, **point, "speed_mps": SPEED_MPS}
        )
        await until(
            search.mission,
            lambda: (
                math.dist(
                    [
                        vehicle(search, drone_id)[k]
                        for k in ("north_m", "east_m", "altitude_above_home_m")
                    ],
                    [point[k] for k in ("north_m", "east_m", "altitude_above_home_m")],
                )
                < 0.8
            ),
            180,
            drone_id + " did not reach the approved re-look waypoint",
        )


async def relook(search, detection_id, drone_id, altitude_agl_m, hold_seconds):
    try:
        return await look(search, detection_id, drone_id, altitude_agl_m, hold_seconds)
    except Exception as error:
        if drone_id in search.paused and search.paused[drone_id]["kind"] == "relook":
            search.paused[drone_id]["status"] = "failed"
            search.changed()
        raise RuntimeError(f"Re-look failed: {error}") from error


async def look(search, detection_id, drone_id, altitude_agl_m, hold_seconds):
    assert search.status == "running", "Re-look requires an active search route"
    assert drone_id not in search.paused, "Drone already has a paused action"
    assert 10 <= altitude_agl_m <= 30 and 1 <= hold_seconds <= 120
    cluster = search.commander.ledger.cluster(detection_id)
    assert cluster["status"] != "dismissed", "Detection has been dismissed"
    state = vehicle(search, drone_id)
    assert state["armed"] and state["landed_state"] == 2
    fleet = search.mission.fleet
    search.paused[drone_id] = {
        "kind": "relook",
        "status": "preparing",
        "altitude_agl_m": altitude_agl_m,
    }
    search.changed()
    await fleet.execute("hold", {"drone_id": drone_id})
    await until(
        search.mission,
        lambda: vehicle(search, drone_id)["ground_speed_mps"] < 0.2,
        30,
        "Aircraft did not stop for re-look",
    )
    state = vehicle(search, drone_id)
    home_z = state["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
    point = cluster["localization"]["ground"]
    if math.hypot(state["north_m"] - point["north_m"], state["east_m"] - point["east_m"]) < 1e-6:
        route = [waypoint(point["north_m"], point["east_m"], home_z)]
        error = abs(
            route[0]["altitude_above_home_m"]
            + home_z
            - terrain_height(point["north_m"], point["east_m"])
            - 30
        )
    else:
        route, error = terrain_leg(state, point, home_z)
    if altitude_agl_m != 30:
        route.append(
            {
                **route[-1],
                "altitude_above_home_m": route[-1]["altitude_above_home_m"] + altitude_agl_m - 30,
            }
        )
    fleet._validate_route(drone_id, [state, *route])
    assert (
        2 * travel_seconds(route) + hold_seconds
        < search.plan["routes"][drone_id]["reserve_seconds"]
    ), "Re-look exceeds planned endurance reserve"
    saved = {
        "kind": "relook",
        "detection_id": detection_id,
        "status": "flying",
        "mission": copy.deepcopy(fleet.vehicles[drone_id]["mission"]),
        "mission_current": state["mission_current"],
        "route": route,
        "return_point": {k: state[k] for k in ("north_m", "east_m", "altitude_above_home_m")},
        "altitude_agl_m": altitude_agl_m,
        "planned_agl_error_m": error,
    }
    search.paused[drone_id] = saved
    search.changed()
    await follow(search, drone_id, route)
    await fleet.execute("hold", {"drone_id": drone_id})
    saved["status"] = "holding"
    search.changed()
    await asyncio.sleep(hold_seconds)
    assert fleet.vehicles[drone_id]["mission"] == saved["mission"], (
        "Re-look altered paused AUTO mission"
    )
    saved["status"] = "awaiting_resume"
    search.changed()
    return {
        "drone_id": drone_id,
        "detection_id": detection_id,
        "altitude_agl_m": altitude_agl_m,
        "hold_seconds": hold_seconds,
        "search_route": "paused; explicit resume required",
        "perception_credit": "calibrated live frames only"
        if altitude_agl_m == 30
        else "operator visual confirmation only",
        "source": search.commander.source,
    }


async def resume_search(search, drone_id):
    try:
        return await resume(search, drone_id)
    except Exception as error:
        search.paused[drone_id]["status"] = "failed"
        search.changed()
        raise RuntimeError(f"Search resume failed: {error}") from error


async def resume(search, drone_id):
    saved = search.paused[drone_id]
    if saved["kind"] == "relook":
        assert saved["status"] == "awaiting_resume", "Re-look is still executing"
        saved["status"] = "returning_to_route"
        search.changed()
        await follow(search, drone_id, [*reversed(saved["route"]), saved["return_point"]])
        assert search.mission.fleet.vehicles[drone_id]["mission"] == saved["mission"]
        assert vehicle(search, drone_id)["mission_current"] == saved["mission_current"]
    result = await search.mission.fleet.execute("resume", {"drone_id": drone_id})
    del search.paused[drone_id]
    search.changed()
    return result
