"""Ground-space evidence membership and operator-owned dispositions."""

import copy
import math

import numpy as np

from mission_planner.agents.perception.analyst import DECISIONS
from mission_planner.agents.perception.localization import ELLIPSE_99, covariance


def escalated(record):
    return record["calibrated"] and record["decision"] == "escalate"


def position(record):
    return np.array([record["localization"]["ground"][k] for k in ("north_m", "east_m")])


def separation(a, b):
    delta = position(a) - position(b)
    matrix = covariance(a["localization"]["covariance_m2"]) + covariance(
        b["localization"]["covariance_m2"]
    )
    distance = float(delta @ np.linalg.solve(matrix, delta))
    assert math.isfinite(distance) and distance >= 0
    return distance


class Detections:
    def __init__(self):
        self.records = {}
        self.clusters = {}
        self.frames = {}

    def add_frame(self, frame_id, records):
        assert frame_id not in self.frames, "Frame already committed to detections"
        assert len({d["id"] for d in records}) == len(records)
        records = copy.deepcopy(records)
        for record in records:
            identity = record["id"]
            assert identity and identity not in self.records and record["frame_id"] == frame_id
            assert isinstance(record["calibrated"], bool)
            assert record["decision"] in DECISIONS
            assert record["decision"] == record["report"]["decision"]
            assert record["source"] in ("live", "recorded")
            assert math.isfinite(record["sam_confidence"]) and 0 <= record["sam_confidence"] <= 1
            covariance(record["localization"]["covariance_m2"])
            assert np.isfinite(position(record)).all()
        changed = set()
        self.frames[frame_id] = []
        for record in records:
            identity = record["id"]
            candidates = []
            for cluster_id, cluster in self.clusters.items():
                distances = [
                    separation(record, self.records[member]) for member in cluster["member_ids"]
                ]
                if max(distances) <= ELLIPSE_99:
                    candidates.append((max(distances), cluster_id))
            cluster_id = min(candidates)[1] if candidates else identity
            if not candidates:
                self.clusters[cluster_id] = {
                    "id": cluster_id,
                    "status": "pending",
                    "reason": None,
                    "reference_frame_id": None,
                    "member_ids": [],
                    "representative_id": identity,
                    "localization": record["localization"],
                    "calibrated": False,
                }
            cluster = self.clusters[cluster_id]
            record["cluster_id"] = cluster_id
            self.records[identity] = record
            self.frames[frame_id].append(identity)
            cluster["member_ids"].append(identity)
            if escalated(record):
                cluster["calibrated"] = True
                if cluster["reference_frame_id"] is None:
                    cluster["reference_frame_id"] = frame_id
            changed.add(cluster_id)
        for cluster_id in changed:
            cluster = self.clusters[cluster_id]
            members = [self.records[identity] for identity in cluster["member_ids"]]
            medoid = min(
                members,
                key=lambda a: (
                    sum(float(np.linalg.norm(position(a) - position(b))) for b in members),
                    a["id"],
                ),
            )
            cluster["representative_id"] = medoid["id"]
            cluster["localization"] = medoid["localization"]
        return sorted(changed)

    def cluster(self, detection_id):
        assert detection_id in self.records, "Unknown detection"
        return self.clusters[self.records[detection_id]["cluster_id"]]

    def review(self, detection_id):
        cluster = self.cluster(detection_id)
        return {
            **cluster,
            "members": [self.records[identity] for identity in cluster["member_ids"]],
        }

    def resolve(self, detection_id, disposition, reason, source):
        assert source == "operator", "Only an operator may resolve a detection"
        assert (
            disposition in ("dismissed", "confirmed_lead")
            and isinstance(reason, str)
            and reason.strip()
        )
        cluster = self.cluster(detection_id)
        cluster["status"], cluster["reason"] = disposition, reason.strip()
        return cluster["id"]
