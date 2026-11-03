"""Mission admission: one session that is idle until mission work or a saved case starts it."""

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
    assert kind in ("launch", "connect") and set(first) == MESSAGE_FIELDS[kind]
    if first["case_id"] is not None:
        assert str(uuid.UUID(first["case_id"])) == first["case_id"].lower(), (
            "Case id must be a UUID"
        )
    if kind == "launch":
        assert first["case_id"] is not None, "Launch requires a saved rescue case"
        task = asyncio.create_task(mission.begin(first["case_id"]))
        await asyncio.wait([task])
        await websocket.send(
            encode(
                {"type": "error", "message": operator_error(task.exception())}
                if task.exception() is not None
                else {"type": "launched", "case_id": task.result()}
            )
        )
        return
    # A browser joining during admission or reset waits for its outcome.
    async with mission.lifecycle_lock:
        error = mission.admission_error
    if error is not None:
        await websocket.send(
            encode({"type": "error", "message": "Mission admission failed: " + error})
        )
        return
    await mission.connection(websocket, first)


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
