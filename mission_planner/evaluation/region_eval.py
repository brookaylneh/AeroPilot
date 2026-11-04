"""Deterministic band containment of the authored subject for every public briefing."""

import argparse
import json
import time
from pathlib import Path

from shapely import Point, Polygon

from mission_planner.environment import load_env
from mission_planner.evaluation import briefs, truth
from mission_planner.gcs.fences import default_fences, fence_items
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.region import compass_degrees, direction_limit_deg, ipp_geometry, placement
from mission_planner.gcs.rejection import PlanRejected
from mission_planner.gcs.tasking import plan
from mission_planner.gcs.test_planner import states
from world.cosys.landmarks import load


def evaluate_case(brief, subject, landmarks, fences, fleet, automatic_proposals):
    """Bands of the first plan for this brief and which one holds the subject."""
    try:
        parsed = briefs.parse(brief, landmarks)
    except AssertionError as error:
        # Acceptance fixtures share the briefing directory; they are reported, not planned.
        return {
            "automatic_proposals": automatic_proposals,
            "rejection": {"rule": "brief", "message": str(error)},
        }
    request = briefs.tasking(parsed, automatic_proposals)
    ipp = ipp_geometry(next(item for item in landmarks if item["id"] == parsed["landmark_id"]))
    point = Point(subject["north_m"], subject["east_m"])
    row = {**parsed, "automatic_proposals": automatic_proposals, "distance_m": ipp.distance(point)}
    started = time.perf_counter()
    try:
        receipt = plan(request, 1, fleet, fences, landmarks, (), [])
    except PlanRejected as error:
        return {**row, "rejection": error.rejection, "seconds": time.perf_counter() - started}
    bands = receipt["region"]["bands"]
    first = Polygon([(p["north_m"], p["east_m"]) for p in bands[0]["polygon"]])
    distance_m, bearing_deg, off_deg = placement(ipp, compass_degrees(parsed["direction"]), first)
    containing = next(
        (
            band["index"]
            for band in bands
            if Polygon([(p["north_m"], p["east_m"]) for p in band["polygon"]]).covers(point)
        ),
        None,
    )
    return {
        **row,
        "rejection": None,
        "seconds": time.perf_counter() - started,
        "bands": len(bands),
        "band_1_area_ha": bands[0]["area_m2"] / 1e4,
        "band_1_mass": bands[0]["mass"],
        "band_1_distance_m": distance_m,
        "band_1_bearing_deg": bearing_deg,
        "band_1_off_direction_deg": off_deg,
        "band_1_touches_ipp": first.distance(ipp) < 100,
        "band_1_in_direction": off_deg <= direction_limit_deg(parsed["category"]),
        "containing_band": containing,
        "coverage_percent": receipt["coverage"]["survey_percent"],
        "min_headroom_s": min(row["headroom_s"] for row in receipt["fit"].values()),
        "longest_flight_s": max(m["duration_s"] for m in receipt["missions"].values()),
    }


def run(output):
    load_env()
    output.mkdir(parents=True, exist_ok=False)
    landmarks = load()
    fences = {name: fence_items(default_fences()) for name in NAMES}
    rows = []
    for case_id, name, brief in truth.briefings():
        subject = truth.subject_position(case_id)
        for automatic_proposals in (False, True):
            row = evaluate_case(brief, subject, landmarks, fences, states(), automatic_proposals)
            rows.append({"case_id": case_id, "case_name": name, **row})
            print(
                f"{case_id} proposals={automatic_proposals} "
                + (
                    f"rejected {row['rejection']['rule']}"
                    if row["rejection"]
                    else f"band={row['containing_band']} of {row['bands']} "
                    f"band1={row['band_1_area_ha']:.1f} ha {row['band_1_off_direction_deg']:.0f}° "
                    f"off {row['seconds']:.1f} s"
                ),
                flush=True,
            )
    summary = {}
    for automatic_proposals in (False, True):
        subset = [r for r in rows if r["automatic_proposals"] == automatic_proposals]
        summary[str(automatic_proposals)] = {
            "cases": len(subset),
            "rejected": sum(r["rejection"] is not None for r in subset),
            "in_band_1": sum(r.get("containing_band") == 1 for r in subset),
            "in_band_1_or_2": sum((r.get("containing_band") or 99) <= 2 for r in subset),
            "in_any_band": sum(r.get("containing_band") is not None for r in subset),
            "band_1_in_direction": sum(r.get("band_1_in_direction") is True for r in subset),
            "band_1_touches_ipp": sum(r.get("band_1_touches_ipp") is True for r in subset),
            "max_band_1_off_direction_deg": max(
                (r["band_1_off_direction_deg"] for r in subset if r["rejection"] is None),
                default=None,
            ),
            "unparsed_briefs": sum(
                r["rejection"] is not None and r["rejection"]["rule"] == "brief" for r in subset
            ),
            "median_band_1_ha": sorted(
                r["band_1_area_ha"] for r in subset if r["rejection"] is None
            )[len([r for r in subset if r["rejection"] is None]) // 2],
        }
    (output / "region_eval.json").write_text(
        json.dumps(
            {
                "fleet": "four landed drones at 100 % battery, default fences, disclosed defaults",
                "summary": summary,
                "cases": rows,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
