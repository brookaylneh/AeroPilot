"""Open Meadow bilinear ground heights: metres north/east and metres UE up."""

import hashlib
import json
import math
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
BOUNDS = WORLD["searchBoundsCm"]
FIELD = WORLD["heightfield"]
assert (FIELD["rowAxis"], FIELD["columnAxis"], FIELD["encoding"]) == (
    "+Y",
    "+X",
    "uint16-little-endian",
)
ROWS, COLUMNS = FIELD["rows"], FIELD["columns"]
assert all(isinstance(n, int) and n > 1 for n in (ROWS, COLUMNS))
assert FIELD["zeroSample"] == 32768 and FIELD["samplesPerMetre"] == 128
for axis, count in (("x", COLUMNS), ("y", ROWS)):
    assert math.isclose(
        (BOUNDS["max"][axis] - BOUNDS["min"][axis]) / 100,
        (count - 1) * FIELD["sampleSpacingMetres"],
        abs_tol=1e-10,
    )
NORTH_MIN_M, NORTH_MAX_M = (BOUNDS[end]["x"] / 100 for end in ("min", "max"))
EAST_MIN_M, EAST_MAX_M = (BOUNDS[end]["y"] / 100 for end in ("min", "max"))


@lru_cache(maxsize=1)
def heights():
    values = array("H")
    path = ROOT / FIELD["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == WORLD["heightfieldSha256"]
    with path.open("rb") as stream:
        values.fromfile(stream, ROWS * COLUMNS)
        assert not stream.read(), "Unexpected heightfield size"
    if sys.byteorder != "little":
        values.byteswap()
    assert len(values) == ROWS * COLUMNS
    return values


def terrain_height(x, y):
    i = (x - NORTH_MIN_M) / (NORTH_MAX_M - NORTH_MIN_M) * (COLUMNS - 1)
    j = (y - EAST_MIN_M) / (EAST_MAX_M - EAST_MIN_M) * (ROWS - 1)
    if not (0 <= i <= COLUMNS - 1 and 0 <= j <= ROWS - 1):
        raise ValueError("Outside searchable landscape")
    a, b = min(int(i), COLUMNS - 2), min(int(j), ROWS - 2)
    u, v = i - a, j - b
    values = heights()
    return sum(
        (values[row * COLUMNS + col] - FIELD["zeroSample"]) / FIELD["samplesPerMetre"] * weight
        for row, col, weight in (
            (b, a, (1 - u) * (1 - v)),
            (b, a + 1, u * (1 - v)),
            (b + 1, a, (1 - u) * v),
            (b + 1, a + 1, u * v),
        )
    )
