"""Provisional dementia feature mixture from published marginal find and offset tables."""

import numpy as np

from mission_planner.gcs.geodesy import point_in_polygon
from mission_planner.search.grid import polygon_areas
from mission_planner.search.prior import QUANTILES, SOURCES, quantile_curve
from world.cosys.landmarks import _intersects

DOMAIN = {"dementia": "temperate_mountainous", "child_td_1_16": "temperate"}
FIND_PERCENT = {
    "Structure": 20,
    "Road": 18,
    "Linear": 9,
    "Drainage": 9,
    "Water": 7,
    "Brush": 6,
    "Woods": 17,
    "Field": 14,
}
FEATURES = {
    "Structure": "structure",
    "Road": "road",
    "Linear": "trail",
    "Drainage": "creek",
    "Water": "pond",
    "Woods": "forest",
}
OFFSET_M = np.array([0.0, 4.0, 15.0, 71.0, 307.0])
OFFSET_CURVE = quantile_curve(OFFSET_M)


def domain_for(category):
    assert category in DOMAIN, f"No sourced Sierra temperate prior for {category}"
    return DOMAIN[category]


def offset(distance_m, derivative=False):
    d = np.asarray(distance_m)
    assert np.isfinite(d).all() and (d >= 0).all()
    slope = OFFSET_CURVE(OFFSET_M[-1], 1)
    tail = np.exp(-slope / 0.05 * np.maximum(0, d - OFFSET_M[-1]))
    result = np.where(
        d > OFFSET_M[-1],
        slope * tail if derivative else 1 - 0.05 * tail,
        OFFSET_CURVE(np.minimum(d, OFFSET_M[-1]), int(derivative)),
    )
    assert np.isfinite(result).all() and (result >= 0).all()
    if not derivative:
        assert (result <= 1).all()
    return result


def line_distance(points, landmarks):
    assert landmarks
    points = np.asarray(points)
    assert points.ndim == 2 and points.shape[1] == 2 and np.isfinite(points).all()
    result = np.full(len(points), np.inf)
    for item in landmarks:
        assert item["geometry"]["type"] == "polyline"
        vertices = np.array([[p["x"], p["y"]] for p in item["geometry"]["points"]]) / 100
        for a, b in zip(vertices, vertices[1:]):
            delta = b - a
            length2 = delta @ delta
            assert length2 > 0
            t = np.clip((points - a) @ delta / length2, 0, 1)
            result = np.minimum(result, np.linalg.norm(points - a - t[:, None] * delta, axis=1))
    assert np.isfinite(result).all() and (result >= 0).all()
    return result


def footprint_exposure(grid, landmarks):
    covered = np.zeros(len(grid["areas"]))
    previous = []
    for item in landmarks:
        assert item["geometry"]["type"] == "polygon", (
            f"Behaviour prior requires an authored polygon area for {item['name']}"
        )
        polygon = [
            {"north_m": p["x"] / 100, "east_m": p["y"] / 100} for p in item["geometry"]["points"]
        ]
        xy = [(p["north_m"], p["east_m"]) for p in polygon]
        for other, other_xy in previous:
            assert not (
                point_in_polygon(polygon[0], other)
                or point_in_polygon(other[0], polygon)
                or any(
                    _intersects(a, b, c, d)
                    for a, b in zip(xy, xy[1:] + xy[:1])
                    for c, d in zip(other_xy, other_xy[1:] + other_xy[:1])
                )
            ), (
                "Behaviour prior requires disjoint, non-touching footprints within each feature class"
            )
        previous.append((polygon, xy))
        covered += polygon_areas(grid, polygon)
    area = np.prod(grid["high"] - grid["low"], axis=1)
    result = covered / area
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1 + 1e-9)).all()
    return np.minimum(result, 1)


def condition(grid, radial, landmarks):
    assert landmarks is not None, "Dementia behaviour prior requires the public landmark catalogue"
    assert radial.shape == grid["areas"].shape
    cell_area = np.prod(grid["high"] - grid["low"], axis=1)
    assert np.all((grid["areas"] == 0) | np.isclose(grid["areas"], cell_area, rtol=0, atol=1e-9)), (
        "Behaviour prior requires whole in-world cells; partial fence clipping is unsupported"
    )
    assert np.isfinite(radial).all() and (radial >= 0).all()
    assert abs(float(radial.sum()) - 1) < 1e-9 and sum(FIND_PERCENT.values()) == 100
    result = np.zeros_like(radial)
    components = []
    for label, percent in FIND_PERCENT.items():
        if label in FEATURES:
            kind = FEATURES[label]
            features = [item for item in landmarks if item["kind"] == kind]
            assert features, f"Behaviour prior lacks authored {kind} geometry ({label}, {percent}%)"
            if kind in ("road", "trail", "creek"):
                exposure = np.zeros_like(radial)
                for dn in (-0.25, 0.25):
                    for de in (-0.25, 0.25):
                        points = grid["centers"] + [dn, de] * (grid["high"] - grid["low"])
                        exposure += offset(line_distance(points, features), True) / (
                            4 * offset(0, True)
                        )
                area_kind = (
                    "effective corridor area; pooled offset density relative to its value at zero"
                )
            else:
                exposure = footprint_exposure(grid, features)
                area_kind = "authored polygon area clipped to probability cells"
        else:
            exposure = np.ones_like(radial)
            area_kind = "unmapped source class; unresolved radial background"
        area = float(grid["areas"] @ exposure)
        weighted = radial * exposure
        normalizer = float(weighted.sum())
        assert area > 0 and normalizer > 0, f"No in-world support for source class {label}"
        component = weighted / normalizer
        assert abs(float(component.sum()) - 1) < 1e-9
        result += percent / 100 * component
        components.append(
            {
                "source_class": label,
                "weight": percent / 100,
                "area_m2": area,
                "area_kind": area_kind,
                "radial_normalizer": normalizer,
            }
        )
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()
    assert abs(float(result.sum()) - 1) < 1e-9
    return result, {
        "method": "Provisional latent feature mixture; radial prior conditioned within each source class",
        "source": {
            "url": SOURCES["dementia"]["url"],
            "page": 165,
            "find_n": 207,
            "track_offset_n": 110,
        },
        "offset_percentiles": QUANTILES[1:].tolist(),
        "offset_m": OFFSET_M[1:].tolist(),
        "components": components,
        "unresolved_weight": sum(
            percent for label, percent in FIND_PERCENT.items() if label not in FEATURES
        )
        / 100,
        "limitations": "Temperate find proportions are terrain-pooled; track offsets have no domain label. Linear=trail and Drainage=creek are mapping assumptions. No individual water-following multiplier, movement speed or elapsed-time update is sourced. Brush and Field retain 20% unresolved radial background. Corridor areas are kernel exposure areas, not physical widths. The joint model is not calibrated.",
    }
