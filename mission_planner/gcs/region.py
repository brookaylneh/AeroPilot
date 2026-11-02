"""Doctrine-sized search regions: distance rings, direction sectors and density-ordered bands."""

import math

import numpy as np
from shapely import LineString, Point, Polygon, box, union_all

from mission_planner.gcs.fences import BOUNDARY_CLEARANCE_M
from mission_planner.gcs.geodesy import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M, NORTH_MIN_M
from mission_planner.gcs.rejection import PlanRejected
from world.cosys.landmarks import COMPASS

QUANTILES = np.array([0, 0.25, 0.5, 0.75, 0.95])
SOURCES = {
    "children": {
        "url": "https://journalofsar.com/wp-content/uploads/2020/04/v4-8-Koester-ASD-vTD.pdf.pdf",
        "table": "3",
        "population": "Typically developing children aged 1–16, temperate; ISRID2 (Koester 2020)",
    },
    "dementia": {
        "url": "https://www.dbs-sar.com/LPB/Dementia.pdf",
        "page": 164,
        "population": "ISRID dementia, temperate mountainous (Koester, Lost Person Behavior)",
    },
    "hiker": {
        "url": "https://journalofsar.com/a-pragmatic-approach-to-applied-search-theory/",
        "table": "1",
        "population": "Hiker, mountain temperate (Koester 2008 via Mansfield et al. 2020)",
    },
}
# Distance from the initial planning point, kilometres, at the 25, 50, 75 and 95 % quantiles.
DISTANCE_KM = {
    "child": ("children", 903, (0.3, 0.9, 2.4, 11.3)),
    "dementia": ("dementia", 95, (0.3, 0.8, 1.9, 8.3)),
    "hiker": ("hiker", None, (1.1, 3.1, 5.8, 11.3)),
}
# Angle between the stated direction of travel and the find, degrees, same quantiles.
DISPERSION_DEG = {"hiker": ("hiker", (2, 23, 64, 132)), "dementia": ("dementia", (11, 23, 66, 70))}
# No published child dispersion table exists; the hiker table stands in and the rationale says so.
DISPERSION_PROXY = {"child": "hiker", "elderly": "hiker", "unknown": "hiker"}
# Mission items quantise to 1e-7 degrees, so the inset keeps a hair more than the clearance.
SEARCH_INSET_M = BOUNDARY_CLEARANCE_M + 0.5
# Rows of adjacent bands end on their shared edge; a metre of gap keeps those endpoints apart.
SEARCHED_GAP_M = 1.0
SUB_RING_M = 100.0
SAMPLE_M = 20.0
ARC_STEP_DEG = 5.0
MIN_CELL_M2 = 400.0
REACH_M = 3000.0


def compass_degrees(name):
    assert name in COMPASS, f"Unknown compass direction: {name}"
    return 45.0 * COMPASS.index(name)


def distance_cdf(category, radius_m, distance_m):
    """Probability that the subject lies within distance_m of the initial planning point.

    The published ring model assigns 25/25/25/20 % to the quartile rings with uniform density
    inside each ring; the last 5 % spreads uniformly over one more equal-area ring.
    """
    x = np.asarray(distance_m, dtype=float)
    assert np.isfinite(x).all() and (x >= 0).all()
    if category not in DISTANCE_KM:
        assert radius_m is not None, "An unpublished category needs an explicit radius"
        # Uniform density over the operator's disc; the rationale states the assumption.
        return np.minimum(1.0, (x / radius_m) ** 2)
    radii = np.array([0, *DISTANCE_KM[category][2]]) * 1000
    knots = np.array([*(radii**2), 2 * radii[-1] ** 2])
    values = np.array([*QUANTILES, 1.0])
    result = np.interp(x**2, knots, values)
    assert np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()
    return result


def dispersion_table(category):
    name = DISPERSION_PROXY.get(category, category) if category not in DISPERSION_DEG else category
    return name, DISPERSION_DEG[name][1]


def dispersion_edges(category):
    return (0.0, *(float(a) for a in dispersion_table(category)[1]), 180.0)


