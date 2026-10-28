"""Mission search state and compact agent results; grids are browser-only payloads."""

import asyncio
import base64
import hashlib
import io

import numpy as np
from PIL import Image

from mission_planner.environment import ROOT
from mission_planner.search.belief import negative_update
from mission_planner.search.coverage import capture_geometry, capture_pod
from mission_planner.search.detection import reduce_calibration
from mission_planner.search.grid import make_grid, polygon_areas, validate_area
from mission_planner.search.planner import encode, plan
from mission_planner.search.prior import TABLES, initialize
from mission_planner.search.subject import explicit_point, validate_profile


class Search:
    def __init__(self, mission):
        self.mission = mission
        self.profile = None
        self.point = None
        self.vertices = None
        self.domain = None
        self.grid = None
        self.probability = None
        self.retained = None
        self.prior_fit = None
        self.model = None
        self.plan = None
        self.status = "unplanned"
        self.revision = 0
        self.heatmap = None
        self.pod = None
        self.captures = {}
        self.footprints = []
        self.negative_count = 0
        self.camera_source = None

    def changed(self):
        self.revision += 1
        self.mission.directory.joinpath("search.json").write_text(encode(self.view()) + "\n")

    def editable(self):
        assert not self.mission.approvals, "Resolve pending approval before changing search inputs"
        assert self.status not in ("starting", "running"), "Search is already executing"
        assert self.negative_count == 0, (
            "A new search hypothesis cannot silently discard processed camera evidence"
        )

    def invalidate(self):
        self.plan = None
        self.status = "unplanned"
        self.grid = None
        self.probability = None
        self.retained = None
        self.prior_fit = None
        self.heatmap = None
        self.pod = None

    def mark_point(self, north, east):
        self.editable()
        assert np.isfinite([north, east]).all()
        from mission_planner.gcs.geodesy import point_in_polygon

        assert self.mission.fleet_ready
        fence = self.mission.vehicles()[0]["fence"]
        assert point_in_polygon({"north_m": north, "east_m": east}, fence), (
            "Last known point outside read-back fence"
        )
        self.point = {"north_m": north, "east_m": east, "source": "operator", "evidence": []}
        self.invalidate()
        self.changed()
        self.mission.event(
            "operator", "Operator marked the last known point", result=self.point, source="operator"
        )

    def initialize(self):
        assert self.profile is not None, "Record subject profile first"
        assert self.point is not None, "Operator must mark the last known point on the map"
        assert self.vertices is not None, "Define the search area and statistical domain"
        assert self.mission.fleet_ready, "MAVLink fleet unavailable"
        if self.grid is None:
            self.grid = make_grid(self.mission.vehicles()[0]["fence"])
            self.probability, self.retained, self.prior_fit = initialize(
                self.grid, self.point, self.profile["category"]["value"], self.domain
            )
            self.pod = np.zeros_like(self.probability)
            self.model = reduce_calibration(ROOT / ".local/data/calibration")
            self.render_heatmap()

    def render_heatmap(self):
        ratio = self.probability / float(self.probability.max())
        rgba = np.column_stack(
            [np.full(len(ratio), 237), 80 + ratio * 110, np.full(len(ratio), 45), ratio * 130]
        ).astype("uint8")
        image = Image.fromarray(rgba.reshape(*self.grid["shape"], 4)[::-1], mode="RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.heatmap = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def inputs(self):
        return {
            "profile": self.profile,
            "point": self.point,
            "vertices": self.vertices,
            "domain": self.domain,
            "calibration": self.model["input_sha256"],
            "readbacks": {n: v["fence"] for n, v in self.mission.fleet.vehicles.items()},
            "probability_sha256": hashlib.sha256(self.probability.tobytes()).hexdigest(),
        }

    def summary(self):
        result = {
            "profile_recorded": self.profile is not None,
            "last_known_point": self.point,
            "area_defined": self.vertices is not None,
            "domain": self.domain,
            "status": self.status,
            "retained_in_world_mass": self.retained,
            "coverage": self.coverage_summary(),
        }
        if self.probability is not None:
            top = np.argsort(-self.probability, kind="stable")[:3]
            result["top_cells"] = [
                {
                    "north_m": float(self.grid["centers"][i, 0]),
                    "east_m": float(self.grid["centers"][i, 1]),
                    "probability": float(self.probability[i]),
                }
                for i in top
            ]
        if self.plan is not None:
            poa = float(
                self.probability
                @ (
                    polygon_areas(self.grid, self.vertices)
                    / np.prod(self.grid["high"] - self.grid["low"], axis=1)
                )
            )
            result["plan"] = {
                k: self.plan[k]
                for k in ("plan_id", "pos", "duration_s", "spacing_m", "axis", "provisional")
            }
            result["plan"].update(
                poa=poa,
                pod=self.plan["pos"] / poa,
                aircraft={
                    n: {k: r[k] for k in ("length_m", "airborne_seconds", "reserve_seconds")}
                    for n, r in self.plan["routes"].items()
                },
                expectation="Conditional on target inside world; stationary, provisional detection model",
            )
        return result

    def coverage_summary(self):
        return {
            "source": self.camera_source,
            "message": "Coverage has no camera source"
            if self.camera_source is None
            else "Capture geometry and explicitly processed negatives",
            "captures": len(self.captures),
            "negative_observations": self.negative_count,
        }

    def view(self):
        grid = (
            None
            if self.grid is None
            else {
                "shape": self.grid["shape"],
                "cell_m": self.grid["cell_m"],
                "probability_f32_le": base64.b64encode(
                    self.probability.astype("<f4").tobytes()
                ).decode(),
            }
        )
        return {
            "revision": self.revision,
            "profile": self.profile,
            "point": self.point,
            "vertices": self.vertices,
            "domain": self.domain,
            "domains": [
                d
                for c, d in TABLES
                if self.profile is not None and c == self.profile["category"]["value"]
            ],
            "heatmap": self.heatmap,
            "grid": grid,
            "plan": self.plan,
            "summary": self.summary(),
            "footprints": self.footprints,
        }

    async def execute(self, name, arguments):
        if name == "record_subject_profile":
            self.editable()
            validate_profile(arguments, self.mission.case["report"])
            self.profile = arguments
            p = explicit_point(arguments)
            if p is not None and (self.point is None or self.point["source"] != "operator"):
                from mission_planner.gcs.geodesy import BOUND_M

                assert abs(p["north_m"]) <= BOUND_M and abs(p["east_m"]) <= BOUND_M
                self.point = p
            self.invalidate()
            self.changed()
            return self.summary()
        if name == "define_search_area":
            self.editable()
            assert self.mission.fleet_ready
            validate_area(arguments["vertices"], self.mission.vehicles()[0]["fence"])
            self.vertices = arguments["vertices"]
            self.domain = arguments["domain"]
            self.invalidate()
            self.changed()
            return self.summary()
        if name == "get_coverage":
            return self.coverage_summary()
        self.initialize()
        if name == "plan_search":
            self.editable()
            inputs = self.inputs()
            states = {v["id"]: v for v in self.mission.vehicles()}
            result = await asyncio.to_thread(
                plan,
                self.grid,
                self.probability,
                self.vertices,
                states,
                inputs["readbacks"],
                self.model,
                inputs,
            )
            assert inputs == self.inputs(), "Search inputs changed during planning"
            self.plan = result
            self.status = "planned"
        else:
            assert name == "get_probability_map"
        self.changed()
        return self.summary()

    def require_plan(self, plan_id):
        assert self.plan is not None and self.plan["plan_id"] == plan_id, (
            "Unknown or stale search plan"
        )
        assert self.status == "planned", "Search plan is not awaiting execution"
        assert self.plan["inputs"] == self.inputs(), "Search inputs changed since planning"
        return self.plan

    def consume_capture(self, frame, outcome):
        assert outcome in ("unprocessed", "negative")
        key = f"{frame['vehicleId']}:{frame['timestampNs']}"
        assert key not in self.captures, "Duplicate capture record"
        assert isinstance(frame["sha256"], str) and len(frame["sha256"]) == 64
        if outcome == "unprocessed":
            _, footprint = capture_geometry(frame)
        else:
            self.initialize()
            pd, footprint = capture_pod(frame, self.grid, self.model)
            self.probability, self.pod = negative_update(self.probability, self.pod, pd)
            self.negative_count += 1
            self.render_heatmap()
        self.captures[key] = frame["sha256"]
        self.camera_source = "Cosys capture records"
        self.footprints.append({"id": key, "vertices": footprint, "outcome": outcome})
        self.changed()
