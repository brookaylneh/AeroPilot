"""Published distance CDFs; radial stationary priors with explicit domain conditioning."""

import numpy as np

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


def table(category, domain):
    assert (category, domain) in TABLES, (
        f"Unsupported prior: {category}/{domain}; no verified distance table"
    )
    source, n, radii = TABLES[(category, domain)]
    q = (np.array([0, *radii]) * 1000) ** 2
    assert np.all(np.diff(q) > 0)
    slopes = np.diff(QUANTILES) / np.diff(q)
    return (
        q,
        slopes,
        {
            "source": SOURCES[source],
            "n": n,
            "radius_m": np.sqrt(q[1:]).tolist(),
            "percentiles": QUANTILES[1:].tolist(),
            "fit_residuals": (np.interp(q[1:], q, QUANTILES) - QUANTILES[1:]).tolist(),
        },
    )


def density(distance_squared, category, domain):
    q, slopes, _ = table(category, domain)
    x = np.asarray(distance_squared)
    assert np.isfinite(x).all() and (x >= 0).all()
    bins = np.searchsorted(q[1:], x, side="right")
    result = slopes[np.minimum(bins, 3)] / np.pi
    tail = x > q[-1]
    result = np.where(
        tail, slopes[-1] / np.pi * np.exp(-slopes[-1] / 0.05 * np.maximum(0, x - q[-1])), result
    )
    return result


def initialize(grid, point, category, domain):
    north, east = point["north_m"], point["east_m"]
    values = np.zeros(len(grid["centers"]))
    for dn in (-0.25, 0.25):
        for de in (-0.25, 0.25):
            delta = (
                grid["centers"]
                - np.array([north, east])
                + np.array([dn, de]) * (grid["high"] - grid["low"])
            )
            values += density(np.sum(delta**2, axis=1), category, domain) / 4
    mass = values * grid["areas"]
    retained = float(mass.sum())
    assert 0 < retained <= 1
    probability = mass / retained
    assert abs(float(probability.sum()) - 1) < 1e-9
    return probability, retained, table(category, domain)[2]
