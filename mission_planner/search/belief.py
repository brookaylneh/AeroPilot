"""Maximum-look negatives and one capped positive factor per reference frame."""

import math

import numpy as np
from scipy.special import ndtr

from mission_planner.agents.perception.localization import (
    ELLIPSE_99,
    ellipse_cells,
    ellipse_weights,
    gaussian_cells,
)
from mission_planner.search.grid import area


def validate_probability(probability):
    assert probability.ndim == 1 and np.isfinite(probability).all()
    assert ((probability >= 0) & (probability <= 1)).all()
    assert abs(float(probability.sum()) - 1) < 1e-9


def negative_update(probability, previous_pod, frame_pod):
    validate_probability(probability)
    assert previous_pod.shape == frame_pod.shape == probability.shape
    assert np.isfinite([previous_pod, frame_pod]).all()
    assert ((previous_pod >= 0) & (previous_pod <= 1) & (frame_pod >= 0) & (frame_pod <= 1)).all()
    cumulative = np.maximum(previous_pod, frame_pod)
    assert np.all(probability[previous_pod == 1] == 0), (
        "Contradictory probability after certain negative evidence"
    )
    increment = np.divide(
        cumulative - previous_pod,
        1 - previous_pod,
        out=np.zeros_like(cumulative),
        where=previous_pod < 1,
    )
    denominator = 1 - float(probability @ increment)
    assert denominator > 0, "Negative observation contradicts entire belief"
    updated = probability * (1 - increment) / denominator
    validate_probability(updated)
    return updated, cumulative


def region_probability(region, matrix, grid):
    assert matrix.shape == (2, 2) and np.isfinite(matrix).all() and matrix[0, 0] > 0
    assert np.array_equal(matrix, np.eye(2) * matrix[0, 0])
    sigma = math.sqrt(matrix[0, 0])
    result = np.zeros(len(grid["areas"]))

    def primitive(z):
        return z * ndtr(z) + np.exp(-z * z / 2) / math.sqrt(2 * math.pi)

    for cell in np.flatnonzero(region):
        low, high = grid["low"][cell], grid["high"][cell]
        ids = np.flatnonzero(
            ((grid["high"] >= low - 9 * sigma) & (grid["low"] <= high + 9 * sigma)).all(axis=1)
            & (grid["areas"] > 0)
        )
        a, b = grid["low"][ids], grid["high"][ids]
        axes = (
            sigma
            / (b - a)
            * (
                primitive((high - a) / sigma)
                - primitive((high - b) / sigma)
                - primitive((low - a) / sigma)
                + primitive((low - b) / sigma)
            )
        )
        assert np.isfinite(axes).all() and (axes >= -1e-10).all() and (axes <= 1 + 1e-10).all()
        fraction = grid["areas"][cell] / np.prod(high - low)
        result[ids] += np.prod(np.clip(axes, 0, 1), axis=1) * fraction
    assert np.isfinite(result).all() and (result >= 0).all() and result.max() <= 1 + 1e-8
    return np.minimum(result, 1.0)