def sector_edges(category):
    """Angular sector bounds for cells: the ±25 % quartile is a sliver, so the core spans the median."""
    return (0.0, *dispersion_edges(category)[2:])


def dispersion_mass(category, from_deg, to_deg):
    """Share of finds whose angle from the stated direction lies in [from_deg, to_deg], both sides."""
    edges = dispersion_edges(category)
    assert 0 <= from_deg < to_deg <= 180
    knots = np.array(edges)
    values = np.array([0, 0.25, 0.5, 0.75, 0.95, 1.0])
    return float(np.interp(to_deg, knots, values) - np.interp(from_deg, knots, values))


def ipp_geometry(landmark):
    points = [(p["x"] / 100, p["y"] / 100) for p in landmark["geometry"]["points"]]
    kind = landmark["geometry"]["type"]
    if kind == "polygon":
        return Polygon(points)
    return LineString(points) if len(points) > 1 else Point(points[0])


def samples(geometry):
    """Witness positions along the initial planning point every SAMPLE_M; polygons use their boundary."""
    line = geometry.exterior if geometry.geom_type == "Polygon" else geometry
    if line.geom_type == "Point":
        return [(line.x, line.y)]
    count = max(1, math.ceil(line.length / SAMPLE_M))
    return [line.interpolate(i / count, normalized=True).coords[0] for i in range(count + 1)]


def sector(origin, bearing_deg, from_deg, to_deg, radius_m):
    """Fan from origin covering the angular band [from_deg, to_deg] on both sides of bearing_deg."""
    north, east = origin

    def arc(a0, a1):
        steps = max(1, math.ceil((a1 - a0) / ARC_STEP_DEG))
        return [
            (
                north + radius_m * math.cos(math.radians(bearing_deg + a)),
                east + radius_m * math.sin(math.radians(bearing_deg + a)),
            )
            for a in np.linspace(a0, a1, steps + 1)
        ]

    if from_deg == 0:
        return Polygon([origin, *arc(-to_deg, to_deg)])
    return union_all(
        [Polygon([origin, *arc(from_deg, to_deg)]), Polygon([origin, *arc(-to_deg, -from_deg)])]
    )


def searchable_area(fences):
    """The inclusion fence inset by the boundary clearance, without the launch corridor south
    of the search bounds, less every exclusion zone buffered by that clearance."""
    inclusion = Polygon([(p["north_m"], p["east_m"]) for p in fences["inclusion"]])
    area = inclusion.buffer(-SEARCH_INSET_M).intersection(
        box(
            NORTH_MIN_M + SEARCH_INSET_M,
            EAST_MIN_M + SEARCH_INSET_M,
            NORTH_MAX_M - SEARCH_INSET_M,
            EAST_MAX_M - SEARCH_INSET_M,
        )
    )
    for exclusion in fences["exclusions"]:
        area = area.difference(
            Polygon([(p["north_m"], p["east_m"]) for p in exclusion]).buffer(SEARCH_INSET_M)
        )
    assert not area.is_empty, "The flight fence leaves no searchable area"
    return area


