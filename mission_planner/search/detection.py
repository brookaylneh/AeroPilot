"""Calibrated single-look lateral detection probability."""

import numpy as np

AGL_M = 30.0
CAPTURE_SPACING_M = 12.0
SAM_THRESHOLD = 0.3


def lateral_probability(offset, model):
    x = np.asarray(offset)
    assert np.isfinite(x).all()
    values = [b["p"] for b in model["bins"][:-1]] + [0.0]
    return np.where(
        abs(x) <= model["width_m"] / 2,
        np.interp(abs(x), [b["lateral_m"] for b in model["bins"]], values),
        0.0,
    )
