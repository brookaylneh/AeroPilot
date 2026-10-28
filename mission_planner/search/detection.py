"""Calibrated single-look probability and maximum-look capture spacing."""

import copy

import numpy as np

AGL_M = 30.0
CAPTURE_SPACING_M = 16.0
SAM_THRESHOLD = 0.3


def positional_probability(lateral, along, model):
    lateral, along = np.broadcast_arrays(np.abs(lateral), along)
    assert np.isfinite([lateral, along]).all()
    position = model["position"]
    x, y = np.asarray(position["lateral_m"]), np.asarray(position["along_m"])
    values = np.asarray(position["p"])
    assert values.shape == (len(y), len(x)) and (np.diff(x) > 0).all() and (np.diff(y) > 0).all()
    assert np.isfinite(values).all() and ((values >= 0) & (values <= 1)).all()
    ix = np.clip(np.searchsorted(x, lateral, side="right") - 1, 0, len(x) - 2)
    iy = np.clip(np.searchsorted(y, along, side="right") - 1, 0, len(y) - 2)
    tx = np.clip((lateral - x[ix]) / (x[ix + 1] - x[ix]), 0, 1)
    ty = np.clip((along - y[iy]) / (y[iy + 1] - y[iy]), 0, 1)
    lower = values[iy, ix] * (1 - tx) + values[iy, ix + 1] * tx
    upper = values[iy + 1, ix] * (1 - tx) + values[iy + 1, ix + 1] * tx
    support = (lateral <= model["width_m"] / 2) & (np.abs(along) <= model["length_m"] / 2)
    return np.where(support, lower * (1 - ty) + upper * ty, 0.0)


def negative_model(model):
    result = copy.deepcopy(model)
    result["position"]["p"] = result["position"]["simultaneous_lower"]
    return result


def track_probability(lateral, along, length, model, spacing, phases=128):
    assert np.isfinite([length, spacing]).all()
    assert length > 0 and spacing > 0 and type(phases) is int and phases >= 16
    lateral, along = np.broadcast_arrays(lateral, along)
    assert np.isfinite([lateral, along]).all()
    result = np.zeros_like(along, dtype=float)
    eligible = (abs(lateral) <= model["width_m"] / 2) & (along >= 0) & (along <= length)
    side, distance = lateral[eligible], along[eligible]
    radius = int(np.ceil(model["length_m"] / (2 * spacing)))
    phase = (np.arange(phases)[:, None] + 0.5) * spacing / phases
    before = np.floor((distance - phase) / spacing)
    maximum = np.zeros_like(before)
    for offset in range(-radius, radius + 2):
        capture = phase + (before + offset) * spacing
        probability = positional_probability(side, capture - distance, model)
        maximum = np.maximum(maximum, probability * (capture >= 0) * (capture <= length))
    result[eligible] = maximum.mean(axis=0)
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()
    return result
