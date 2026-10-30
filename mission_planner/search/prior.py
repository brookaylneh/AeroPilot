"""Published distance CDFs; radial stationary priors with explicit domain conditioning."""

import numpy as np
from scipy.interpolate import CubicHermiteSpline, PchipInterpolator

SOURCES = {
    "children": {
        "url": "https://journalofsar.com/wp-content/uploads/2020/04/v4-8-Koester-ASD-vTD.pdf.pdf",
        "page": 73,
        "pdf_page": 9,
        "table": "3",
        "population": "Typically developing children aged 1–16; ISRID",
    },
    "hiker": {
        "url": "https://sarbayes.org/wp-content/uploads/2012/07/natsar.pdf",
        "page": 32,
        "pdf_page": 40,
        "table": "2.8",
        "population": "Australian MP-All hikers, no terrain stratification; 131 cases, 72 distances",
    },
    "dementia": {
        "url": "https://www.dbs-sar.com/LPB/Dementia.pdf",
        "page": 164,
        "pdf_page": 4,
        "table": "Horizontal distance from IPP, kilometres",
        "population": "ISRID dementia",
    },
}
TABLES = {
    ("child_td_1_16", "temperate"): ("children", 903, [0.3, 0.9, 2.4, 11.3]),
    ("child_td_1_16", "dry"): ("children", 115, [0.8, 2.4, 4.0, 9.7]),
    ("child_td_1_16", "urban"): ("children", 129, [0.2, 0.5, 1.6, 5.2]),
    ("hiker", "australian_mixed"): ("hiker", 72, [1.5, 3.2, 8.1, 17.4]),
    ("dementia", "temperate_mountainous"): ("dementia", 95, [0.3, 0.8, 1.9, 8.3]),
    ("dementia", "temperate_flat"): ("dementia", 175, [0.3, 1.0, 2.4, 12.8]),
    ("dementia", "dry_mountainous"): ("dementia", 14, [1.0, 1.9, 3.1, 6.1]),
    ("dementia", "dry_flat"): ("dementia", 15, [0.5, 1.6, 3.6, 11.8]),
    ("dementia", "urban"): ("dementia", 336, [0.3, 1.1, 3.2, 12.6]),
}
CATEGORIES = ("child_td_1_16", "hiker", "dementia", "elderly_non_dementia", "unknown")
QUANTILES = np.array([0, 0.25, 0.5, 0.75, 0.95])


def quantile_curve(q):
    assert q.shape == QUANTILES.shape and np.isfinite(q).all() and q[0] == 0
    assert np.all(np.diff(q) > 0)
    derivatives = PchipInterpolator(q, QUANTILES).derivative()(q)
    derivatives[-1] = (QUANTILES[-1] - QUANTILES[-2]) / (q[-1] - q[-2])
    interpolant = CubicHermiteSpline(q, QUANTILES, derivatives, extrapolate=False)
    assert (derivatives > 0).all()
    for i, width in enumerate(np.diff(q)):
        cubic, quadratic, linear, _ = interpolant.c[:, i]
        if cubic > 0:
            minimum = -quadratic / (3 * cubic)
            if 0 < minimum < width:
                assert (3 * cubic * minimum + 2 * quadratic) * minimum + linear >= 0
    assert np.allclose(interpolant(q), QUANTILES, rtol=0, atol=1e-15)
    assert abs(interpolant.derivative().integrate(q[0], q[-1]) + 0.05 - 1) < 1e-15
    return interpolant


def table(category, domain):
    assert (category, domain) in TABLES, (
        f"Unsupported prior: {category}/{domain}; no verified distance table"
    )
    source, n, radii = TABLES[(category, domain)]
    q = (np.array([0, *radii]) * 1000) ** 2
    interpolant = quantile_curve(q)
    return (
        q,
        interpolant,
        {
            "source": SOURCES[source],
            "n": n,
            "radius_m": np.sqrt(q[1:]).tolist(),
            "percentiles": QUANTILES[1:].tolist(),
            "method": "Cubic Hermite CDF in squared radius; PCHIP interior slopes, final secant slope; derivative-matched exponential tail",
            "fit_residuals": (interpolant(q[1:]) - QUANTILES[1:]).tolist(),
        },
    )


def cdf(distance_squared, category, domain):
    q, interpolant, _ = table(category, domain)
    x = np.asarray(distance_squared)
    assert np.isfinite(x).all() and (x >= 0).all()
    rate = interpolant(q[-1], 1) / 0.05
    result = np.where(
        x > q[-1],
        1 - 0.05 * np.exp(-rate * np.maximum(0, x - q[-1])),
        interpolant(np.minimum(x, q[-1])),
    )
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()
    return result


def density(distance_squared, category, domain):
    q, interpolant, _ = table(category, domain)
    x = np.asarray(distance_squared)
    assert np.isfinite(x).all() and (x >= 0).all()
    slope = interpolant(q[-1], 1)
    result = (
        np.where(
            x > q[-1],
            slope * np.exp(-slope / 0.05 * np.maximum(0, x - q[-1])),
            interpolant(np.minimum(x, q[-1]), 1),
        )
        / np.pi
    )
    assert np.isfinite(result).all() and (result >= 0).all()
    return result


def initialize(grid, point, category, domain):
    from mission_planner.search.places import location_support

    positions, weights = location_support(point, float(np.min(grid["high"] - grid["low"])))
    values = np.zeros(len(grid["centers"]))
    for position, weight in zip(positions, weights):
        for dn in (-0.25, 0.25):
            for de in (-0.25, 0.25):
                delta = (
                    grid["centers"] - position + np.array([dn, de]) * (grid["high"] - grid["low"])
                )
                values += weight * density(np.sum(delta**2, axis=1), category, domain) / 4
    mass = values * grid["areas"]
    retained = float(mass.sum())
    assert 0 < retained <= 1
    probability = mass / retained
    assert abs(float(probability.sum()) - 1) < 1e-9
    fit = table(category, domain)[2]
    fit["location_support"] = {
        "method": "point" if len(positions) == 1 else "uniform arclength midpoint quadrature",
        "samples": len(positions),
        "maximum_interval_m": float(np.min(grid["high"] - grid["low"])),
    }
    return probability, retained, fit
