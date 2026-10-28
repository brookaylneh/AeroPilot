"""World-metre calibration targets through the mission fleet's guarded MAVLink path."""

import asyncio
import math
import time

from mission_planner.gcs.fleet import MAV
from mission_planner.gcs.geodesy import ORIGIN
from world.cosys.client import validate_waypoint


def world_position(state):
    return {
        "x": state["north_m"],
        "y": state["east_m"],
        "z": state["home"]["altitude_msl_m"]
        + state["altitude_above_home_m"]
        - ORIGIN["altitude_msl_m"],
    }


def waypoint(point, state):
    validate_waypoint(point)
    return {
        "north_m": point["x"],
        "east_m": point["y"],
        "altitude_above_home_m": point["z"]
        + ORIGIN["altitude_msl_m"]
        - state["home"]["altitude_msl_m"],
    }


def state(fleet, name):
    fleet._check_tasks()
    result = next(s for s in fleet.snapshot() if s["id"] == name)
    assert not result["link_lost"], f"{name}: link lost during calibration"
    assert result["ekf_healthy"] and result["gps_fix"] >= 3, f"{name}: GPS/EKF unhealthy"
    return result


async def until(fleet, name, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while True:
        current = state(fleet, name)
        if predicate(current):
            return current
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{name}: {description} exceeded {timeout} s")
        await asyncio.sleep(0.05)


async def takeoff(fleet, name, altitude):
    mask = MAV.MAV_SYS_STATUS_PREARM_CHECK | MAV.MAV_SYS_STATUS_SENSOR_GPS

    def ready(_):
        status = fleet.vehicles[name]["messages"]["SYS_STATUS"]
        assert status.onboard_control_sensors_present & mask == mask
        assert status.onboard_control_sensors_enabled & mask == mask
        return status.onboard_control_sensors_health & mask == mask

    await until(fleet, name, ready, 30, "GPS and prearm readiness")
    await fleet.execute("take_off", {"drone_id": name, "altitude_above_home_m": altitude})
    return await until(
        fleet,
        name,
        lambda s: (
            s["armed"]
            and s["landed_state"] == MAV.MAV_LANDED_STATE_IN_AIR
            and abs(s["altitude_above_home_m"] - altitude) < 0.5
        ),
        120,
        "takeoff arrival",
    )


async def move(fleet, name, point, speed):
    target = waypoint(point, state(fleet, name))
    await fleet.execute("fly_to", {"drone_id": name, "speed_mps": speed, **target})
    settled_at = None

    def arrived(current):
        nonlocal settled_at
        error = math.dist(tuple(world_position(current).values()), tuple(point[k] for k in "xyz"))
        if (
            error >= 0.6
            or math.hypot(current["ground_speed_mps"], current["climb_rate_mps"]) >= 0.15
        ):
            settled_at = None
            return False
        if settled_at is None:
            settled_at = time.monotonic()
        return time.monotonic() - settled_at >= 1

    actual = await until(fleet, name, arrived, 120, "stationary waypoint arrival")
    return {
        "state": actual,
        "waypointWorldM": point,
        "errorMetres": math.dist(
            tuple(world_position(actual).values()), tuple(point[k] for k in "xyz")
        ),
    }


async def land(fleet, name):
    await fleet.execute("land", {"drone_id": name})
    return await until(
        fleet,
        name,
        lambda s: not s["armed"] and s["landed_state"] == MAV.MAV_LANDED_STATE_ON_GROUND,
        120,
        "landing and disarm",
    )
