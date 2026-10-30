"""Offline decision equivalence of dedicated and combined person requests."""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment


def compare_frame(row, sam_threshold, glimmer_threshold):
    assert 0 <= sam_threshold <= 1 and 0 <= glimmer_threshold <= 1
    assert row["frame_id"] and len(row["input_sha256"]) == 64
    assert set(row["variants"]) == {"dedicated", "combined"}
    people = [
        [d for d in row["variants"][name]["detections"] if d["prompt"] == "person"]
        for name in ("dedicated", "combined")
    ]
    for detections in people:
        for detection in detections:
            box = np.asarray(detection["box_xyxy"], dtype=float)
            assert box.shape == (4,) and np.isfinite(box).all()
            assert (box[:2] >= 0).all() and (box[2:] > box[:2]).all()
            assert box[2] <= 1920 and box[3] <= 1080
            for key in ("sam_confidence", "glimmer_yes", "outside_mass"):
                assert math.isfinite(detection[key]) and 0 <= detection[key] <= 1
            assert detection["sam_confidence"] > sam_threshold
            assert type(detection["qualified"]) is bool
            assert detection["qualified"] == (detection["glimmer_yes"] >= glimmer_threshold)

    ious = np.empty((len(people[0]), len(people[1])))
    for i, first in enumerate(people[0]):
        for j, second in enumerate(people[1]):
            a, b = np.array(first["box_xyxy"]), np.array(second["box_xyxy"])
            intersection = float(
                np.prod(np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])))
            )
            union = float(np.prod(a[2:] - a[:2]) + np.prod(b[2:] - b[:2]) - intersection)
            ious[i, j] = intersection / union
    assert np.isfinite(ious).all() and ((ious >= 0) & (ious <= 1)).all()
    first_ids, second_ids = linear_sum_assignment(-ious)
    pairs = []
    for i, j in zip(first_ids, second_ids):
        a, b = people[0][i], people[1][j]
        pairs.append(
            {
                "dedicated_index": int(i),
                "combined_index": int(j),
                "iou": float(ious[i, j]),
                "score_difference": abs(a["sam_confidence"] - b["sam_confidence"]),
                "max_box_coordinate_difference_px": float(
                    np.max(np.abs(np.array(a["box_xyxy"]) - b["box_xyxy"]))
                ),
                "glimmer_probability_difference": abs(a["glimmer_yes"] - b["glimmer_yes"]),
                "same_qualification": a["qualified"] == b["qualified"],
                "dedicated_qualified": a["qualified"],
                "combined_qualified": b["qualified"],
            }
        )
    counts = [len(detections) for detections in people]
    qualified = [sum(d["qualified"] for d in detections) for detections in people]
    return {
        "frame_id": row["frame_id"],
        "input_sha256": row["input_sha256"],
        "person_candidates": counts,
        "qualified_candidates": qualified,
        "unqualified_candidates": [n - k for n, k in zip(counts, qualified)],
        "unmatched_candidates": [n - len(pairs) for n in counts],
        "same_frame_decision": (qualified[0] > 0) == (qualified[1] > 0),
        "equivalent": counts[0] == counts[1]
        and all(
            p["same_qualification"] and p["iou"] >= 0.95 and p["score_difference"] <= 0.01
            for p in pairs
        ),
        "pairs": pairs,
    }


def compare(rows, sam_threshold, glimmer_threshold):
    assert rows and len({row["frame_id"] for row in rows}) == len(rows)
    frames = [compare_frame(row, sam_threshold, glimmer_threshold) for row in rows]
    pairs = [pair for frame in frames for pair in frame["pairs"]]
    assert pairs, "Equivalence requires observed person detections"
    return {
        "frames": len(frames),
        "all_equivalent": all(frame["equivalent"] for frame in frames),
        "sam_threshold_strictly_greater_than": sam_threshold,
        "glimmer_threshold_at_least": glimmer_threshold,
        "minimum_required_iou": 0.95,
        "maximum_allowed_score_difference": 0.01,
        "matching": "Maximum total person-box IoU assignment; every returned person candidate must have a counterpart and retain its qualification",
        "minimum_observed_iou": min(pair["iou"] for pair in pairs),
        "maximum_observed_iou_loss": max(1 - pair["iou"] for pair in pairs),
        "maximum_observed_score_difference": max(pair["score_difference"] for pair in pairs),
        "maximum_observed_box_coordinate_difference_px": max(
            pair["max_box_coordinate_difference_px"] for pair in pairs
        ),
        "maximum_observed_glimmer_probability_difference": max(
            pair["glimmer_probability_difference"] for pair in pairs
        ),
        "matched_candidates": len(pairs),
        "unmatched_candidates": [
            sum(frame["unmatched_candidates"][i] for frame in frames) for i in (0, 1)
        ],
        "changed_candidate_decisions": sum(not pair["same_qualification"] for pair in pairs),
        "changed_frame_decisions": sum(not frame["same_frame_decision"] for frame in frames),
        "failed_frame_ids": [frame["frame_id"] for frame in frames if not frame["equivalent"]],
        "comparisons": frames,
    }