class Evidence:
    def __init__(self, prior, grid, clutter_rate):
        validate_probability(prior)
        assert prior.shape == grid["areas"].shape
        assert math.isfinite(clutter_rate) and 0 < clutter_rate < 1
        self.prior, self.probability = prior.copy(), prior.copy()
        self.grid, self.clutter_rate = grid, clutter_rate
        self.maximum_pod = np.zeros_like(prior)
        self.reserved = np.zeros(len(prior), dtype=bool)
        self.factors, self.frames, self.images = {}, set(), set()

    def recompute(self):
        values = np.full_like(self.prior, -np.inf)
        np.log(self.prior, out=values, where=self.prior > 0)
        survival = 1 - self.maximum_pod
        logs = np.full_like(values, -np.inf)
        np.log(survival, out=logs, where=survival > 0)
        values += logs
        for factor in self.factors.values():
            likelihood = factor["likelihood"]
            logs = np.full_like(values, -np.inf)
            np.log(likelihood, out=logs, where=likelihood > 0)
            values += logs
        maximum = float(values.max())
        assert math.isfinite(maximum), "Camera evidence contradicts the entire prior"
        weights = np.exp(values - maximum)
        self.probability = weights / float(weights.sum())
        validate_probability(self.probability)

    def rebuild(self, factor, ledger):
        active = {
            identity: value
            for identity, value in factor["clusters"].items()
            if ledger.clusters[identity]["status"] != "dismissed"
        }
        factor["active_cluster_ids"] = sorted(active)
        factor["active_region_cells"] = []
        if not active:
            factor["likelihood"] = np.ones_like(self.prior)
            return
        region = np.logical_or.reduce([value["region"] for value in active.values()])
        factor["active_region_cells"] = np.flatnonzero(region).tolist()
        h = region_probability(region, np.array(factor["covariance_m2"]), self.grid)
        density = np.sum(
            [value["kernel"] * value["retained_mass"] for value in active.values()], axis=0
        )
        density = np.divide(
            density, self.grid["areas"], out=np.zeros_like(density), where=self.grid["areas"] > 0
        )
        kappa = -math.log1p(-self.clutter_rate) / factor["area_m2"]
        likelihood = 1 - factor["pod"] * h + factor["pod"] * density / kappa
        assert np.isfinite(likelihood).all() and (likelihood >= 0).all()
        factor["likelihood"] = likelihood

    def apply(self, frame_id, pod, footprint, ledger, calibrated):
        assert frame_id not in self.frames and frame_id in ledger.frames
        assert isinstance(calibrated, bool)
        assert pod.shape == self.prior.shape and np.isfinite(pod).all()
        assert ((pod >= 0) & (pod <= 1)).all()
        before = self.probability.copy()
        records = [ledger.records[identity] for identity in ledger.frames[frame_id]]
        qualified = [d for d in records if d["calibrated"] and d["qualified"]]
        assert calibrated or not qualified
        self.frames.add(frame_id)
        credited, kind = [], "uncalibrated"
        if calibrated and not qualified:
            self.maximum_pod = np.maximum(self.maximum_pod, pod)
            kind = "negative"
        elif calibrated:
            hashes = {d["input_sha256"] for d in qualified}
            assert len(hashes) == 1
            image_hash = next(iter(hashes))
            representatives = {}
            old = self.reserved.copy()
            for record in qualified:
                cluster = ledger.clusters[record["cluster_id"]]
                if cluster["reference_frame_id"] == frame_id:
                    if cluster["id"] not in representatives:
                        representatives[cluster["id"]] = record
                else:
                    old |= ellipse_weights(record["localization"], self.grid, ELLIPSE_99) > 0
            kind = "repeated"
            if representatives and image_hash not in self.images:
                variance = max(
                    float(np.linalg.eigvalsh(d["localization"]["covariance_m2"]).max())
                    for d in representatives.values()
                )
                matrix = np.eye(2) * variance
                members = {}
                for identity, record in representatives.items():
                    region = (
                        ellipse_weights(record["localization"], self.grid, ELLIPSE_99) > 0
                    ) & ~old
                    point = np.array(
                        [record["localization"]["ground"][k] for k in ("north_m", "east_m")]
                    )
                    contains = ((self.grid["low"] <= point) & (point < self.grid["high"])).all(
                        axis=1
                    )
                    if not (region & contains).any():
                        continue
                    kernel, retained = gaussian_cells(point, matrix, self.grid)
                    members[identity] = {
                        "record_id": record["id"],
                        "region": region,
                        "kernel": kernel,
                        "retained_mass": retained,
                    }
                if members:
                    region = np.logical_or.reduce([value["region"] for value in members.values()])
                    positive_pod = pod[pod > 0]
                    factor = {
                        "clusters": members,
                        "pod": pod.copy(),
                        "area_m2": area([(p["north_m"], p["east_m"]) for p in footprint]),
                        "region_cells": np.flatnonzero(region).tolist(),
                        "covariance_m2": matrix.tolist(),
                        "detection_probability_range": (
                            [float(positive_pod.min()), float(positive_pod.max())]
                            if len(positive_pod)
                            else [0.0, 0.0]
                        ),
                    }
                    assert factor["area_m2"] > 0
                    self.rebuild(factor, ledger)
                    self.factors[frame_id] = factor
                    self.reserved |= region
                    credited, kind = sorted(members), "positive"
            self.images.add(image_hash)
        self.recompute()
        return {
            "frame_id": frame_id,
            "kind": kind,
            "credited_cluster_ids": credited,
            "probability_change": float(np.abs(self.probability - before).sum() / 2),
        }

    def resolve(self, ledger):
        before = self.probability.copy()
        for factor in self.factors.values():
            self.rebuild(factor, ledger)
        self.recompute()
        return {"probability_change": float(np.abs(self.probability - before).sum() / 2)}

    def mass(self, localization):
        region = ellipse_cells(localization, self.grid)
        weights = ellipse_weights(localization, self.grid)
        region_area = float(self.grid["areas"][region].sum())
        return {
            "prior_mass": float(self.prior[region].sum()),
            "posterior_mass": float(self.probability[region].sum()),
            "region_cell_count": int(region.sum()),
            "region_area_m2": region_area,
            "ellipse_prior_mass": float(self.prior @ weights),
            "ellipse_posterior_mass": float(self.probability @ weights),
            "ellipse_area_fraction": float(weights @ self.grid["areas"]) / region_area
            if region_area > 0
            else 0.0,
        }

    def detail(self, cluster_id):
        return [
            {
                "frame_id": frame_id,
                "cluster_ids": list(factor["clusters"]),
                "region_cells": factor["region_cells"],
                "active_cluster_ids": factor["active_cluster_ids"],
                "active_region_cells": factor["active_region_cells"],
                "covariance_m2": factor["covariance_m2"],
                "detection_probability_range": factor["detection_probability_range"],
                "retained_kernel_mass": {
                    identity: value["retained_mass"]
                    for identity, value in factor["clusters"].items()
                },
                "outside_grid_kernel_mass": {
                    identity: max(0.0, 1 - value["retained_mass"])
                    for identity, value in factor["clusters"].items()
                },
                "false_alarm_rate": self.clutter_rate,
                "footprint_area_m2": factor["area_m2"],
            }
            for frame_id, factor in self.factors.items()
            if cluster_id in factor["clusters"]
        ]
