"""Mission search state and compact agent results; grids are browser-only payloads."""

import asyncio
import base64
import hashlib
import io

import numpy as np
from matplotlib import colormaps
from PIL import Image

from mission_planner.search.behavior import DOMAIN, condition, domain_for
from mission_planner.search.commander import Commander
from mission_planner.search.coverage import capture_geometry
from mission_planner.search.grid import make_grid, polygon_areas, validate_area
from mission_planner.search.occlusion import aerial_visibility
from mission_planner.search.planner import encode, plan
from mission_planner.search.prior import initialize
from mission_planner.search.subject import explicit_point, validate_profile
from world.cosys.landmarks import directions, find, load


class Search:
    def __init__(self, mission):
        self.mission = mission
        self.landmarks = load()
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
        self.heatmap_scale = None
        self.pod = None
        self.aerial_visible = None
        self.captures = {}
        self.footprints = []
        self.negative_count = 0
        self.camera_source = None
        self.paused = {}
        self.commander = Commander(self)

    def changed(self):
        self.revision += 1
        self.mission.directory.joinpath("search.json").write_text(encode(self.view()) + "\n")

    def editable(self):
        assert not self.mission.approvals, "Resolve pending approval before changing search inputs"
        assert self.status not in ("starting", "running"), "Search is already executing"
        assert self.negative_count == 0, (
            "A new search hypothesis cannot silently discard processed camera evidence"
        )

    def edit_hypothesis(self):
        self.editable()
        assert self.commander.source is None, "Camera source selection locks search inputs"
        assert self.commander.evidence is None or not self.commander.evidence.ground_checks, (
            "Confirmed ground evidence locks search inputs"
        )

    def invalidate(self):
        assert self.commander.evidence is None or not self.commander.evidence.ground_checks, (
            "A new search hypothesis cannot silently discard confirmed ground evidence"
        )
        self.plan = None
        self.status = "unplanned"
        self.grid = None
        self.probability = None
        self.retained = None
        self.prior_fit = None
        self.heatmap = None
        self.heatmap_scale = None
        self.pod = None
        self.aerial_visible = None
        self.commander.evidence = None

    def mark_point(self, north, east):
        self.edit_hypothesis()
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
        assert self.point is not None, "Operator must mark or approve the last known location"
        assert self.vertices is not None, "Define the search area and statistical domain"
        assert self.mission.fleet_ready, "MAVLink fleet unavailable"
        if self.grid is None:
            self.commander.load_model()
            category = self.profile["category"]["value"]
            assert self.domain == domain_for(category), (
                "Statistical domain differs from Sierra source"
            )
            grid = make_grid(self.mission.vehicles()[0]["fence"])
            probability, retained, fit = initialize(grid, self.point, category, self.domain)
            fit["behavior"] = None
            if category == "dementia":
                probability, fit["behavior"] = condition(grid, probability, self.landmarks)
            visible = aerial_visibility(grid, self.landmarks)
            self.grid, self.probability, self.retained, self.prior_fit = (
                grid,
                probability,
                retained,
                fit,
            )
            self.pod = np.zeros_like(self.probability)
            self.aerial_visible = visible
            self.commander.initialize_evidence()
            self.model = self.commander.model
            self.render_heatmap()

    def render_heatmap(self):
        maximum = float(self.probability.max())
        scale = float(self.probability[self.probability > 0].min())
        span = np.log1p(maximum / scale)
        ratio = np.log1p(self.probability / scale) / span
        assert np.isfinite(ratio).all() and ((ratio >= 0) & (ratio <= 1)).all()
        colourmap = colormaps["viridis"]
        rgba = colourmap(ratio, bytes=True)
        rgba[:, 3] = np.where(self.grid["areas"] > 0, 110, 0)
        self.heatmap_scale = {
            "method": "log1p",
            "maximum": maximum,
            "midpoint": float(scale * np.expm1(span / 2)),
            "scale_probability": scale,
            "colours": [
                "#" + "".join(f"{channel:02x}" for channel in colour[:3])
                for colour in colourmap(np.linspace(0, 1, 9), bytes=True)
            ],
        }
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
            "landmarks_sha256": hashlib.sha256(encode(self.landmarks).encode()).hexdigest(),
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
            "paused_drones": self.paused,
            "pipeline_capacity": self.commander.capacity_view(),
            "behavior_prior": None
            if self.prior_fit is None or self.prior_fit["behavior"] is None
            else {
                key: self.prior_fit["behavior"][key]
                for key in ("method", "unresolved_weight", "limitations")
            },
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
                expectation="Conditional on target inside world; stationary, provisional detection model; zero aerial detection in structure and forest footprints",
            )
        return result

    def directions(self, north_m, east_m):
        return directions(self.landmarks, north_m, east_m)

    def structure_mass(self, place_id, geometry):
        self.initialize()
        return self.commander.evidence.structure_mass(place_id, geometry)

    def confirm_structure_negative(self, delivery_id, place_id, geometry):
        self.initialize()
        result = self.commander.evidence.ground_negative(delivery_id, place_id, geometry)
        self.probability = self.commander.evidence.probability
        if self.status == "planned":
            self.plan, self.status = None, "unplanned"
        self.mission.directory.joinpath("ground-evidence.json").write_text(
            encode(self.commander.evidence.ground_checks) + "\n"
        )
        self.render_heatmap()
        self.changed()
        self.mission.event(
            "ground_evidence",
            "Operator confirmed structure checked negative",
            result={"delivery_id": delivery_id, "place_id": place_id, **result},
            source="operator",
        )
        return result

    def coverage_summary(self):
        ingestion = self.commander.ingestion
        return {
            "source": self.camera_source,
            "message": "Coverage has no camera source"
            if self.camera_source is None
            else "Calibrated person evidence; briefing prompts are uncalibrated leads",
            "captures": len(self.captures),
            "negative_observations": self.negative_count,
            "backlog": None if ingestion is None else ingestion.summary()["lanes"],
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
            "landmarks": self.landmarks,
            "revision": self.revision,
            "profile": self.profile,
            "point": self.point,
            "vertices": self.vertices,
            "domain": self.domain,
            "domains": [DOMAIN[self.profile["category"]["value"]]]
            if self.profile is not None and self.profile["category"]["value"] in DOMAIN
            else [],
            "prior_fit": self.prior_fit,
            "heatmap": self.heatmap,
            "heatmap_scale": self.heatmap_scale,
            "grid": grid,
            "plan": self.plan,
            "summary": self.summary(),
            "footprints": self.footprints,
            "commander": self.commander.view(),
        }

    async def execute(self, name, arguments, source="operator"):
        if name in ("find_place", "describe_location"):
            if name == "find_place":
                return find(self.landmarks, arguments["name"])
            return self.directions(arguments["north_m"], arguments["east_m"])
        if name in ("list_detections", "review_detection", "resolve_detection"):
            return await self.commander.execute(name, arguments, source)
        if name == "record_subject_profile":
            self.edit_hypothesis()
            validate_profile(arguments, self.mission.case["report"])
            self.profile = arguments
            p = explicit_point(arguments)
            if p is not None and (
                self.point is None or self.point["source"] == "briefing_coordinates"
            ):
                from world.cosys.terrain import EAST_MAX_M, EAST_MIN_M, NORTH_MAX_M, NORTH_MIN_M

                assert NORTH_MIN_M <= p["north_m"] <= NORTH_MAX_M
                assert EAST_MIN_M <= p["east_m"] <= EAST_MAX_M
                self.point = p
            self.invalidate()
            self.changed()
            return self.summary()
        if name == "define_search_area":
            self.edit_hypothesis()
            assert self.mission.fleet_ready
            assert self.profile is not None, (
                "Record subject profile before defining the search area"
            )
            assert arguments["domain"] == domain_for(self.profile["category"]["value"]), (
                "Statistical domain differs from Sierra source"
            )
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
                self.landmarks,
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

    def consume_capture(self, frame):
        key = f"{frame['vehicleId']}:{frame['timestampNs']}"
        assert key not in self.captures, "Duplicate capture record"
        assert isinstance(frame["sha256"], str) and len(frame["sha256"]) == 64
        _, footprint = capture_geometry(frame)
        self.captures[key] = frame["sha256"]
        self.footprints.append({"id": key, "vertices": footprint, "outcome": "unprocessed"})
        self.changed()

    def complete_capture(self, result):
        return self.commander.complete_capture(result)

    def review_detection(self, detection_id):
        return self.commander.review(detection_id)
