"""Drawn boundaries, complete flight legs and all-aircraft fence installation."""

import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from mission_planner.gcs.fences import (
    BOUNDARY_CLEARANCE_M,
    default_fences,
    fence_items,
    read_fences,
    rtl_envelope,
    validate_fence_segment,
    validate_fences,
)
from mission_planner.gcs.fleet import MAV, Fleet
from mission_planner.gcs.geodesy import NAMES, homes
from mission_planner.gcs.planner import compile_plan
from mission_planner.gcs.test_planner import airborne, fly, hover, plan, states
from world.cosys.landmarks import load


def point(north, east):
    return {"north_m": north, "east_m": east}


def rectangle(south, west, north, east):
    return [point(south, west), point(south, east), point(north, east), point(north, west)]


def fences():
    return {
        **default_fences(),
        "names": {"inclusion": "Park boundary", "exclusions": ["Bear habitat"]},
        "exclusions": [rectangle(-20, -20, 20, 20)],
    }


class FenceGeometry(unittest.TestCase):
    def test_multiple_polygons_round_trip_with_their_own_counts_and_commands(self):
        value = fences()
        value["exclusions"].append([point(100, 100), point(150, 100), point(100, 150)])
        value["names"]["exclusions"].append("Campground")
        items = fence_items(value)
        self.assertEqual([item["param1"] for item in items], [8] * 8 + [4] * 4 + [3] * 3)
        self.assertEqual(items[8]["command"], MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_EXCLUSION)
        decoded = read_fences(items)
        self.assertEqual(len(decoded["exclusions"]), 2)
        self.assertEqual(fence_items(decoded), items)
        self.assertEqual(validate_fences(value, homes())["exclusions"], decoded["exclusions"])
        self.assertEqual(validate_fences(value, homes())["names"], value["names"])

    def test_names_are_required_nonblank_bounded_and_match_polygon_count(self):
        for names in (
            {"inclusion": "Park", "exclusions": []},
            {"inclusion": "Park", "exclusions": ["Bear", "Extra"]},
            {"inclusion": "", "exclusions": ["Bear"]},
            {"inclusion": "Park", "exclusions": [" "]},
            {"inclusion": "Park", "exclusions": ["A" * 61]},
            {"inclusion": "Park", "exclusions": [None]},
            {"inclusion": "Park", "exclusions": "Bear"},
            {"inclusion": "Park"},
        ):
            with self.subTest(names=names), self.assertRaisesRegex(AssertionError, "name"):
                validate_fences({**fences(), "names": names}, {})
        unnamed = fences()
        del unnamed["names"]
        with self.assertRaisesRegex(AssertionError, "names"):
            validate_fences(unnamed, {})

    def test_line_repeated_vertices_and_crossed_polygon_are_rejected(self):
        for shape, error in (
            ([point(0, 0), point(1, 1)], "unique"),
            ([point(0, 0), point(1, 1), point(2, 2)], "no area"),
            ([point(0, 0), point(10, 0), point(0, 10), point(0, 0)], "unique"),
            ([point(0, 0), point(10, 10), point(0, 10), point(10, 0)], "self-intersects"),
            ([point(0, 0), point(float("nan"), 0), point(0, 10)], "finite"),
        ):
            with self.subTest(shape=shape), self.assertRaisesRegex(AssertionError, error):
                validate_fences({**fences(), "exclusions": [shape]}, {})

    def test_unencoded_polygon_cannot_collapse_when_sent_to_mavlink(self):
        tiny = [point(0, 0), point(0.001, 0), point(0, 0.01)]
        with self.assertRaisesRegex(AssertionError, "Encoded fence.*unique"):
            validate_fences({**fences(), "exclusions": [tiny]}, {})

    def test_map_projection_rounding_is_checked_in_uploaded_coordinates(self):
        value = default_fences()
        original = copy.deepcopy(value)
        value["inclusion"][6]["north_m"] += 0.002
        value["inclusion"][6]["east_m"] += 0.002
        self.assertEqual(validate_fences(value, homes()), original)
        value["inclusion"][6]["north_m"] += 0.02
        with self.assertRaisesRegex(AssertionError, "world boundary"):
            validate_fences(value, homes())

    def test_world_boundary_home_access_and_vertex_budget_are_enforced(self):
        outside = {**default_fences(), "inclusion": rectangle(-900, -1300, 900, 1300)}
        with self.assertRaisesRegex(AssertionError, "world boundary"):
            validate_fences(outside, {})
        with self.assertRaisesRegex(AssertionError, "Drone1 home/launch position"):
            validate_fences({**default_fences(), "inclusion": rectangle(-50, -50, 50, 50)}, homes())
        home = homes()["Drone1"]
        shape = rectangle(
            home["north_m"] - 10, home["east_m"] - 10, home["north_m"] + 10, home["east_m"] + 10
        )
        with self.assertRaisesRegex(AssertionError, "Drone1 home/launch position.*Exclusion 1"):
            validate_fences({**fences(), "exclusions": [shape]}, homes())
        with self.assertRaisesRegex(AssertionError, "70 vertices"):
            validate_fences(
                {
                    **default_fences(),
                    "names": {"inclusion": "Park", "exclusions": ["Zone"] * 16},
                    "exclusions": [rectangle(-20, -20, 20, 20)] * 16,
                },
                {},
            )

    def test_crossings_tangencies_and_hover_inside_exclusions_fail_at_all_altitudes(self):
        for start, end in (
            (point(-50, 0), point(50, 0)),
            (point(-50, 20), point(50, 20)),
            (point(0, 0), point(0, 0)),
        ):
            for altitude in (10, 30, 100):
                with (
                    self.subTest(start=start, altitude=altitude),
                    self.assertRaisesRegex(AssertionError, "Exclusion 1"),
                ):
                    validate_fence_segment(
                        {**start, "altitude_above_home_m": altitude}, end, fences(), "Drone1 step 2"
                    )
        validate_fence_segment(point(-50, 31), point(50, 31), fences(), "Detour")

    def test_boundary_clearance_is_continuous_and_has_an_exact_limit(self):
        value = {
            "inclusion": rectangle(-200, -200, 200, 200),
            "exclusions": [rectangle(-20, -20, 20, 20)],
        }
        validate_fence_segment(point(-50, 20 + BOUNDARY_CLEARANCE_M), point(50, 30), value, "Route")
        with self.assertRaisesRegex(AssertionError, "requires 10 m"):
            validate_fence_segment(point(-50, 29.999), point(50, 29.999), value, "Route")
        with self.assertRaisesRegex(AssertionError, "inclusion fence"):
            validate_fence_segment(point(195, -50), point(195, 50), value, "Route")

    def test_concave_inclusion_rejects_a_leg_with_both_endpoints_inside(self):
        polygon = [
            point(-100, -100),
            point(-100, 100),
            point(100, 100),
            point(100, 20),
            point(0, 20),
            point(0, -20),
            point(100, -20),
            point(100, -100),
        ]
        with self.assertRaisesRegex(AssertionError, "exits the inclusion"):
            validate_fence_segment(
                point(50, -50), point(50, 50), {"inclusion": polygon, "exclusions": []}, "Route"
            )

    def test_malformed_readback_cannot_be_treated_as_an_inclusion_polygon(self):
        for mutation in (
            lambda items: items[0].update(param1=9),
            lambda items: items[2].update(command=16),
            lambda items: items[0].update(param1=2),
            lambda items: items[0].update(param1=3.5),
        ):
            value = fence_items(fences())
            mutation(value)
            with self.assertRaises(AssertionError):
                read_fences(value)

    def test_rtl_envelope_clears_all_terrain_and_obstacle_heights(self):
        for name, home in homes().items():
            with self.subTest(name=name):
                self.assertGreaterEqual(rtl_envelope(home, load())["clearance_m"], 10)

    def test_every_pattern_rejects_a_crossing_even_when_the_destination_is_clear(self):
        boundaries = dict.fromkeys(NAMES, fence_items(fences()))
        movements = [
            fly(100, 0),
            {
                "kind": "follow",
                "points": [point(-100, 0), point(100, 0)],
                "height_m": 40,
                "speed_mps": 5,
            },
            {
                "kind": "survey",
                "polygon": rectangle(-100, -10, 100, 10),
                "spacing_m": 10,
                "bearing_deg": 0,
                "height_m": 40,
                "speed_mps": 5,
            },
            {
                "kind": "circle",
                **point(0, 0),
                "radius_m": 25,
                "height_m": 40,
                "speed_mps": 5,
                "turns": 1,
                "clockwise": True,
            },
        ]
        for movement in movements:
            with (
                self.subTest(kind=movement["kind"]),
                self.assertRaisesRegex(AssertionError, "Exclusion 1"),
            ):
                compile_plan(plan([movement, hover()]), airborne(-100, 0), boundaries, load())
        for step in (hover(), {"kind": "land"}):
            with (
                self.subTest(kind=step["kind"]),
                self.assertRaisesRegex(AssertionError, "Exclusion 1"),
            ):
                compile_plan(plan([step]), airborne(0, 0), boundaries, load())
        grounded = states()
        grounded[0].update(point(0, 0))
        with self.assertRaisesRegex(AssertionError, "Exclusion 1"):
            compile_plan(
                plan([{"kind": "takeoff", "height_m": 40}, hover()]), grounded, boundaries, load()
            )

    def test_explicit_detour_and_circle_around_exclusion_pass_without_repair(self):
        for steps in (
            [fly(-100, 100), fly(100, 100), fly(100, 0), hover()],
            [
                {
                    "kind": "circle",
                    **point(0, 0),
                    "radius_m": 100,
                    "height_m": 40,
                    "speed_mps": 5,
                    "turns": 1,
                    "clockwise": True,
                },
                hover(),
            ],
        ):
            requested = plan(steps)
            receipt = compile_plan(
                requested, airborne(-100, 0), dict.fromkeys(NAMES, fence_items(fences())), load()
            )
            self.assertEqual(receipt["plan"], requested)

    def test_planned_and_operator_returns_cannot_cross_an_exclusion(self):
        current = airborne(100, 0)
        value = {**fences(), "exclusions": [rectangle(-200, -100, -100, 100)]}
        with self.assertRaisesRegex(AssertionError, "return_home.*Exclusion 1"):
            compile_plan(
                plan([{"kind": "return_home", "height_m": 40, "speed_mps": 5}]),
                current,
                dict.fromkeys(NAMES, fence_items(value)),
                load(),
            )
        from mission_planner.gcs.test_plan_execution import fleet

        aircraft = fleet(current)
        aircraft.vehicles["Drone1"]["fence"] = fence_items(value)
        with self.assertRaisesRegex(AssertionError, "Exclusion 1"):
            aircraft.check_return(current[0])


