"""Jerk-limited travel and the pinned SITL climb, descent and landing constraints."""

import math

PARAMETERS = {
    "WP_ACC": 2.5,
    "WP_ACC_Z": 1.0,
    "WP_JERK": 1.0,
    "WP_SPD_UP": 2.5,
    "WP_SPD_DN": 1.5,
    "PILOT_SPD_UP": 2.5,
    "PILOT_ACC_Z": 2.5,
    "RTL_LOIT_TIME": 5000.0,
    "LAND_ALT_LOW_M": 10.0,
    "LAND_SPD_MS": 0.5,
}


def motion_seconds(length, speed, acceleration, jerk):
    assert all(math.isfinite(x) for x in (length, speed, acceleration, jerk))
    assert length >= 0 and min(speed, acceleration, jerk) > 0
    if length == 0:
        return 0.0
    if length < 2 * acceleration**3 / jerk**2:
        peak = (length * math.sqrt(jerk) / 2) ** (2 / 3)
    else:
        peak = (
            -(acceleration**2) / jerk
            + math.sqrt(acceleration**4 / jerk**2 + 4 * acceleration * length)
        ) / 2
    velocity = min(speed, peak)
    ramp = (
        2 * math.sqrt(velocity / jerk)
        if velocity < acceleration**2 / jerk
        else velocity / acceleration + acceleration / jerk
    )
    return length / velocity + ramp


def launch_seconds(height):
    return motion_seconds(
        height, PARAMETERS["PILOT_SPD_UP"], PARAMETERS["PILOT_ACC_Z"], PARAMETERS["WP_JERK"]
    )


def landing_seconds(height):
    low = PARAMETERS["LAND_ALT_LOW_M"]
    assert height >= low
    return (
        motion_seconds(
            height - low, PARAMETERS["WP_SPD_DN"], PARAMETERS["WP_ACC_Z"], PARAMETERS["WP_JERK"]
        )
        + low / PARAMETERS["LAND_SPD_MS"]
        + PARAMETERS["RTL_LOIT_TIME"] / 1000
    )
