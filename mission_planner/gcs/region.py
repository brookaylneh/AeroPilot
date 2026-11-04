"""Doctrine-sized search regions: distance rings, direction sectors and density-ordered bands."""

import math

import numpy as np
import shapely
from shapely import LineString, Point, Polygon, STRtree, box, union_all

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
# Witness points along a linear or polygon initial planning point.
SAMPLE_M = 20.0
# Grid squares take the density at their centre; a square is a quarter of a 20 m sweep row.
CELL_M = 50.0
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
    """Witness positions along the initial planning point every SAMPLE_M; polygons use their
    boundary, a point is its own witness."""
    line = geometry.exterior if geometry.geom_type == "Polygon" else geometry
    if line.geom_type == "Point":
        return np.array([[line.x, line.y]])
    count = max(1, math.ceil(line.length / SAMPLE_M))
    return np.array(
        [line.interpolate(i / count, normalized=True).coords[0] for i in range(count + 1)]
    )


def offsets(geometry, points):
    """Distance of each (north, east) point from the initial planning point and the bearing from
    its nearest point there; a point on or inside the geometry has distance 0 and bearing 0."""
    targets = shapely.points(np.asarray(points, dtype=float))
    distance = shapely.distance(geometry, targets)
    ends = shapely.get_coordinates(shapely.shortest_line(geometry, targets)).reshape(-1, 2, 2)
    bearing = np.degrees(np.arctan2(ends[:, 1, 1] - ends[:, 0, 1], ends[:, 1, 0] - ends[:, 0, 0]))
    return distance, np.where(distance > 0, bearing % 360, 0.0)


def angle_off(bearing_deg, direction_deg):
    """Unsigned angle between a bearing and the stated direction of travel, 0–180°."""
    return np.abs(((np.asarray(bearing_deg, dtype=float) - direction_deg + 180) % 360) - 180)


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


def point_density(category, radius_m, bearing_deg, half_angle_deg, distance, off):
    """Probability density (per m²) of the published point model at a distance from the witness
    point and an unsigned angle off the stated direction; uniform inside each quartile ring, with
    the dispersion share of each sector spread over its angular width on both sides."""
    if category in DISTANCE_KM:
        radii = np.array([0.0, *(r * 1000 for r in DISTANCE_KM[category][2])])
        radii = np.append(radii, math.sqrt(2) * radii[-1])
        mass = np.diff(distance_cdf(category, None, radii))
    else:
        radii = np.array([0.0, float(radius_m)])
        mass = np.array([1.0])
    ring_density = mass / (math.pi * np.diff(radii**2))
    quartile = np.minimum(np.searchsorted(radii[1:], distance, side="right"), len(mass) - 1)
    density = np.where(distance < radii[-1], ring_density[quartile], 0.0)
    if bearing_deg is None:
        return density
    if half_angle_deg is not None:
        return np.where(off <= half_angle_deg, density * 360 / (2 * half_angle_deg), 0.0)
    edges = np.array(sector_edges(category))
    shares = np.array([dispersion_mass(category, a0, a1) for a0, a1 in zip(edges, edges[1:])])
    sector = np.minimum(np.searchsorted(edges[1:], off, side="right"), len(shares) - 1)
    return density * shares[sector] / (2 * np.diff(edges)[sector] / 360)


