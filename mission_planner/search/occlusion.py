"""Conservative aerial visibility and authored footprint fractions on the belief grid."""

import numpy as np

from mission_planner.search.grid import polygon_areas


def footprint_fraction(grid, geometry):
    assert geometry["type"] == "polygon", "An authored polygon footprint is required"
    polygon = [{"north_m": p["x"] / 100, "east_m": p["y"] / 100} for p in geometry["points"]]
    cell_area = np.prod(grid["high"] - grid["low"], axis=1)
    assert np.all((grid["areas"] == 0) | np.isclose(grid["areas"], cell_area, rtol=0, atol=1e-9)), (
        "Footprint evidence requires whole in-world cells"
    )
    fractions = polygon_areas(grid, polygon) / cell_area
    assert np.isfinite(fractions).all() and ((fractions >= 0) & (fractions <= 1 + 1e-9)).all()
    return np.minimum(fractions, 1) * (grid["areas"] > 0)


def aerial_visibility(grid, landmarks):
    assert landmarks is not None, "Aerial detection credit requires the public landmark catalogue"
    visible = grid["areas"] > 0
    for landmark in landmarks:
        if landmark["kind"] in ("structure", "forest"):
            visible &= footprint_fraction(grid, landmark["geometry"]) == 0
    return visible
