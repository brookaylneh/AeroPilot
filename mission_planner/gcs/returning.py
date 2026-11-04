"""Checked home approaches on separate altitude levels, followed by native RTL landing."""

import math

from pymavlink.dialects.v20 import ardupilotmega as MAV

from mission_planner.gcs.geodesy import MAX_ALTITUDE_M, NAMES, RTL_ALTITUDE_M
from mission_planner.gcs.planner import mission_home, mission_item, mission_point

LEVEL_SPACING_M = 6.0
HOME_TOLERANCE_M = 0.5


def approach(state, home_altitude_msl_m):
    altitude = (
        home_altitude_msl_m
        + RTL_ALTITUDE_M
        + NAMES.index(state["id"]) * LEVEL_SPACING_M
        - state["home"]["altitude_msl_m"]
    )
    assert math.isfinite(altitude) and RTL_ALTITUDE_M <= altitude <= MAX_ALTITUDE_M, (
        state["id"] + ": separated return level exceeds the permitted altitude"
    )
    items = [
        mission_home(state["home"]),
        # A timed loiter completes the vertical leg before horizontal travel begins.
        mission_item(state, altitude, MAV.MAV_CMD_NAV_LOITER_TIME, 1),
        mission_item(state["home"], altitude, MAV.MAV_CMD_NAV_LOITER_UNLIM),
    ]
    return {"items": items, "route": [state, *(mission_point(item) for item in items[1:])]}


def arrived(state, point):
    return (
        math.hypot(state["north_m"] - point["north_m"], state["east_m"] - point["east_m"])
        <= HOME_TOLERANCE_M
        and abs(state["altitude_above_home_m"] - point["altitude_above_home_m"]) <= HOME_TOLERANCE_M
        and state["ground_speed_mps"] <= 0.2
        and abs(state["climb_rate_mps"]) <= 0.2
    )
