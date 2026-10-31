"""Installed fleet owner with one ephemeral mission and no saved-case loading."""

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
        assert mission.report is not None and not mission.clearing, "No active mission"
        return await mission.ground_team.connection(websocket)
    first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
    if first["type"] in ("launch", "reset"):
        if first["type"] == "launch":
            action = mission.begin(first)
        else:
            assert set(first) == MESSAGE_FIELDS["reset"]
            action = mission.clear()
        task = asyncio.create_task(action)
        await asyncio.wait([task])
        if task.exception() is not None:
            await websocket.send(
                encode({"type": "error", "message": operator_error(task.exception())})
            )
        elif first["type"] == "launch":
            await websocket.send(encode({"type": "launched", "run_id": task.result()}))
        else:
            await websocket.send(encode({"type": "idle"}))
        return
    assert set(first) == MESSAGE_FIELDS["connect"] and first["type"] == "connect"
    if mission.report is None:
        await websocket.send(encode({"type": "idle"}))
        return
    await mission.connection(websocket, first)


async def run():
    load_env()
    port = int(os.environ["SNR_MISSION_PORT"])
    assert 1 <= port <= 65535 and os.environ["SNR_HOST"] == "127.0.0.1"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def health(connection, request):
        if request.path != "/health":
            return None
        healthy = mission.fleet_error is None and mission.sensor_error is None
        state = (
            "starting" if not mission.fleet_ready else "idle" if mission.report is None else "ready"
        )
        return connection.respond(
            HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
            encode({"state": state, "ok": healthy}),
        )

    # The coupled world needs motor replies for the entire service lifetime.
    mission = Mission(None)
    startup = asyncio.create_task(mission.start_fleet())
    try:
        async with serve(
            lambda socket: connection(mission, socket),
            os.environ["SNR_HOST"],
            port,
            max_size=65536,
            process_request=health,
        ):
            print(encode({"pid": os.getpid(), "port": port}), flush=True)
            await stop.wait()
    finally:
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await mission.close()


if __name__ == "__main__":
    asyncio.run(run())
