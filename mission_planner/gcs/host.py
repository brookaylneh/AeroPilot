"""Mission admission and explicit Studio reset, retaining the coupled fleet."""

import asyncio
import json
import os
import signal
import uuid
from http import HTTPStatus

from websockets.asyncio.server import serve

from mission_planner.environment import load_env
from mission_planner.gcs.service import MESSAGE_FIELDS, Mission, encode, operator_error


async def connection(mission, websocket):
    first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
    kind = first["type"]
    assert kind in ("status", "launch", "studio", "connect") and set(first) == MESSAGE_FIELDS[kind]
    if kind == "status":
        await websocket.send(encode(mission.status()))
        return
    if kind == "launch":
        assert isinstance(first["case_id"], str), "Launch requires a saved rescue case"
    if "case_id" in first and first["case_id"] is not None:
        assert str(uuid.UUID(first["case_id"])) == first["case_id"].lower(), (
            "Case id must be a UUID"
        )
    if kind in ("launch", "studio") or first["case_id"] is None:
        action = mission.clear() if kind == "studio" else mission.begin(first["case_id"])
        task = asyncio.create_task(action)
        await asyncio.wait([task])
        if task.exception() is not None:
            await websocket.send(
                encode({"type": "error", "message": operator_error(task.exception())})
            )
            return
        elif kind == "launch":
            await websocket.send(encode({"type": "launched", "case_id": task.result()}))
            return
        elif kind == "studio":
            await websocket.send(encode({"type": "studio"}))
            return
    async with mission.lifecycle_lock:
        if (
            not mission.active
            or mission.clearing
            or (
                first["case_id"] is not None
                and (mission.case is None or first["case_id"].lower() != mission.case["id"].lower())
            )
        ):
            response = {"type": "idle"}
        elif mission.admission_error is not None:
            response = {
                "type": "error",
                "message": "Mission admission failed: " + mission.admission_error,
            }
        else:
            response = None
            mission.sockets.add(websocket)
    if response is not None:
        await websocket.send(encode(response))
        return
    try:
        await mission.connection(websocket, first)
    finally:
        mission.sockets.discard(websocket)


async def run():
    load_env()
    port = int(os.environ["SNR_MISSION_PORT"])
    assert 1 <= port <= 65535
    assert os.environ["SNR_HOST"] == "127.0.0.1"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def health(connection, request):
        if request.path != "/health":
            return None
        healthy = mission.fleet_error is None and mission.admission_error is None
        state = (
            "cancelling"
            if mission.clearing
            else (
                "starting" if not mission.fleet_ready else "idle" if not mission.active else "ready"
            )
        )
        return connection.respond(
            HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
            encode({"state": state, "ok": healthy}),
        )

    # The coupled world blocks on motor replies, so the autopilots run for the service lifetime.
    mission = Mission(None)
    try:
        mission.launch(mission.start_fleet())
        async with serve(
            lambda socket: connection(mission, socket),
            os.environ["SNR_HOST"],
            port,
            max_size=32768,
            process_request=health,
        ):
            print(encode({"pid": os.getpid(), "port": port, "mission_id": mission.id}), flush=True)
            await stop.wait()
    finally:
        await mission.close()


if __name__ == "__main__":
    asyncio.run(run())