def cells(geometry, bearing_deg, category, radius_m, area, half_angle_deg=None):
    """Searchable cells (distance band × dispersion sector) with their probability density.

    area is the searchable polygon; an operator-stated half_angle_deg replaces the published
    dispersion sectors with one wedge holding all modelled mass.
    """
    assert geometry.geom_type in ("Point", "LineString", "Polygon"), geometry.geom_type
    assert bearing_deg is None or 0 <= bearing_deg < 360
    assert area.geom_type == "Polygon" and not area.is_empty
    assert half_angle_deg is None or (bearing_deg is not None and 0 < half_angle_deg <= 180)
    if category not in DISTANCE_KM and radius_m is None:
        raise PlanRejected(
            "no_distance_table",
            f"No published distance model for the {category} category; "
            "state an explicit search radius in region.radius_m and disclose it to the operator",
            detail={"category": category, "published": sorted(DISTANCE_KM)},
        )
    r95 = DISTANCE_KM[category][2][3] * 1000 if category in DISTANCE_KM else radius_m
    reach = min(r95, REACH_M, max(geometry.distance(Point(p)) for p in area.exterior.coords))
    edges = [*np.arange(0, reach, SUB_RING_M), reach]
    if bearing_deg is None:
        masks = [(0.0, 180.0, None, 1.0)]
    elif half_angle_deg is not None:
        wedge = union_all(
            [sector(p, bearing_deg, 0.0, half_angle_deg, REACH_M) for p in samples(geometry)]
        )
        masks = [(0.0, half_angle_deg, wedge, 1.0)]
    else:
        # Fans from different witness points overlap between angular bands; each location
        # belongs to the smallest band that reaches it, so the masks peel outward disjointly.
        angles = sector_edges(category)
        masks, covered = [], Polygon()
        for a0, a1 in zip(angles, angles[1:]):
            fan = union_all([sector(p, bearing_deg, a0, a1, REACH_M) for p in samples(geometry)])
            masks.append((a0, a1, fan.difference(covered), dispersion_mass(category, a0, a1)))
            covered = covered.union(fan)
    result = []
    for ring, (d0, d1) in enumerate(zip(edges, edges[1:])):
        band = geometry.buffer(d1).difference(geometry.buffer(d0)) if d0 else geometry.buffer(d1)
        mass = float(distance_cdf(category, radius_m, d1) - distance_cdf(category, radius_m, d0))
        for index, (a0, a1, mask, share) in enumerate(masks):
            whole = band if mask is None else band.intersection(mask)
            if whole.area < MIN_CELL_M2:
                continue
            # Density belongs to the whole ring sector; fence clipping removes mass with the area.
            density = mass * share / whole.area
            cell = whole.intersection(area)
            # Off-axis sectors have two lobes; each lobe is its own cell with its share of the mass.
            parts = [cell] if cell.geom_type == "Polygon" else list(cell.geoms)
            for part in parts:
                if part.geom_type != "Polygon" or part.area < MIN_CELL_M2:
                    continue
                result.append(
                    {
                        "ring": ring,
                        "sector": index,
                        "inner_m": float(d0),
                        "outer_m": float(d1),
                        "angle_deg": [a0, a1],
                        "polygon": part,
                        "mass": density * part.area,
                        "density": density,
                    }
                )
    assert result, "No searchable cell lies within reach of the initial planning point"
    return result


def remaining(cells, searched):
    """Cells with already searched polygons removed; a partly searched cell keeps its unsearched
    part and the matching share of its mass."""
    done = union_all(
        [Polygon([(p["north_m"], p["east_m"]) for p in points]) for points in searched]
    ).buffer(SEARCHED_GAP_M)
    result = []
    for cell in cells:
        left = cell["polygon"].difference(done)
        parts = [left] if left.geom_type == "Polygon" else list(left.geoms)
        for part in parts:
            if part.geom_type != "Polygon" or part.area < MIN_CELL_M2:
                continue
            result.append({**cell, "polygon": part, "mass": cell["density"] * part.area})
    if not result:
        raise PlanRejected(
            "coverage",
            "Every cell within reach of the initial planning point has been searched",
            detail={"searched_bands": len(searched)},
        )
    return result


def largest_part(shape):
    if shape.geom_type == "Polygon":
        return shape, []
    parts = sorted(shape.geoms, key=lambda p: p.area, reverse=True)
    return parts[0], parts[1:]


