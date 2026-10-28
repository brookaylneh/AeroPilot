"""Normalized negative evidence with fully correlated repeated-look accounting."""

import numpy as np


def validate_probability(probability):
    assert probability.ndim == 1 and np.isfinite(probability).all()
    assert ((probability >= 0) & (probability <= 1)).all()
    assert abs(float(probability.sum()) - 1) < 1e-9


def negative_update(probability, previous_pod, frame_pod):
    validate_probability(probability)
    assert previous_pod.shape == frame_pod.shape == probability.shape
    assert np.isfinite([previous_pod, frame_pod]).all()
    assert ((previous_pod >= 0) & (previous_pod <= 1) & (frame_pod >= 0) & (frame_pod <= 1)).all()
    cumulative = np.maximum(previous_pod, frame_pod)
    assert np.all(probability[previous_pod == 1] == 0), (
        "Contradictory probability after certain negative evidence"
    )
    increment = np.divide(
        cumulative - previous_pod,
        1 - previous_pod,
        out=np.zeros_like(cumulative),
        where=previous_pod < 1,
    )
    denominator = 1 - float(probability @ increment)
    assert denominator > 0, "Negative observation contradicts entire belief"
    updated = probability * (1 - increment) / denominator
    validate_probability(updated)
    return updated, cumulative