def cells(geometry, bearing_deg, category, radius_m, area, half_angle_deg=None):
    """Searchable cells: CELL_M squares with the probability density of the published model
    averaged over witness points along the initial planning point.

    The published distance rings and dispersion sectors describe one witness point. A trail or
    an outline is every point on it, so a square's density is the mean of the point model over
    witness points every SAMPLE_M, measured from each as distance and angle off the stated
    direction. Densities are scaled so the squares within reach carry exactly the model's mass
    within reach; fence clipping then removes mass with the area. Each square also records its
    nearest distance band and sector for ordering and reporting. area is the searchable polygon;
    an operator-stated half_angle_deg replaces the published sectors with one wedge holding all
    modelled mass.
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
    angles = (
        (0.0, 180.0)
        if bearing_deg is None
        else (0.0, float(half_angle_deg))
        if half_angle_deg is not None
        else sector_edges(category)
    )
    min_n, min_e, max_n, max_e = geometry.bounds
    norths = np.arange(math.floor((min_n - reach) / CELL_M) * CELL_M, max_n + reach, CELL_M)
    easts = np.arange(math.floor((min_e - reach) / CELL_M) * CELL_M, max_e + reach, CELL_M)
    corners = np.array([(n, e) for n in norths for e in easts])
    centres = corners + CELL_M / 2
    witnesses = samples(geometry)
    delta = centres[:, None, :] - witnesses[None, :, :]
    distance = np.hypot(delta[..., 0], delta[..., 1])
    off = np.zeros_like(distance)
    if bearing_deg is not None:
        off = angle_off(np.degrees(np.arctan2(delta[..., 1], delta[..., 0])), bearing_deg)
    density = point_density(category, radius_m, bearing_deg, half_angle_deg, distance, off).mean(
        axis=1
    )
    nearest, bearing = offsets(geometry, centres)
    nearest_off = np.where(nearest > 0, angle_off(bearing, bearing_deg or 0.0), 0.0)
    keep = (nearest < reach) & (nearest_off <= angles[-1]) & (density > 0)
    density *= float(distance_cdf(category, radius_m, reach)) / float(
        (density[keep] * CELL_M**2).sum()
    )
    ring = np.minimum((nearest // SUB_RING_M).astype(int), len(edges) - 2)
    sector = np.searchsorted(np.array(angles[1:-1]), nearest_off, side="right")
    members = sorted(np.flatnonzero(keep), key=lambda i: (nearest[i], -density[i]))
    squares = shapely.box(
        corners[members, 0],
        corners[members, 1],
        corners[members, 0] + CELL_M,
        corners[members, 1] + CELL_M,
    )
    result = []
    for index, clipped in zip(members, shapely.intersection(squares, area)):
        parts = [clipped] if clipped.geom_type == "Polygon" else list(clipped.geoms)
        for part in parts:
            if part.geom_type != "Polygon" or part.area < MIN_CELL_M2:
                continue
            r, s = int(ring[index]), int(sector[index])
            result.append(
                {
                    "ring": r,
                    "sector": s,
                    "inner_m": float(edges[r]),
                    "outer_m": float(edges[r + 1]),
                    "angle_deg": [angles[s], angles[s + 1]],
                    "distance_m": float(nearest[index]),
                    "polygon": part,
                    "mass": float(density[index]) * part.area,
                    "density": float(density[index]),
                }
            )
    assert result, "No searchable cell lies within reach of the initial planning point"
    return result


def placement(geometry, bearing_deg, polygon):
    """A band's centroid: distance from the initial planning point, bearing from its nearest
    point and, with a stated direction, the unsigned angle off that direction."""
    centroid = polygon.centroid
    distance, bearing = offsets(geometry, [[centroid.x, centroid.y]])
    off = None
    if bearing_deg is not None:
        off = 0.0 if distance[0] == 0 else float(angle_off(bearing[0], bearing_deg))
    return float(distance[0]), float(bearing[0]), off


def direction_limit_deg(category):
    """The 75 % dispersion quantile: the first band must centre within it."""
    return float(dispersion_table(category)[1][2])


def check_direction(geometry, bearing_deg, category, band, index):
    """The first band of a directed search touches the first distance band of the initial planning
    point and centres within the 75 % dispersion angle of the stated direction; anything else is a
    structured rejection."""
    distance, bearing, off = placement(geometry, bearing_deg, band["polygon"])
    limit = direction_limit_deg(category)
    if off > limit or band["polygon"].distance(geometry) >= SUB_RING_M:
        raise PlanRejected(
            "band_direction",
            f"Band {index} centres {distance:.0f} m from the initial planning point at bearing "
            f"{bearing:.0f}°, {off:.0f}° off the stated direction of travel ({bearing_deg:g}°); "
            f"the first band must touch the first {SUB_RING_M:g} m and centre within ±{limit:g}°",
            detail={
                "band_index": index,
                "distance_m": distance,
                "bearing_deg": bearing,
                "off_direction_deg": off,
                "limit_deg": limit,
            },
        )


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

    Each band seeds on the highest-priority unassigned cell and repeatedly adds the
    highest-priority cell touching it, so squares that become adjacent later still join. Enclosed
    unsearched cells join the surrounding band; already searched polygons stay out, so a band
    wrapping a searched core keeps that core as a hole.
    """
    assert budget_s > 0 and rate_m2_per_s > 0
    done = union_all(
        [Polygon([(p["north_m"], p["east_m"]) for p in points]) for points in searched]
    ).buffer(SEARCHED_GAP_M)
    # Cells of one quartile ring sector share a density up to geometric approximation; such ties
    # (chained within one percent) are searched nearest-first.
    groups = []
    for cell in sorted(cells, key=lambda c: -c["density"]):
        if groups and cell["density"] >= groups[-1][0]["density"] * 0.99:
            groups[-1].append(cell)
        else:
            groups.append([cell])
    order = [
        cell
        for group in groups
        for cell in sorted(group, key=lambda c: (c["inner_m"], c["distance_m"]))
    ]
    polygons = [cell["polygon"] for cell in order]
    tree = STRtree(polygons)
    remaining = set(range(len(order)))
    result = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        chosen, shape, area = [seed], polygons[seed], polygons[seed].area
        while True:
            touching = sorted(
                i for i in tree.query(shape.buffer(1.0), predicate="intersects") if i in remaining
            )
            if not touching or (area + polygons[touching[0]].area) / rate_m2_per_s > budget_s:
                break
            for index in touching:
                merged, leftovers = largest_part(union_all([shape, polygons[index]]))
                if leftovers:
                    # Corner contact only; it joins once an edge neighbour is in the band.
                    continue
                shape, area = merged, area + polygons[index].area
                chosen.append(index)
                remaining.remove(index)
                break
            else:
                break
        # An enclosed lower-priority cell would leave a hole: search it with its band instead.
        shape = Polygon(shape.exterior).difference(done)
        assert shape.geom_type == "Polygon", "A band stays one connected polygon"
        for index in tree.query(shape.buffer(0.1), predicate="contains"):
            if index in remaining:
                chosen.append(index)
                area += polygons[index].area
                remaining.remove(index)
        survey_s = area / rate_m2_per_s
        result.append(
            {
                "index": len(result) + 1,
                "polygon": shape,
                "area_m2": area,
                "mass": sum(order[i]["mass"] for i in chosen),
                "cells": [{k: v for k, v in order[i].items() if k != "polygon"} for i in chosen],
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
        f"Cells are {CELL_M:g} m squares whose probability density averages the published point "
        f"model over witness points every {SAMPLE_M:g} m along the initial planning point, ranked "
        f"by density and searched nearest-first. Band {planned['index']} holds "
        f"{len(planned['cells'])} of {total} "
        f"cells, {planned['area_m2'] / 1e4:.1f} ha and {planned['mass']:.0%} of the modelled "
        "mass; other bands are provisional at the same budget."
    )
    return sentences
