"""Operator-authored planner acceptance through the installed coupled mission service."""

import argparse
import asyncio
import itertools
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from websockets.asyncio.client import connect

from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.geodesy import (
    MIN_SEPARATION_M,
    ORIGIN,
    SEPARATION_TOLERANCE_M,
    point_in_polygon,
    segment_distance,
    segment_in_polygon,
)
from mission_planner.gcs.validation import minimum_clearance
from world.cosys.terrain import terrain_height

CABIN = (-498.0, -189.0)
DOME = (630.9, 299.5)
LAKE = (358.0, -124.1)


def itinerary():
    def fly(point):
        return {
            "kind": "fly",
            "north_m": point[0],
            "east_m": point[1],
            "height_m": 40,
            "speed_mps": 15,
        }

    def hover():
        return {"kind": "hover", "seconds": None}

    def branch(name, steps):
        return {"drone_id": name, "steps": steps}

    branches = [
        [
            branch(
                "Drone1",
                [{"kind": "takeoff", "height_m": 40}, fly((-780, -2.5)), fly(CABIN), hover()],
            )
        ],
        [branch("Drone1", [fly(LAKE), hover()])],
        [branch("Drone1", [fly(DOME), hover()])],
        [
            branch(
                "Drone1",
                [
                    {
                        "kind": "circle",
                        "north_m": DOME[0],
                        "east_m": DOME[1],
                        "radius_m": 40,
                        "height_m": 40,
                        "speed_mps": 15,
                        "turns": 1,
                        "clockwise": True,
                    },
                    hover(),
                ],
            )
        ],
        [
            branch("Drone1", [hover()]),
            branch(
                "Drone2",
                [{"kind": "takeoff", "height_m": 40}, fly((-780, 2.5)), fly((-600, 200)), hover()],
            ),
        ],
        [branch("Drone1", [hover()]), branch("Drone2", [fly((-500, 200)), hover()])],
        [
            branch(
                name, [fly((-780, east)), {"kind": "return_home", "height_m": 40, "speed_mps": 15}]
            )
            for name, east in (("Drone1", -2.5), ("Drone2", 2.5))
        ],
    ]
    return [{"version": index, "drones": drones} for index, drones in enumerate(branches, 1)]


def ground_column(vehicle):
    return (
        vehicle["mode"] == "LAND"
        or vehicle["landed_state"] in (1, 3, 4)
        or any(
            item["seq"] == vehicle["mission_current"] and item["command"] in (21, 22)
            for item in vehicle["mission"]
        )
    )


def neu(vehicle):
    return (
        vehicle["north_m"],
        vehicle["east_m"],
        vehicle["home"]["altitude_msl_m"]
        - ORIGIN["altitude_msl_m"]
        + vehicle["altitude_above_home_m"],
    )


