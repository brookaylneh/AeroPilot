"""Jerk-limited travel and the pinned SITL climb, descent and landing constraints."""

import math

SPEED_MPS = 5.0
PARAMETERS = {
    "WP_ACC": 2.5,
    "WP_ACC_Z": 1.0,
    "WP_JERK": 1.0,
    "WP_SPD_UP": 2.5,
    "WP_SPD_DN": 1.5,
    "WP_RADIUS_M": 2.0,
    "WP_ACC_CNR": 0.0,
    "PILOT_SPD_UP": 2.5,
    "PILOT_ACC_Z": 2.5,
    "RTL_LOIT_TIME": 5000.0,
    "LAND_ALT_LOW_M": 10.0,
    "LAND_SPD_MS": 0.5,
    "LAND_SPD_HIGH_MS": 0.0,
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


def segment_seconds(a, b):
    horizontal = math.hypot(a["north_m"] - b["north_m"], a["east_m"] - b["east_m"])
    vertical = b["altitude_above_home_m"] - a["altitude_above_home_m"]
    return max(
        motion_seconds(horizontal, SPEED_MPS, PARAMETERS["WP_ACC"], PARAMETERS["WP_JERK"]),
        motion_seconds(
            abs(vertical),
            PARAMETERS["WP_SPD_UP"] if vertical > 0 else PARAMETERS["WP_SPD_DN"],
            PARAMETERS["WP_ACC_Z"],
            PARAMETERS["WP_JERK"],
        ),
    )


def travel_seconds(route):
    segments = []
    for a, b in zip(route, route[1:]):
        delta = [b[k] - a[k] for k in ("north_m", "east_m", "altitude_above_home_m")]
        horizontal = math.hypot(*delta[:2])
        vertical_speed = PARAMETERS["WP_SPD_UP"] if delta[2] > 0 else PARAMETERS["WP_SPD_DN"]
        cruise = max(horizontal / SPEED_MPS, abs(delta[2]) / vertical_speed)
        if cruise == 0:
            continue
        duration = segment_seconds(a, b)
        segments.append((duration, cruise, [d / cruise for d in delta]))
    total = sum(s[0] for s in segments)
    for a, b in zip(segments, segments[1:]):
        ramps = [s[0] - s[1] for s in (a, b)]
        overlap = min(ramps)
        change = [y - x for x, y in zip(a[2], b[2])]
        corner_acceleration = PARAMETERS["WP_ACC_CNR"] or 2 * PARAMETERS["WP_ACC"]
        if (
            a[1] >= ramps[0]
            and b[1] >= ramps[1]
            and math.sqrt(sum(v * v for v in change)) * overlap / 2 <= PARAMETERS["WP_RADIUS_M"]
            and 2 * math.hypot(*change[:2]) / overlap <= corner_acceleration
            and 2 * abs(change[2]) / overlap <= PARAMETERS["WP_ACC_Z"]
        ):
            total -= overlap
    assert math.isfinite(total) and total >= 0
    return total


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
