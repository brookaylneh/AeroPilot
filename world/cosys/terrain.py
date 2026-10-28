"""Open Meadow bilinear ground heights: metres north/east and metres UE up."""

import json
import sys
from array import array
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORLD = json.loads((ROOT / "world/cosys/world.json").read_text())
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
        values.fromfile(stream, 2017 * 2017)
        assert not stream.read(), "Unexpected heightfield size"
    if sys.byteorder != "little":
        values.byteswap()
    assert len(values) == 2017 * 2017
    return values


def terrain_height(x, y):
    bounds = WORLD["searchBoundsCm"]
    i = (x * 100 - bounds["min"]["x"]) / (bounds["max"]["x"] - bounds["min"]["x"]) * 2016
    j = (y * 100 - bounds["min"]["y"]) / (bounds["max"]["y"] - bounds["min"]["y"]) * 2016
    if not (0 <= i <= 2016 and 0 <= j <= 2016):
        raise ValueError("Outside searchable landscape")
    a, b = min(int(i), 2015), min(int(j), 2015)
    u, v = i - a, j - b
    values = heights()
    return sum(
        (values[row * 2017 + col] - 32768) / 128 * weight
        for row, col, weight in (
            (b, a, (1 - u) * (1 - v)),
            (b, a + 1, u * (1 - v)),
            (b + 1, a, (1 - u) * v),
            (b + 1, a + 1, u * v),
        )
    )