def measure(samples, receipts, changes):
    ground, cruise, separation = [], [], []
    deviations, swaps, previous = {}, {}, None
    fence_violations, count, circle_angle, circle_last = [], 0, 0.0, None
    for sample in samples:
        count += 1
        fleet = {v["id"]: v for v in sample["vehicles"]}
        old = {v["id"]: v for v in previous["vehicles"]} if previous is not None else {}
        version = sample["plan_version"]
        for name, vehicle in fleet.items():
            xyz = neu(vehicle)
            ground.append(xyz[2] - terrain_height(*xyz[:2]))
            if not point_in_polygon(vehicle, vehicle["fence"]) or (
                name in old and not segment_in_polygon(old[name], vehicle, vehicle["fence"])
            ):
                fence_violations.append({"at_ms": sample["at_ms"], "drone_id": name})
            if name in old:
                home_z = vehicle["home"]["altitude_msl_m"] - ORIGIN["altitude_msl_m"]
                start = {**old[name], "altitude_above_home_m": neu(old[name])[2] - home_z}
                clearance = minimum_clearance(start, vehicle, 0, 1, home_z, 0)
                ground.append(clearance)
                if vehicle["armed"] and not ground_column(vehicle) and not ground_column(old[name]):
                    cruise.append(clearance)
            if version is not None and name in receipts[version]["missions"]:
                route = receipts[version]["missions"][name]["route"]
                point = tuple(
                    vehicle[key] for key in ("north_m", "east_m", "altitude_above_home_m")
                )
                coordinates = [
                    tuple(p[key] for key in ("north_m", "east_m", "altitude_above_home_m"))
                    for p in route
                ]
                segments = (
                    list(zip(coordinates, coordinates[1:]))
                    if len(coordinates) > 1
                    else [(coordinates[0], coordinates[0])]
                )
                deviations.setdefault(name, []).append(
                    min(segment_distance(point, point, a, b) for a, b in segments)
                )
            pending = sample["applying_version"]
            if pending is not None and name in changes[pending]["drones"]:
                observed = swaps.setdefault(pending, {}).setdefault(
                    name, {"brake_sample_ms": None, "hover_sample_ms": None, "auto_sample_ms": None}
                )
                if vehicle["mode"] == "BRAKE":
                    if observed["brake_sample_ms"] is None:
                        observed["brake_sample_ms"] = sample["at_ms"]
                    if vehicle["ground_speed_mps"] < 0.2 and observed["hover_sample_ms"] is None:
                        observed["hover_sample_ms"] = sample["at_ms"]
                if (
                    vehicle["mode"] == "AUTO"
                    and version == pending
                    and observed["auto_sample_ms"] is None
                ):
                    observed["auto_sample_ms"] = sample["at_ms"]
            if sample["phase"] == "circle" and name == "Drone1":
                offset = xyz[0] - DOME[0], xyz[1] - DOME[1]
                if 30 <= math.hypot(*offset) <= 50:
                    angle = math.atan2(offset[1], offset[0])
                    if circle_last is not None:
                        circle_angle += (angle - circle_last + math.pi) % math.tau - math.pi
                    circle_last = angle
        for a, b in itertools.combinations(fleet, 2):
            relative = [neu(fleet[a])[k] - neu(fleet[b])[k] for k in range(3)]
            separation.append(math.sqrt(sum(v * v for v in relative)))
            if old:
                start = [neu(old[a])[k] - neu(old[b])[k] for k in range(3)]
                delta = [relative[k] - start[k] for k in range(3)]
                length2 = sum(v * v for v in delta)
                t = (
                    max(0, min(1, -sum(start[k] * delta[k] for k in range(3)) / length2))
                    if length2
                    else 0
                )
                separation.append(math.sqrt(sum((start[k] + t * delta[k]) ** 2 for k in range(3))))
        previous = sample
    for version, drones in swaps.items():
        for name, observed in drones.items():
            observed["hover_s"] = (
                (observed["auto_sample_ms"] - observed["hover_sample_ms"]) / 1000
                if observed["auto_sample_ms"] is not None
                and observed["hover_sample_ms"] is not None
                else None
            )
            upload = changes[version]["uploads_ms"]
            observed["change_to_upload_s"] = (
                (upload[name] - changes[version]["submitted_at_ms"]) / 1000
                if name in upload
                else None
            )
    return {
        "telemetry_samples": count,
        "minimum_clearance_including_ground_columns_m": min(ground) if ground else None,
        "minimum_cruise_clearance_m": min(cruise) if cruise else None,
        "minimum_separation_m": min(separation) if separation else None,
        "fence_violations": fence_violations,
        "maximum_route_deviation_m": {name: max(values) for name, values in deviations.items()},
        "circle_clockwise_turns": circle_angle / math.tau,
        "swaps": swaps,
        "limits": "Receipt-time MAVLink telemetry; straight interpolation between samples. Ground columns include takeoff/landing and are excluded from cruise clearance. Hover times run from first stopped BRAKE sample to first AUTO sample.",
    }


