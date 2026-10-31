"""Saved-case launch and Studio cancellation, retaining the coupled fleet."""

import asyncio
import json
import os
import signal
from http import HTTPStatus

from websockets.asyncio.server import serve

from mission_planner.environment import load_env
from mission_planner.gcs.service import MESSAGE_FIELDS, Mission, encode, operator_error


async def connection(mission, websocket):
    if websocket.request.path == "/team":
        assert mission.case is not None and not mission.clearing, "No active mission"
        mission.sockets.add(websocket)
        try:
            return await mission.ground_team.connection(websocket)
        finally:
            mission.sockets.discard(websocket)
    first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
    kind = first["type"]
    assert kind in ("launch", "studio", "connect") and set(first) == MESSAGE_FIELDS[kind]
    if kind in ("launch", "studio"):
        action = mission.begin(first["case_id"]) if kind == "launch" else mission.clear()
        task = asyncio.create_task(action)
        await asyncio.wait([task])
        if task.exception() is not None:
            await websocket.send(
                encode({"type": "error", "message": operator_error(task.exception())})
            )
        elif kind == "launch":
            await websocket.send(encode({"type": "launched", "case_id": task.result()}))
        else:
            await websocket.send(encode({"type": "studio"}))
        return
    if (
        mission.case is None
        or mission.clearing
        or first["case_id"].lower() != mission.case["id"].lower()
    ):
        await websocket.send(encode({"type": "idle"}))
        return
    mission.sockets.add(websocket)
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
        healthy = mission.fleet_error is None and mission.sensor_error is None
        state = (
            "cancelling"
            if mission.clearing
            else (
                "starting"
                if not mission.fleet_ready
                else "idle"
                if mission.case is None
                else "ready"
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
