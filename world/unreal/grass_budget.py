"""Conservative GPU Scene allocation bounds for partitioned runtime grass."""

import math

LIMIT = 12_000_000
RUNTIME_RESERVE = 65_536


def cell_bound(radius, grid):
    assert radius > 0 and grid > 0
    radius = radius / grid + 1e-9
    extent = math.ceil(radius) + 1
    largest = 0
    # Cover every possible source position in a cell with closed 1/32-cell boxes.
    # Counting cells intersecting each box expanded by the radius is an upper bound
    # for every camera position in that box, including exact grid/radius boundaries.
    for i in range(32):
        for j in range(32):
            count = 0
            for x in range(-extent, extent + 1):
                dx = max(x - (i + 1) / 32, i / 32 - x - 1, 0)
                if dx > radius:
                    continue
                dy = math.sqrt(radius * radius - dx * dx)
                count += math.floor((j + 1) / 32 + dy) - math.ceil(j / 32 - dy - 1) + 1
            largest = max(largest, count)
    return largest


def scene_budget(grass, vehicle_count, authored_instances, generating_components):
    assert vehicle_count == 4 and authored_instances > 0 and generating_components > 0
    assert set(grass["profiles"]) == {"studio", "mission"}
    profiles = {}
    for name, bands in grass["profiles"].items():
        sources = 1 if name == "studio" else vehicle_count
        rows = []
        for band in bands:
            points = sum(
                math.ceil(band["gridM"] ** 2 * row["per100m2"] / 100 * band["density"])
                for entry in grass["types"].values()
                for row in entry["varieties"]
            )
            cells = cell_bound(band["reachM"] * grass["cleanup"], band["gridM"])
            rows.append({"gridM": band["gridM"], "cellsPerSource": cells, "pointsPerCell": points})
        resident = sources * sum(row["cellsPerSource"] * row["pointsPerCell"] for row in rows)
        in_flight = generating_components * max(row["pointsPerCell"] for row in rows)
        total = resident + in_flight + authored_instances + RUNTIME_RESERVE
        profiles[name] = {
            "sources": sources,
            "bands": rows,
            "residentGrass": resident,
            "inFlightGrass": in_flight,
            "sceneInstances": total,
        }
    report = {
        "limit": LIMIT,
        "authoredInstances": authored_instances,
        "runtimeReserve": RUNTIME_RESERVE,
        "profiles": profiles,
        "worstCaseSceneInstances": max(p["sceneInstances"] for p in profiles.values()),
    }
    assert report["worstCaseSceneInstances"] <= LIMIT, report
    return report