class Acceptance:
    def __init__(self, socket, output):
        self.socket, self.output = socket, output
        self.state, self.phase, self.applying = None, "connect", None
        self.events, self.checkpoints, self.approved = [], [], set()
        self.receipts, self.changes, self.cards = {}, {}, {}
        self.log = (output / "websocket.jsonl").open("x")
        self.telemetry = (output / "telemetry.jsonl").open("x")
        self.at_ms = None

    async def send(self, message):
        self.log.write(json.dumps({"sent_at_ms": time.time() * 1000, "message": message}) + "\n")
        self.log.flush()
        await self.socket.send(json.dumps(message))

    def drone(self, name):
        return next(v for v in self.state["vehicles"] if v["id"] == name)

    async def read(self, timeout):
        message = json.loads(await asyncio.wait_for(self.socket.recv(), timeout))
        self.at_ms = time.time() * 1000
        kind = message["type"]
        if kind == "state":
            self.state = message
            active = message["planner"]["active"]
            version = active["plan"]["version"] if active is not None else None
            if version is not None:
                self.receipts[version] = active
            self.telemetry.write(
                json.dumps(
                    {
                        "at_ms": self.at_ms,
                        "phase": self.phase,
                        "applying_version": self.applying,
                        "plan_version": version,
                        "vehicles": message["vehicles"],
                    }
                )
                + "\n"
            )
            self.telemetry.flush()
            assert not message["agent_busy"], (
                "A Wonder request is running during operator acceptance"
            )
            assert not message["mission"]["alerts"], message["mission"]["alerts"]
            for vehicle in message["vehicles"]:
                assert not vehicle["link_lost"] and vehicle["ekf_healthy"], vehicle["id"]
                assert point_in_polygon(vehicle, vehicle["fence"]), (
                    f"{vehicle['id']}: actual fence violation"
                )
                if vehicle["armed"] and not ground_column(vehicle):
                    assert (
                        neu(vehicle)[2] - terrain_height(vehicle["north_m"], vehicle["east_m"])
                        >= 10
                    ), f"{vehicle['id']}: actual terrain clearance below 10 m"
            for a, b in itertools.combinations(message["vehicles"], 2):
                assert math.dist(neu(a), neu(b)) >= MIN_SEPARATION_M - SEPARATION_TOLERANCE_M, (
                    f"Actual separation below {MIN_SEPARATION_M} m: {a['id']}, {b['id']}"
                )
            for card in message["approvals"]:
                if card["id"] in self.approved:
                    continue
                assert card["name"] == "request_launch" and self.applying in (1, 5), (
                    "Unexpected approval"
                )
                if card["id"] not in self.cards:
                    self.cards[card["id"]] = self.at_ms
                if self.at_ms - self.cards[card["id"]] < 2000:
                    continue
                if self.applying == 5:
                    assert self.drone("Drone1")["mode"] == "BRAKE", (
                        "Airborne drone did not hover for added-drone approval"
                    )
                    if self.drone("Drone1")["ground_speed_mps"] >= 0.2:
                        continue
                self.approved.add(card["id"])
                await self.send({"type": "approval", "id": card["id"], "approved": True})
        elif kind in ("event", "history"):
            self.log.write(json.dumps({"received_at_ms": self.at_ms, "message": message}) + "\n")
            self.log.flush()
            if kind == "history":
                return
            event = message["event"]
            self.events.append(event)
            assert event["kind"] != "error", event["text"]
            if event["kind"] == "plan_version" and event["result"] is not None:
                result = event["result"]
                if "stage" in result and result["stage"] == "upload":
                    self.changes[result["version"]]["uploads_ms"][result["drone_id"]] = result[
                        "at_ms"
                    ]
        elif kind in ("error", "idle"):
            self.log.write(json.dumps({"received_at_ms": self.at_ms, "message": message}) + "\n")
            self.log.flush()
            raise AssertionError(message)

    async def wait(self, predicate, timeout, label):
        deadline = time.monotonic() + timeout
        while self.state is None or not predicate():
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"Timed out waiting for {label}"
            await self.read(remaining)

    async def tool(self, name, arguments):
        first = len(self.events)
        await self.send({"type": "tool", "name": name, "arguments": arguments})

        def result():
            return next(
                (
                    e["result"]
                    for e in self.events[first:]
                    if e["kind"] == "tool_result"
                    and e["name"] == name
                    and e["arguments"] == arguments
                ),
                None,
            )

        await self.wait(lambda: result() is not None, 120, name)
        value = result()
        assert value["ok"], value["error"]
        return value["state"]

    async def plan(self, plan):
        version = plan["version"]
        self.applying = version
        self.changes[version] = {
            "submitted_at_ms": time.time() * 1000,
            "drones": [d["drone_id"] for d in plan["drones"]],
            "uploads_ms": {},
        }
        await self.tool("preview_plan", {"plan": plan})
        command = "request_launch" if version in (1, 5) else "apply_plan_change"
        self.changes[version]["result"] = await self.tool(command, {"version": version})
        await self.wait(
            lambda: (
                self.state["planner"]["active"] is not None
                and self.state["planner"]["active"]["plan"]["version"] == version
                and all(
                    self.drone(name)["mode"] == "AUTO" for name in self.changes[version]["drones"]
                )
            ),
            20,
            f"plan {version} AUTO telemetry",
        )
        self.applying = None

    def reached(self, name, target):
        vehicle = self.drone(name)
        return (
            vehicle["armed"]
            and vehicle["mode"] == "AUTO"
            and vehicle["ground_speed_mps"] < 0.5
            and math.dist((vehicle["north_m"], vehicle["east_m"]), target) < 3
        )

    def checkpoint(self, label):
        self.checkpoints.append(
            {"phase": label, "at_ms": self.at_ms, "vehicles": self.state["vehicles"]}
        )
        (self.output / "checkpoints.json").write_text(json.dumps(self.checkpoints, indent=2) + "\n")
        print(label, flush=True)

    async def fly(self, case_id):
        await self.send({"type": "connect", "case_id": case_id})
        await self.wait(lambda: len(self.state["vehicles"]) == 4, 30, "four installed autopilots")
        assert all(not v["armed"] and v["landed_state"] == 1 for v in self.state["vehicles"]), (
            "Acceptance requires a landed fleet"
        )
        assert not self.state["approvals"] and self.state["planner"]["active"] is None, (
            "Acceptance requires a fresh mission"
        )
        plans = itinerary()
        self.phase = "cabin"
        await self.plan(plans[0])
        await self.wait(lambda: self.reached("Drone1", CABIN), 180, "Ranger Cabin hover")
        self.checkpoint("Ranger Cabin reached; hovering until instructed")
        self.phase = "change"
        await self.plan(plans[1])
        await self.wait(
            lambda: (
                self.drone("Drone1")["ground_speed_mps"] > 2
                and math.dist(
                    (self.drone("Drone1")["north_m"], self.drone("Drone1")["east_m"]), CABIN
                )
                > 75
            ),
            60,
            "moving along Lake leg",
        )
        self.checkpoint("Lake leg interrupted while moving")
        await self.plan(plans[2])
        await self.wait(lambda: self.reached("Drone1", DOME), 240, "Granite Dome hover")
        self.checkpoint("Mid-flight replacement reached Granite Dome")
        self.phase = "circle"
        await self.plan(plans[3])
        await self.wait(
            lambda: (
                self.state["planner"]["progress"]["Drone1"]["current_step"] == 1
                and self.drone("Drone1")["ground_speed_mps"] < 0.5
            ),
            180,
            "one circle completed",
        )
        self.checkpoint("Granite Dome circle completed")
        self.phase = "two_drones"
        await self.plan(plans[4])
        await self.wait(lambda: self.reached("Drone2", (-600, 200)), 150, "two-drone task")
        self.checkpoint("Drone2 reached task; Drone1 remained airborne")
        self.phase = "override"
        await self.plan(plans[5])
        await self.wait(
            lambda: (
                self.drone("Drone2")["ground_speed_mps"] > 2
                and self.drone("Drone2")["north_m"] > -580
            ),
            45,
            "Drone2 moving before override",
        )
        requested = time.time() * 1000
        approvals = len(self.approved)
        await self.tool("hold", {"drone_id": "Drone2"})
        await self.wait(
            lambda: (
                self.drone("Drone2")["mode"] == "BRAKE"
                and self.drone("Drone2")["ground_speed_mps"] < 0.2
            ),
            20,
            "immediate hold",
        )
        assert len(self.approved) == approvals, "Override requested approval"
        self.checkpoint("Moving Drone2 stopped by immediate hold")
        self.checkpoints[-1]["request_to_stopped_brake_s"] = (self.at_ms - requested) / 1000
        self.phase = "return"
        await self.plan(plans[6])
        await self.wait(
            lambda: all(not v["armed"] and v["landed_state"] == 1 for v in self.state["vehicles"]),
            300,
            "full return and landing",
        )
        for name in ("Drone1", "Drone2"):
            vehicle = self.drone(name)
            assert (
                math.dist(
                    (vehicle["north_m"], vehicle["east_m"]),
                    (vehicle["home"]["north_m"], vehicle["home"]["east_m"]),
                )
                < 2
            ), f"{name}: landed away from home"
        assert len(self.approved) == 2
        self.checkpoint("Both aircraft returned home and landed")


