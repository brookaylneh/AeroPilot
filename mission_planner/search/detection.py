"""Explicit provisional calibration cohort; linked SAM and Glimmer frame outcomes."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import norm

from mission_planner.agents.perception.glimmer_calibration import glimmer, linked_crops

COHORT = (
    "adult-kelvin",
    "adult-etta",
    "senior-walter",
    "senior-sookja",
    "child-01",
    "child-02",
    "child-03",
    "child-04",
    "child-05",
    "adult-kelvin-blue",
)
AGL_M = 30.0
CAPTURE_SPACING_M = 12.0
SAM_THRESHOLD = 0.3


def wilson(k, n, z):
    assert 0 <= k <= n and n > 0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, mid - half), min(1.0, mid + half)]


def reduce_calibration(root):
    root = Path(root)
    data = []
    inputs = {}
    for name in COHORT:
        path = root / name / "inference.json"
        raw = path.read_bytes()
        d = json.loads(raw)
        assert d["schemaVersion"] == 2 and d["condition"][0] == name and d["samThreshold"] == 0.05
        assert len(d["frames"]) == 49
        inputs[str(path)] = hashlib.sha256(raw).hexdigest()
        data.append(d)
    crop_groups = [linked_crops(d) for d in data]
    verification = glimmer([c for group in crop_groups for c in group], SAM_THRESHOLD)
    threshold = verification["threshold"]
    bins = []
    for offset in range(0, 25, 4):
        n = k = total = empty = partial = 0
        for d, crops in zip(data, crop_groups):
            for frame in d["frames"]:
                if frame["offsetM"] != offset:
                    continue
                total += 1
                empty += frame["empty"]
                partial += not frame["contains"] and not frame["empty"]
                if not frame["contains"]:
                    continue
                n += 1
                k += any(
                    c["kind"] == "detection"
                    and c["file"] == frame["file"]
                    and c["detection"]["true"]
                    and c["detection"]["confidence"] > SAM_THRESHOLD
                    and c["yes"] >= threshold
                    for c in crops
                )
        bins.append(
            {
                "lateral_m": offset,
                "frames": total,
                "empty": empty,
                "partial": partial,
                "successes": k,
                "trials": n,
                "p": k / n if n else None,
                "wilson95": wilson(k, n, norm.ppf(0.975)) if n else None,
                "simultaneous_wilson": wilson(k, n, norm.ppf(1 - 0.05 / 12)) if n else None,
            }
        )
    assert all(b["trials"] > 0 for b in bins[:-1]) and bins[-1]["trials"] == 0
    width = 2 * AGL_M * math.tan(math.radians(35))
    knots = [0, 4, 8, 12, 16, 20, width / 2]

    def integral(values):
        return float(2 * np.trapz(np.interp(knots, range(0, 25, 4), values), knots))

    return {
        "provisional": True,
        "cohort": list(COHORT),
        "input_sha256": inputs,
        "agl_m": AGL_M,
        "sam_threshold": SAM_THRESHOLD,
        "glimmer_threshold": threshold,
        "bins": bins,
        "width_m": width,
        "length_m": width * 9 / 16,
        "sweep_width_m": integral([b["p"] for b in bins[:-1]] + [0]),
        "sweep_interval_m": [
            integral([b["simultaneous_wilson"][i] for b in bins[:-1]] + [0]) for i in (0, 1)
        ],
        "combination": "maximum qualified single-look POD; not independent frames",
        "interval_limitation": "Binomial envelope excludes phase, renderer, symmetry and correlated-cohort uncertainty",
    }


def lateral_probability(offset, model):
    x = np.asarray(offset)
    assert np.isfinite(x).all()
    values = [b["p"] for b in model["bins"][:-1]] + [0.0]
    return np.where(
        abs(x) <= model["width_m"] / 2,
        np.interp(abs(x), [b["lateral_m"] for b in model["bins"]], values),
        0.0,
    )