class FenceInstallation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fleet = Fleet(Path(self.temporary.name) / "sitl", Mock(), load())
        self.fleet.fences_verified = True
        self.fleet.fences_path = Path(self.temporary.name) / "fences.json"
        self.states = [
            {
                "id": name,
                "home": home,
                **home,
                "armed": False,
                "landed_state": 1,
                "link_lost": False,
                "ekf_healthy": True,
                "gps_fix": 3,
            }
            for name, home in homes().items()
        ]
        self.fleet.snapshot = Mock(return_value=self.states)
        self.fleet.vehicles = {name: {"fence": fence_items(default_fences())} for name in NAMES}
        self.sent = {}

        async def upload(name, items, mission_type):
            self.assertEqual(mission_type, MAV.MAV_MISSION_TYPE_FENCE)
            self.assertFalse(self.fleet.fences_verified)
            self.sent[name] = copy.deepcopy(items)

        self.fleet._upload = AsyncMock(side_effect=upload)
        self.fleet._download = AsyncMock(side_effect=lambda name, kind: self.sent[name])

    async def test_save_verifies_every_aircraft_before_persisting(self):
        result = await self.fleet.set_fences(fences())
        self.assertEqual(result["version"], 1)
        self.assertTrue(self.fleet.fences_verified)
        self.assertEqual(list(self.sent), list(NAMES))
        self.assertEqual(json.loads(self.fleet.fences_path.read_text()), result)
        self.assertEqual(result["names"], fences()["names"])
        self.assertTrue(
            all(
                read_fences(v["fence"])["exclusions"] == result["exclusions"]
                for v in self.fleet.vehicles.values()
            )
        )

    async def test_stale_revision_and_airborne_save_do_not_upload(self):
        for update in ({"armed": True}, {"landed_state": 2}, {"link_lost": True}, {"gps_fix": 2}):
            original = dict(self.states[1])
            self.states[1].update(update)
            with self.assertRaisesRegex(AssertionError, "healthy, landed"):
                await self.fleet.set_fences(fences())
            self.states[1] = original
        with self.assertRaisesRegex(AssertionError, "another session"):
            await self.fleet.set_fences({**fences(), "version": 4})
        self.fleet._upload.assert_not_awaited()
        self.assertTrue(self.fleet.fences_verified)

    async def test_readback_failure_locks_launch_and_does_not_retry_or_persist(self):
        async def download(name, kind):
            received = copy.deepcopy(self.sent[name])
            if name == "Drone2":
                received[8]["x"] += 10
            return received

        self.fleet._download.side_effect = download
        with self.assertRaisesRegex(RuntimeError, "Drone2: fence vertex 9 x differs"):
            await self.fleet.set_fences(fences())
        self.assertEqual(list(self.sent), ["Drone1", "Drone2"])
        self.assertFalse(self.fleet.fences_verified)
        self.assertFalse(self.fleet.fences_path.exists())
        with self.assertRaisesRegex(AssertionError, "launch is locked"):
            await self.fleet.preview_plan({})
        with self.assertRaisesRegex(AssertionError, "launch is locked"):
            await self.fleet.execute("take_off", {"drone_id": "Drone1"})

    async def test_preview_cannot_finish_against_a_replaced_fence_version(self):
        from mission_planner.gcs.test_plan_execution import fleet

        aircraft = fleet(states())
        entered, release = threading.Event(), threading.Event()

        def compile(*args):
            entered.set()
            assert release.wait(2), "Test did not release compilation"
            return {"plan": {"version": 1}}

        with patch("mission_planner.gcs.planner.compile_plan", side_effect=compile) as compiler:
            task = asyncio.create_task(aircraft.preview_plan({"version": 1}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            aircraft.fences = {**aircraft.fences, "version": 1}
            release.set()
            with self.assertRaisesRegex(AssertionError, "changed during preview"):
                await task
            self.assertEqual(compiler.call_count, 1)

    async def test_invalid_readback_leaves_the_last_readable_snapshot_and_locks_launch(self):
        previous = copy.deepcopy(self.fleet.vehicles["Drone1"]["fence"])
        self.fleet._download.return_value = []
        self.fleet._download.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "Fence readback needs"):
            await self.fleet.set_fences(fences())
        self.assertEqual(self.fleet.vehicles["Drone1"]["fence"], previous)
        self.assertFalse(self.fleet.fences_verified)
        self.assertIsNotNone(self.fleet.fence_error)


class FenceApprovals(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from mission_planner.gcs.test_plan_execution import mission

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.owner = mission(states())
        self.owner.lifecycle_lock = asyncio.Lock()
        self.owner.clearing = False
        self.owner.fleet.fences_path = Path(self.temporary.name) / "fences.json"
        self.owner.fleet.clear_missions = AsyncMock()
        self.sent = {}

        async def upload(name, items, kind):
            self.sent[name] = copy.deepcopy(items)

        self.owner.fleet._upload = AsyncMock(side_effect=upload)
        self.owner.fleet._download = AsyncMock(side_effect=lambda name, kind: self.sent[name])

    async def pending(self):
        from mission_planner.gcs.test_plan_execution import pending_approval

        await self.owner.execute_plan(
            "preview_plan",
            {"plan": plan([{"kind": "takeoff", "height_m": 30}, hover()])},
            "operator",
            "preview",
        )
        task = asyncio.create_task(
            self.owner.invoke("request_launch", {"version": 1}, "operator", "launch")
        )
        await pending_approval(self.owner)
        return task

    async def test_save_invalidates_an_existing_approval_without_arm_or_launch(self):
        task = await self.pending()
        self.assertTrue(self.owner.fences_editable())
        await self.owner.set_fences(fences())
        result = await task
        self.assertFalse(result["ok"])
        self.assertIn("Flight fences changed", result["error"])
        self.assertIsNone(self.owner.plan_preview)
        self.assertEqual(self.owner.approvals, {})
        self.owner.fleet._command.assert_not_awaited()
        self.owner.fleet.clear_missions.assert_awaited_once()
        self.assertTrue(self.owner.fleet.fences_verified)

    async def test_invalid_drawing_leaves_the_pending_plan_and_approval_unchanged(self):
        task = await self.pending()
        preview = self.owner.plan_preview
        with self.assertRaisesRegex(AssertionError, "self-intersects"):
            await self.owner.set_fences(
                {
                    **fences(),
                    "exclusions": [[point(0, 0), point(20, 20), point(0, 20), point(20, 0)]],
                }
            )
        self.assertIs(self.owner.plan_preview, preview)
        self.assertEqual(len(self.owner.approvals), 1)
        self.owner.fleet.clear_missions.assert_not_awaited()
        self.owner.fleet._upload.assert_not_awaited()
        next(iter(self.owner.approvals.values()))["future"].set_result(False)
        await task