def bands(cells, budget_s, rate_m2_per_s, searched=()):
    """Group density-ordered, connected cells into bands whose survey time fits the fleet budget.

    Enclosed unsearched cells join the surrounding band; already searched polygons stay out, so
    a band wrapping a searched core keeps that core as a hole.
    """
    assert budget_s > 0 and rate_m2_per_s > 0
    done = union_all(
        [Polygon([(p["north_m"], p["east_m"]) for p in points]) for points in searched]
    ).buffer(SEARCHED_GAP_M)
    # Cells inside one quartile ring share a density up to geometric approximation; such ties
    # (chained within one percent) are searched nearest-first.
    groups = []
    for cell in sorted(cells, key=lambda c: -c["density"]):
        if groups and cell["density"] >= groups[-1][0]["density"] * 0.99:
            groups[-1].append(cell)
        else:
            groups.append([cell])
    remaining = [
        cell
        for group in groups
        for cell in sorted(group, key=lambda c: (c["inner_m"], c["sector"]))
    ]
    result = []
    while remaining:
        shape, chosen, area = None, [], 0.0
        for cell in list(remaining):
            polygon = cell["polygon"]
            if chosen and not shape.buffer(1.0).intersects(polygon):
                continue
            if chosen and (area + polygon.area) / rate_m2_per_s > budget_s:
                break
            merged = polygon if shape is None else union_all([shape, polygon])
            merged, leftovers = largest_part(merged)
            if leftovers:
                continue
            shape, area = merged, area + polygon.area
            chosen.append(cell)
            remaining.remove(cell)
        assert chosen, "Band construction made no progress"
        # An enclosed lower-priority cell would leave a hole: search it with its band instead.
        shape = Polygon(shape.exterior).difference(done)
        assert shape.geom_type == "Polygon", "A band stays one connected polygon"
        for cell in list(remaining):
            if cell["polygon"].within(shape.buffer(0.1)):
                chosen.append(cell)
                area += cell["polygon"].area
                remaining.remove(cell)
        survey_s = area / rate_m2_per_s
        result.append(
            {
                "index": len(result) + 1,
                "polygon": shape,
                "area_m2": area,
                "mass": sum(c["mass"] for c in chosen),
                "cells": [{k: v for k, v in c.items() if k != "polygon"} for c in chosen],
                "survey_s": survey_s,
                "status": "outside_endurance"
                if survey_s > budget_s
                else "planned"
                if len(result) == 0
                else "provisional",
            }
        )
    return result


def polygon_points(shape):
    simplified = shape.simplify(1.0, preserve_topology=True)
    assert simplified.geom_type == "Polygon" and simplified.is_valid
    return [{"north_m": float(n), "east_m": float(e)} for n, e in simplified.exterior.coords[:-1]]


def rationale(category, radius_m, bearing_deg, direction_name, half_angle_deg, planned, total):
    sentences = []
    if category in DISTANCE_KM:
        source, n, radii = DISTANCE_KM[category]
        record = SOURCES[source]
        sentences.append(
            f"{record['population']}: 25/50/75/95 % of subjects were found within "
            f"{radii[0]:g}/{radii[1]:g}/{radii[2]:g}/{radii[3]:g} km of the initial planning point"
            + (f" (n={n})" if n is not None else "")
            + f"; {record['url']}."
        )
    else:
        sentences.append(
            f"No published distance model for the {category} category; the region uses the "
            f"explicitly stated {radius_m:g} m radius with uniform density."
        )
    if bearing_deg is None:
        sentences.append("No direction of travel was reported, so the rings are searched whole.")
    elif half_angle_deg is not None:
        sentences.append(
            f"Direction of travel {direction_name} ({bearing_deg:g}°): the operator-stated "
            f"±{half_angle_deg:g}° wedge holds all modelled mass."
        )
    else:
        table, quartiles = dispersion_table(category)
        proxy = (
            f" No published dispersion table exists for {category}; the {table} table stands in."
            if category in DISPERSION_PROXY
            else ""
        )
        sentences.append(
            f"Direction of travel {direction_name} ({bearing_deg:g}°): 25/50/75/95 % of {table} "
            f"finds lie within ±{quartiles[0]}/{quartiles[1]}/{quartiles[2]}/{quartiles[3]}° "
            f"of the stated direction; {SOURCES[table]['url']}.{proxy}"
        )
    sentences.append(
        f"Cells are {SUB_RING_M:g} m distance bands crossed with dispersion sectors, ranked by "
        f"probability density. Band {planned['index']} holds {len(planned['cells'])} of {total} "
        f"cells, {planned['area_m2'] / 1e4:.1f} ha and {planned['mass']:.0%} of the modelled "
        "mass; other bands are provisional at the same budget."
    )
    return sentences
