"""Open Meadow bilinear ground heights: metres north/east and metres UE up."""

import json
import sys
from array import array
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORLD = json.loads((ROOT / "world/cosys/world.json").read_text())
ROWS = COLUMNS = 2017
assert (WORLD["coordinateSystem"], WORLD["north"], WORLD["east"], WORLD["moveWorldOrigin"]) == (
    "UE_LH_Z_UP_CENTIMETERS",
    "+X",
    "+Y",
    False,
)


@lru_cache(maxsize=1)
def heights():
    values = array("H")
    with (ROOT / ".local/vendor/open-meadow/height.r16").open("rb") as stream:
        values.fromfile(stream, ROWS * COLUMNS)
        assert not stream.read(), "Unexpected heightfield size"
    if sys.byteorder != "little":
        values.byteswap()
    assert len(values) == ROWS * COLUMNS
    return values


def terrain_height(x, y):
    bounds = WORLD["searchBoundsCm"]
    i = (x * 100 - bounds["min"]["x"]) / (bounds["max"]["x"] - bounds["min"]["x"]) * (COLUMNS - 1)
    j = (y * 100 - bounds["min"]["y"]) / (bounds["max"]["y"] - bounds["min"]["y"]) * (ROWS - 1)
    if not (0 <= i <= COLUMNS - 1 and 0 <= j <= ROWS - 1):
        raise ValueError("Outside searchable landscape")
    a, b = min(int(i), COLUMNS - 2), min(int(j), ROWS - 2)
    u, v = i - a, j - b
    values = heights()
    return sum(
        (values[row * COLUMNS + col] - 32768) / 128 * weight
        for row, col, weight in (
            (b, a, (1 - u) * (1 - v)),
            (b, a + 1, u * (1 - v)),
            (b + 1, a, (1 - u) * v),
            (b + 1, a + 1, u * v),
        )
    )
