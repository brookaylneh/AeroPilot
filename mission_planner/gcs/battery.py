"""Conservative charge budget for the documented AirSim electrical model."""

import math

CAPACITY_AH = 50.0
MAX_CURRENT_A = 50.0
CUTOFF_PERCENT = 10.0
LOW_VOLTAGE_V = 10.5
SECONDS_PER_PERCENT = CAPACITY_AH / MAX_CURRENT_A * 3600 / 100

# Pinned SIM_Battery::soc_table rows bracket the loaded-voltage threshold at 50 A.
CELL_VOLTAGE_V = (LOW_VOLTAGE_V + MAX_CURRENT_A * 0.014) / 12.6 * 4.173
assert 3.679 <= CELL_VOLTAGE_V <= 3.715
VOLTAGE_FLOOR_PERCENT = 42.31 + (CELL_VOLTAGE_V - 3.679) / (3.715 - 3.679) * (46.15 - 42.31)


def available_seconds(percent):
    assert math.isfinite(percent) and 0 <= percent <= 100, "Battery charge must be 0–100%"
    return (percent - required_percent(0)) * SECONDS_PER_PERCENT


def required_percent(seconds):
    assert math.isfinite(seconds) and seconds >= 0, "Battery duration must be finite seconds"
    return max(CUTOFF_PERCENT, VOLTAGE_FLOOR_PERCENT) + seconds / SECONDS_PER_PERCENT