async def run(case_id, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "case_id": case_id, "model_calls": 0, "plans": itinerary()}
    started = time.monotonic()
    try:
        load_env()
        runtime = dotenv_values(ROOT / ".local/runtime.env")
        assert (runtime["SNR_HOST"], runtime["SNR_MISSION_PORT"], runtime["SNR_SITL_MODEL"]) == (
            "127.0.0.1",
            "8765",
            "airsim-copter",
        )
        native = json.loads((Path(runtime["SNR_DRONE_DATA_DIR"]) / "runtime.json").read_text())
        assert native["schemaVersion"] == 1 and native["worldToMeters"] == 100
        assert native["worldOriginOffsetCm"] == {"x": 0, "y": 0, "z": 0}
        captured = datetime.fromisoformat(native["capturedAt"].replace("Z", "+00:00"))
        assert abs((datetime.now(timezone.utc) - captured).total_seconds()) < 10, (
            "Coupled world telemetry is stale"
        )
        report.update(
            native_captured_at=native["capturedAt"], unreal_image=runtime["SNR_UNREAL_IMAGE"]
        )
        async with connect("ws://127.0.0.1:8765", max_size=2**24) as socket:
            acceptance = Acceptance(socket, output)
            task = asyncio.create_task(acceptance.fly(case_id))
            try:
                await asyncio.wait([task])
                error = task.exception()
                report.update(
                    flight_failure=f"{type(error).__name__}: {error}"
                    if error is not None
                    else None,
                    checkpoints=acceptance.checkpoints,
                    changes=acceptance.changes,
                    approval_count=len(acceptance.approved),
                )
            finally:
                acceptance.log.close()
                acceptance.telemetry.close()
            with (output / "telemetry.jsonl").open() as samples:
                report["measurements"] = measure(
                    (json.loads(line) for line in samples), acceptance.receipts, acceptance.changes
                )
            if error is not None:
                raise error
            assert 0.9 <= report["measurements"]["circle_clockwise_turns"] <= 1.1, (
                "Telemetry did not complete one circle"
            )
            assert not report["measurements"]["fence_violations"]
            assert report["measurements"]["minimum_cruise_clearance_m"] >= 10
            assert (
                report["measurements"]["minimum_separation_m"]
                >= MIN_SEPARATION_M - SEPARATION_TOLERANCE_M
            )
    finally:
        error = sys.exc_info()[1]
        report.update(
            passed=error is None,
            failure=f"{type(error).__name__}: {error}" if error is not None else None,
            duration_s=time.monotonic() - started,
        )
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.case, args.output))
