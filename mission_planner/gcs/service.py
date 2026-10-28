"""Persistent mission owner: MAVLink, operator/agent tools, approvals and WebSocket."""

import argparse
import asyncio
import json
import os
import signal
import time
import uuid
from pathlib import Path

import httpx
from websockets.asyncio.server import serve

from mission_planner.agents.models import Wonder
from mission_planner.agents.tools import (
    AGENT_INSTRUCTIONS,
    CATEGORIES,
    TOOLS,
    validate_arguments,
)
from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.fleet import Fleet
from mission_planner.gcs.geodesy import BOUND_M, NAMES
from mission_planner.gcs.sensor_feed import sensor_message
from mission_planner.search.execution import flight_label, start_search
from mission_planner.search.state import Search
from mission_planner.search.tools import NAMES as SEARCH_TOOLS
from world.cosys.sensor import MissionSensor

MESSAGE_FIELDS = {
    "connect": {"type", "case_id"},
    "chat": {"type", "text"},
    "tool": {"type", "name", "arguments"},
    "approval": {"type", "id", "approved"},
    "autonomy": {"type", "mode"},
    "export_plan": {"type", "drone_id"},
    "search_point": {"type", "north_m", "east_m"},
}


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def operator_error(error):
    if isinstance(error, httpx.HTTPStatusError):
        return f"Wonder is unavailable (HTTP {error.response.status_code}). No command was sent."
    if isinstance(error, httpx.TimeoutException):
        return "Wonder did not respond before the request timed out. No command was sent."
    return str(error) if str(error) else "The operation failed without an error description."


def compact_vehicle(vehicle):
    return {
        "drone_id": vehicle["id"],
        **{
            key: vehicle[key]
            for key in (
                "mode",
                "armed",
                "landed_state",
                "north_m",
                "east_m",
                "altitude_above_home_m",
                "mission_current",
                "mission_total",
            )
        },
    }


def fence_summary(vertices):
    assert vertices, "The autopilot fence has not been read back."
    return {
        "vertex_count": len(vertices),
        "bounds": {
            axis: [
                round(min(p[axis] for p in vertices), 3),
                round(max(p[axis] for p in vertices), 3),
            ]
            for axis in ("north_m", "east_m")
        },
    }


