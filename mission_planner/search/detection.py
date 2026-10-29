"""Calibrated single-look probability and maximum-look capture spacing."""

import copy

import numpy as np

AGL_M = 30.0
CAPTURE_SPACING_M = 16.0
SAM_THRESHOLD = 0.3


def positional_probability(lateral, along, model):
    lateral, along = np.abs(np.asarray(lateral)), np.asarray(along)
    assert np.isfinite(lateral).all() and np.isfinite(along).all()
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
    return track_detection(model, spacing, phases)(lateral, along, length)


def track_detection(model, spacing, phases=128):
    periodic = {}

    def detect(lateral, along, length):
        return track_values(lateral, along, length, model, spacing, phases, periodic)

    return detect


def track_values(lateral, along, length, model, spacing, phases, periodic):
    assert np.isfinite([length, spacing]).all()
    assert length > 0 and spacing > 0 and type(phases) is int and phases >= 16
    lateral, along = np.broadcast_arrays(lateral, along)
    assert np.isfinite([lateral, along]).all()
    result = np.zeros_like(along, dtype=float)
    eligible = (abs(lateral) <= model["width_m"] / 2) & (along >= 0) & (along <= length)
    side, distance = lateral[eligible], along[eligible]
    values = np.zeros_like(distance, dtype=float)
    margin = model["length_m"] / 2
    interior = (distance >= margin) & (distance <= length - margin)
    if interior.any():
        packed = np.empty(int(interior.sum()), dtype=complex)
        packed.real = abs(side[interior])
        packed.imag = np.remainder(distance[interior], spacing)
        positions, inverse = np.unique(packed, return_inverse=True)
        missing = np.array([p for p in positions if p not in periodic], dtype=complex)
        if len(missing):
            shift = np.ceil(margin / spacing) * spacing
            averaged = phase_average(
                missing.real, missing.imag + shift, 2 * shift + spacing, model, spacing, phases
            )
            periodic.update(zip(missing, averaged))
        values[interior] = np.array([periodic[p] for p in positions])[inverse]
    edge = ~interior
    if edge.any():
        values[edge] = phase_average(side[edge], distance[edge], length, model, spacing, phases)
    result[eligible] = values
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()
    return result


def phase_average(side, distance, length, model, spacing, phases):
    radius = int(np.ceil(model["length_m"] / (2 * spacing)))
    phase = (np.arange(phases)[:, None] + 0.5) * spacing / phases
    before = np.floor((distance - phase) / spacing)
    maximum = np.zeros_like(before)
    for offset in range(-radius, radius + 2):
        capture = phase + (before + offset) * spacing
        probability = positional_probability(side, capture - distance, model)
        maximum = np.maximum(maximum, probability * (capture >= 0) * (capture <= length))
    return maximum.mean(axis=0)
