"""Persistent mission owner: MAVLink, operator/agent tools, approvals and WebSocket."""

import argparse
import asyncio
import json
import os
import signal
import time
import uuid
from pathlib import Path

from websockets.asyncio.server import serve

from mission_control.agent.tools import (
    AGENT_INSTRUCTIONS,
    CATEGORIES,
    NAMES,
    TOOLS,
    validate_arguments,
)
from mission_control.environment import ROOT, load_env
from mission_control.gcs.fleet import Fleet
from mission_control.models import Wonder

MESSAGE_FIELDS = {
    "connect": {"type", "case_id"},
    "chat": {"type", "text"},
    "tool": {"type", "name", "arguments"},
    "approval": {"type", "id", "approved"},
    "autonomy": {"type", "mode"},
    "export_plan": {"type", "drone_id"},
}


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


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
            -80467.2,
            80467.2,
            -80467.2,
            80467.2,
        ), "Rescue case search bounds differ from the configured mission fence"
    return value


class Mission:
    def __init__(self, case):
        self.case = case
        self.id = str(uuid.uuid4())
        self.directory = ROOT / "data/missions" / self.id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log = (self.directory / "events.jsonl").open("x")
        self.events = []
        self.event_sequence = 0
        self.started = time.monotonic()
        self.autonomy = "Supervised"
        self.approvals = {}
        self.tasks = set()
        self.agent_busy = False
        self.previous_response_id = None
        self.context_index = 0
        self.map_focus = None
        self.fleet_ready = False
        self.fleet_closed = False
        self.fleet_error = None
        self.fleet = Fleet(self.directory / "sitl", self.fleet_event)
        self.model = Wonder()
        self.flight_lock = asyncio.Lock()

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
                self.event("error", f"{type(error).__name__}: {error}")

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
            self.fleet_error = f"{type(error).__name__}: {error}"
            self.event("error", "Fleet startup failed: " + self.fleet_error)
            await self.fleet.close()
            self.fleet_closed = True
            return
        self.fleet_ready = True
        self.event("mode", "Four autopilots connected; fences and missions read back.")

    def vehicles(self):
        return self.fleet.snapshot() if self.fleet_ready else []

    def summary(self):
        vehicles = self.vehicles()
        alerts = []
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
        if name == "get_mission":
            return {
                **self.summary(),
                "fences": {v["id"]: v["fence"] for v in self.vehicles()},
            }
        if name == "show_on_map":
            if arguments["target"] == "drone":
                assert self.fleet_ready, "MAVLink fleet is unavailable"
            self.map_focus = {**arguments, "id": str(uuid.uuid4())}
            return {"map_focus": self.map_focus}
        assert self.fleet_ready, "MAVLink fleet is unavailable: " + str(self.fleet_error)
        if name == "get_fleet":
            return {"vehicles": self.vehicles()}
        if name == "get_drone":
            return next(v for v in self.vehicles() if v["id"] == arguments["drone_id"])
        assert CATEGORIES[name] == "flight"
        if self.autonomy == "Supervised":
            approval_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self.approvals[approval_id] = {
                "card": {"id": approval_id, "name": name, "arguments": arguments, "source": source},
                "future": future,
                "call_id": call_id,
            }
            self.event(
                "approval",
                "Awaiting operator approval",
                result={"approval_id": approval_id},
                name=name,
                arguments=arguments,
                call_id=call_id,
                source=source,
            )
            approved = await future
            del self.approvals[approval_id]
            assert approved, "operator declined"
        async with self.flight_lock:
            return await self.fleet.execute(name, arguments)

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
            result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
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

    def instructions(self):
        context = {
            "mission": self.summary(),
            "operator_events": [
                event
                for event in self.events
                if event["sequence"] > self.context_index
                and (event["source"] == "operator" or event["kind"] == "mode")
            ],
        }
        context["mission"]["briefing"] = {
            key: self.case["report"][key]
            for key in ("description", "reportedCircumstances", "lastKnownLocation")
        }
        self.context_index = self.event_sequence
        return AGENT_INSTRUCTIONS + "\nCurrent mission context: " + encode(context)

    async def agent(self, text):
        try:
            outputs = ()
            for turn in range(12):
                response = await asyncio.to_thread(
                    self.model.respond,
                    text if turn == 0 else None,
                    [],
                    "low",
                    TOOLS,
                    self.previous_response_id,
                    outputs,
                    self.instructions(),
                )
                self.previous_response_id = response["id"]
                path = self.directory / f"response-{time.time_ns()}.json"
                path.write_text(encode(response) + "\n")
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
                "Operator approved" if message["approved"] else "operator declined",
                result={"id": message["id"], "approved": message["approved"]},
                name=item["card"]["name"],
                arguments=item["card"]["arguments"],
                call_id=item["call_id"],
                source="operator",
            )
            item["future"].set_result(message["approved"])
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

    async def connection(self, websocket):
        first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        assert set(first) == MESSAGE_FIELDS["connect"] and first["type"] == "connect"
        assert first["case_id"].lower() == self.case["id"].lower(), (
            "Service is running a different rescue case"
        )
        await websocket.send(encode({"type": "history", "events": self.events}))
        cursor = self.event_sequence

        async def publish():
            nonlocal cursor
            while True:
                for event in [e for e in self.events if e["sequence"] > cursor]:
                    await websocket.send(encode({"type": "event", "event": event}))
                    cursor = event["sequence"]
                await websocket.send(encode(self.state()))
                await asyncio.sleep(0.1)

        async def consume():
            async for raw in websocket:
                task = asyncio.create_task(self.handle(json.loads(raw), websocket))
                await asyncio.wait([task])
                error = task.exception()
                if error is not None:
                    text = f"{type(error).__name__}: {error}"
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
                    self.event("error", f"Shutdown task: {type(result).__name__}: {result}")
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
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(run(args.case, args.port))