def read_case(case_id):
    assert str(uuid.UUID(case_id)) == case_id.lower(), "Case id must be a UUID"
    directory = Path(os.environ["SNR_SCENARIO_DATA_DIR"])
    value = json.loads((directory / "briefings" / f"{case_id}.json").read_text())
    assert set(value) == {"schemaVersion", "id", "name", "report", "world"}
    assert value["schemaVersion"] == 1 and value["id"].lower() == case_id.lower()
    assert isinstance(value["name"], str) and 1 <= len(value["name"]) <= 80
    assert set(value["report"]) == {
        "description",
        "reportedCircumstances",
        "lastKnownLocation",
        "portrait",
    }
    assert all(isinstance(v, str) for v in value["report"].values())
    assert all(
        len(value["report"][k]) <= 1500
        for k in ("description", "reportedCircumstances", "lastKnownLocation")
    )
    assert len(value["report"]["portrait"]) <= 45000
    world = value["world"]
    assert set(world) == {"environmentId", "assetVersion", "coordinateSystem", "searchBounds"}
    assert world["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
    assert world["searchBounds"], "Rescue case has no search bounds"
    for bounds in world["searchBounds"]:
        assert set(bounds) == {"id", "minX", "maxX", "minY", "maxY"}
        assert (bounds["minX"], bounds["maxX"], bounds["minY"], bounds["maxY"]) == (
            -BOUND_M * 100,
            BOUND_M * 100,
            -BOUND_M * 100,
            BOUND_M * 100,
        ), "Rescue case search bounds differ from the configured mission fence"
    return value


class Mission:
    def __init__(self, case):
        assert os.environ["SNR_SITL_MODEL"] == "airsim-copter", (
            "Mission service requires the Unreal-coupled fleet"
        )
        self.case = case
        self.id = str(uuid.uuid4())
        self.directory = ROOT / ".local/data/missions" / self.id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log = (self.directory / "events.jsonl").open("x")
        self.events = []
        self.event_sequence = 0
        self.started = time.monotonic()
        self.autonomy = "Supervised"
        self.approvals = {}
        self.tasks = set()
        self.agent_busy = False
        self.context_index = 0
        self.map_focus = None
        self.fleet_ready = False
        self.fleet_closed = False
        self.fleet_error = None
        self.fleet = Fleet(self.directory / "sitl", self.fleet_event)
        self.model = Wonder()
        self.search = Search(self)
        self.sensor = MissionSensor(self.directory / "captures", self.fleet, self.capture_received)
        self.sensor_error = None
        self.sensor_frames = {}
        self.feed_focus = None

    def event(
        self, kind, text, *, name=None, arguments=None, result=None, call_id=None, source=None
    ):
        self.event_sequence += 1
        item = {
            "sequence": self.event_sequence,
            "id": str(uuid.uuid4()),
            "timestamp_ms": time.time() * 1000,
            "kind": kind,
            "text": text,
            "name": name,
            "arguments": arguments,
            "result": result,
            "call_id": call_id,
            "source": source,
        }
        self.log.write(encode(item) + "\n")
        self.log.flush()
        self.events.append(item)
        del self.events[:-1000]
        return item

    def fleet_event(self, item):
        kind = item["kind"]
        if kind == "statustext":
            text = item["drone_id"] + ": " + item["text"]
        elif kind == "mode":
            text = item["drone_id"] + " mode " + item["mode"]
        elif kind == "autopilot_started":
            text = item["drone_id"] + " autopilot started"
        elif kind == "firmware_verified":
            text = item["drone_id"] + " firmware verified"
        elif kind == "safety_parameters_verified":
            text = item["drone_id"] + " safety parameters verified"
        elif kind == "sitl_ports_verified":
            text = "SITL listener ports verified"
        elif kind == "home_comparison":
            text = f"{item['drone_id']} home position error {item['home_error_m']:.3f} m"
        elif kind == "capture_rejected":
            self.event(
                "error",
                item["droneId"] + " capture rejected: " + "; ".join(item["validation"]["reasons"]),
                result=item,
            )
            return
        else:
            assert kind == "parameter", f"Unknown fleet event: {kind}"
            text = f"{item['drone_id']} {item['name']} verified: {item['value']}"
        self.event("statustext" if kind == "statustext" else "mode", text, result=item)

    def launch(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def completed(finished):
            self.tasks.remove(finished)
            if not finished.cancelled() and finished.exception() is not None:
                error = finished.exception()
                self.event("error", operator_error(error))

        task.add_done_callback(completed)

    async def start_fleet(self):
        task = asyncio.create_task(self.fleet.start())
        try:
            await asyncio.wait([task])
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait([task])
        error = task.exception()
        if error is not None:
            self.fleet_error = operator_error(error)
            self.event("error", "Fleet startup failed: " + self.fleet_error)
            await self.fleet.close()
            self.fleet_closed = True
            return
        self.fleet_ready = True
        self.event("mode", "Four autopilots connected; fences and missions read back.")
        self.launch(self.run_sensor())

    async def run_sensor(self):
        try:
            await self.sensor.start()
            while True:
                self.sensor.check()
                await asyncio.sleep(0.1)
        except Exception as error:
            self.sensor_error = operator_error(error)
            raise RuntimeError("Sensor acquisition stopped: " + self.sensor_error) from error

    async def capture_received(self, record, preview):
        message = sensor_message(record, preview)
        self.search.consume_capture(record, "unprocessed")
        self.sensor_frames[record["vehicleId"]] = message

    def vehicles(self):
        return self.fleet.snapshot() if self.fleet_ready else []

    def summary(self):
        vehicles = self.vehicles()
        alerts = []
        if self.sensor_error is not None:
            alerts.append("Sensor acquisition stopped: " + self.sensor_error)
        if self.fleet_error is not None:
            alerts.append("Fleet startup failed: " + self.fleet_error)
        elif not self.fleet_ready:
            alerts.append("Starting autopilots; awaiting verified telemetry and fence read-back.")
        for vehicle in vehicles:
            if vehicle["link_lost"]:
                alerts.append(vehicle["id"] + " telemetry link lost")
            elif not vehicle["ekf_healthy"]:
                alerts.append(vehicle["id"] + " EKF is not healthy")
        return {
            "id": self.id,
            "case_id": self.case["id"],
            "case_name": self.case["name"],
            "briefing": self.case["report"],
            "elapsed_s": time.monotonic() - self.started,
            "autonomy": self.autonomy,
            "alerts": alerts,
        }

    def state(self):
        return {
            "type": "state",
            "mission": self.summary(),
            "vehicles": self.vehicles(),
            "agent_busy": self.agent_busy,
            "approvals": [item["card"] for item in self.approvals.values()],
            "map_focus": self.map_focus,
        }

    async def execute(self, name, arguments, source, call_id):
        validate_arguments(name, arguments)
        if name in SEARCH_TOOLS and name != "start_search":
            return await self.search.execute(name, arguments)
        if name == "start_search":
            self.search.require_plan(arguments["plan_id"])
        if name == "get_mission":
            return {
                **self.summary(),
                "briefing": {
                    key: self.case["report"][key]
                    for key in (
                        "description",
                        "reportedCircumstances",
                        "lastKnownLocation",
                    )
                },
                "fences": {v["id"]: fence_summary(v["fence"]) for v in self.vehicles()},
            }
        if name == "show_on_map":
            if arguments["target"] == "drone":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
            self.map_focus = {**arguments, "id": str(uuid.uuid4())}
            return {"map_focus": self.map_focus}
        if name == "open_feed":
            self.feed_focus = {"type": "feed_focus", "id": str(uuid.uuid4()), **arguments}
            return {"drone_id": arguments["drone_id"], "opened": True}
        assert self.fleet_ready, "MAVLink fleet is unavailable: " + str(self.fleet_error)
        if name == "get_fleet":
            return {
                "vehicles": [
                    {
                        **compact_vehicle(v),
                        "link_lost": v["link_lost"],
                        "heartbeat_age_s": v["heartbeat_age_s"],
                    }
                    for v in self.vehicles()
                ]
            }
        if name == "get_drone":
            vehicle = next(v for v in self.vehicles() if v["id"] == arguments["drone_id"])
            return {
                **compact_vehicle(vehicle),
                **{
                    key: vehicle[key]
                    for key in (
                        "link_lost",
                        "heartbeat_age_s",
                        "battery_percent",
                        "battery_voltage_v",
                        "gps_fix",
                        "satellites",
                        "ekf_healthy",
                    )
                },
                "waypoints": [
                    {
                        key: p[key]
                        for key in (
                            "seq",
                            "north_m",
                            "east_m",
                            "altitude_above_home_m",
                        )
                    }
                    for p in vehicle["mission"]
                    if p["seq"] > 0
                ],
                "status_text": vehicle["status_text"][-5:],
            }
        assert CATEGORIES[name] == "flight"
        if self.autonomy == "Supervised":
            approval_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self.approvals[approval_id] = {
                "card": {
                    "id": approval_id,
                    "name": name,
                    "arguments": arguments,
                    "source": source,
                    "call_id": call_id,
                },
                "future": future,
                "call_id": call_id,
            }
            self.event(
                "approval",
                f"{flight_label(name, arguments)} · {name.replace('_', ' ')} awaiting operator approval",
                result={"approval_id": approval_id},
                name=name,
                arguments=arguments,
                call_id=call_id,
                source=source,
            )
            approved = await future
            del self.approvals[approval_id]
            assert approved, "Operator declined"
        self.event(
            "tool_running",
            f"{flight_label(name, arguments)} · {name.replace('_', ' ')} running",
            name=name,
            arguments=arguments,
            call_id=call_id,
            source=source,
        )
        if name == "start_search":
            return await start_search(self.search, arguments["plan_id"])
        state = await self.fleet.execute(name, arguments)
        return {**compact_vehicle(state), "acknowledged_command": name}

    async def invoke(self, name, arguments, source, call_id):
        self.event(
            "tool_call", name, name=name, arguments=arguments, call_id=call_id, source=source
        )
        task = asyncio.create_task(self.execute(name, arguments, source, call_id))
        try:
            await asyncio.wait([task])
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait([task])
        error = task.exception()
        if error is None:
            result = {"ok": True, "state": task.result()}
        else:
            result = {"ok": False, "error": operator_error(error)}
        self.event(
            "tool_result",
            name,
            name=name,
            arguments=arguments,
            result=result,
            call_id=call_id,
            source=source,
        )
        return result

    def instructions(self, initial):
        context = {
            "autonomy": self.autonomy,
            "search": self.search.summary(),
            "telemetry_at_utc_ms": time.time() * 1000,
            "current_vehicles": [compact_vehicle(v) for v in self.vehicles()],
            "historical_operator_events": [
                {
                    "sequence": event["sequence"],
                    "timestamp_ms": event["timestamp_ms"],
                    "kind": event["kind"],
                    "text": event["text"],
                    "name": event["name"],
                    "arguments": event["arguments"],
                    "result": event["result"],
                }
                for event in self.events
                if (initial or event["sequence"] > self.context_index)
                and event["source"] == "operator"
                and event["kind"] in ("tool_result", "mode")
            ][-8:],
        }
        if initial:
            context["briefing"] = {
                key: self.case["report"][key]
                for key in (
                    "description",
                    "reportedCircumstances",
                    "lastKnownLocation",
                )
            }
            context["recent_messages"] = [
                {
                    "role": event["kind"],
                    "text": event["text"][:1200],
                    "truncated": len(event["text"]) > 1200,
                }
                for event in self.events
                if event["kind"] in ("operator", "agent")
            ][-6:-1]
        self.context_index = self.event_sequence
        return AGENT_INSTRUCTIONS + "\nCurrent mission context: " + encode(context)

    async def agent(self, text):
        try:
            outputs = ()
            previous_response_id = None
            for turn in range(12):
                trace = self.directory / f"response-{time.time_ns()}"
                task = asyncio.create_task(
                    asyncio.to_thread(
                        self.model.respond,
                        text if turn == 0 else None,
                        [],
                        "low",
                        TOOLS,
                        previous_response_id,
                        outputs,
                        self.instructions(turn == 0),
                        trace,
                    )
                )
                await asyncio.wait([task])
                error = task.exception()
                if error is not None:
                    diagnostic = {
                        "trace": str(trace.relative_to(ROOT)),
                        "input_tokens": None,
                        "turn": turn,
                        "previous_response_id": previous_response_id,
                    }
                    if trace.with_suffix(".http.json").is_file():
                        diagnostic.update(json.loads(trace.with_suffix(".http.json").read_text()))
                    message = operator_error(error)
                    if turn > 0 and any(json.loads(item["output"])["ok"] for item in outputs):
                        message = message.replace(
                            "No command was sent.",
                            "Earlier actions are recorded below; no additional command was sent.",
                        )
                    self.event("error", message, result=diagnostic, source="agent")
                    return
                response = task.result()
                previous_response_id = response["id"]
                self.event(
                    "model_usage",
                    "Wonder response received",
                    source="agent",
                    result={
                        "response_id": response["id"],
                        "turn": turn,
                        "input_tokens": response["usage"]["input_tokens"],
                        "output_tokens": response["usage"]["output_tokens"],
                        "request_body_bytes": trace.with_suffix(".request.json").stat().st_size,
                        "trace": str(trace.relative_to(ROOT)),
                    },
                )
                calls = []
                for item in response["output"]:
                    if item["type"] == "message":
                        for content in item["content"]:
                            if content["type"] == "output_text":
                                self.event("agent", content["text"], source="agent")
                    elif item["type"] == "function_call":
                        calls.append(item)
                if not calls:
                    return
                results = []
                for call in calls:
                    result = await self.invoke(
                        call["name"], json.loads(call["arguments"]), "agent", call["call_id"]
                    )
                    results.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": encode(result),
                        }
                    )
                outputs = tuple(results)
            raise RuntimeError(
                "Wonder exceeded the 12-turn tool limit; no further commands executed"
            )
        finally:
            self.agent_busy = False

    async def handle(self, message, websocket):
        assert isinstance(message, dict) and "type" in message, "Expected a typed message object"
        kind = message["type"]
        assert kind in MESSAGE_FIELDS, f"Unknown message type: {kind}"
        assert set(message) == MESSAGE_FIELDS[kind], f"Invalid fields for {kind}"
        if kind == "chat":
            assert isinstance(message["text"], str) and 1 <= len(message["text"].strip()) <= 4000
            assert not self.agent_busy, "Wonder is still processing the previous request"
            self.agent_busy = True
            self.event("operator", message["text"], source="operator")
            self.launch(self.agent(message["text"]))
        elif kind == "tool":
            self.launch(
                self.invoke(message["name"], message["arguments"], "operator", str(uuid.uuid4()))
            )
        elif kind == "approval":
            assert type(message["approved"]) is bool
            assert message["id"] in self.approvals, "Unknown or already resolved approval"
            item = self.approvals[message["id"]]
            assert not item["future"].done(), "Approval already resolved"
            self.event(
                "approval",
                f"{flight_label(item['card']['name'], item['card']['arguments'])} · {item['card']['name'].replace('_', ' ')} "
                + ("approved by operator" if message["approved"] else "declined by operator"),
                result={"id": message["id"], "approved": message["approved"]},
                name=item["card"]["name"],
                arguments=item["card"]["arguments"],
                call_id=item["call_id"],
                source="operator",
            )
            item["future"].set_result(message["approved"])
        elif kind == "search_point":
            self.search.mark_point(message["north_m"], message["east_m"])
        elif kind == "autonomy":
            assert message["mode"] in ("Supervised", "Autonomous")
            assert not self.approvals, "Resolve pending approvals before changing autonomy"
            self.autonomy = message["mode"]
            self.event("mode", "Autonomy changed to " + self.autonomy, source="operator")
        elif kind == "export_plan":
            assert message["drone_id"] in NAMES, "Unknown drone: " + message["drone_id"]
            assert self.fleet_ready, "MAVLink fleet is unavailable"
            plan = self.fleet.export_plan(message["drone_id"])
            await websocket.send(
                encode({"type": "plan", "drone_id": message["drone_id"], "plan": plan})
            )
        else:
            raise AssertionError("Mission is already connected")

    async def connection(self, websocket, first=None):
        if first is None:
            first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        assert set(first) == MESSAGE_FIELDS["connect"] and first["type"] == "connect"
        assert first["case_id"].lower() == self.case["id"].lower(), (
            "Service is running a different rescue case"
        )
        await websocket.send(encode({"type": "history", "events": self.events}))
        cursor = self.event_sequence

        async def publish():
            nonlocal cursor
            search_revision = -1
            sensor_cursors = {}
            focus_id = None
            while True:
                for event in [e for e in self.events if e["sequence"] > cursor]:
                    await websocket.send(encode({"type": "event", "event": event}))
                    cursor = event["sequence"]
                await websocket.send(encode(self.state()))
                if search_revision != self.search.revision:
                    await websocket.send(encode({"type": "search", "search": self.search.view()}))
                    search_revision = self.search.revision
                for drone, message in tuple(self.sensor_frames.items()):
                    capture_id = message["frame"]["capture_id"]
                    if drone not in sensor_cursors or sensor_cursors[drone] != capture_id:
                        await websocket.send(encode(message))
                        sensor_cursors[drone] = capture_id
                if self.feed_focus is not None and self.feed_focus["id"] != focus_id:
                    await websocket.send(encode(self.feed_focus))
                    focus_id = self.feed_focus["id"]
                await asyncio.sleep(0.1)

        async def consume():
            async for raw in websocket:
                task = asyncio.create_task(self.handle(json.loads(raw), websocket))
                await asyncio.wait([task])
                error = task.exception()
                if error is not None:
                    text = operator_error(error)
                    self.event("error", text)
                    await websocket.send(encode({"type": "error", "message": text}))

        tasks = [asyncio.create_task(publish()), asyncio.create_task(consume())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            results = await asyncio.gather(*self.tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self.event("error", "Shutdown task: " + operator_error(result))
        try:
            await self.sensor.close()
        finally:
            if not self.fleet_closed:
                await self.fleet.close()
                self.fleet_closed = True
            self.model.http.close()
            self.log.close()


async def run(case_id, port):
    load_env()
    assert 1 <= port <= 65535
    mission = Mission(read_case(case_id))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    print(encode({"pid": os.getpid(), "mission_id": mission.id, "port": port}), flush=True)
    try:
        async with serve(mission.connection, "127.0.0.1", port, max_size=32768):
            mission.launch(mission.start_fleet())
            await stop.wait()
    finally:
        await mission.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--port", type=int, default=int(os.environ["SNR_MISSION_PORT"]))
    args = parser.parse_args()
    asyncio.run(run(args.case, args.port))
