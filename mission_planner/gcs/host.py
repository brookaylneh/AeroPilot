"""One installed mission service; the first browser chooses its rescue case."""

import asyncio
import json
import os
import signal
from http import HTTPStatus

from websockets.asyncio.server import serve

from mission_planner.environment import load_env
from mission_planner.gcs.service import MESSAGE_FIELDS, Mission, encode, read_case


async def run():
    load_env()
    port = int(os.environ["SNR_MISSION_PORT"])
    assert 1 <= port <= 65535
    assert os.environ["SNR_HOST"] == "127.0.0.1"
    mission = None
    stop = asyncio.Event()
    lock = asyncio.Lock()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def connection(websocket):
        nonlocal mission
        if websocket.request.path == "/team":
            assert mission is not None, "Open a mission before connecting a receiving team"
            return await mission.ground_team.connection(websocket)
        first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        assert set(first) == MESSAGE_FIELDS["connect"] and first["type"] == "connect"
        async with lock:
            if mission is None:
                mission = Mission(read_case(first["case_id"]))
                mission.launch(mission.start_fleet())
            if first["case_id"].lower() != mission.case["id"].lower():
                await websocket.send(
                    encode(
                        {
                            "type": "error",
                            "message": "Another rescue case is active. Stop the mission service before changing cases.",
                        }
                    )
                )
                await websocket.close(code=1008, reason="Different active rescue case")
                return
        await mission.connection(websocket, first)

    def health(connection, request):
        if request.path != "/health":
            return None
        healthy = mission is None or (mission.fleet_error is None and mission.sensor_error is None)
        state = "idle" if mission is None else "ready" if mission.fleet_ready else "starting"
        return connection.respond(
            HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
            encode({"state": state, "ok": healthy}),
        )

    try:
        async with serve(
            connection, os.environ["SNR_HOST"], port, max_size=32768, process_request=health
        ):
            print(encode({"pid": os.getpid(), "port": port, "state": "idle"}), flush=True)
            await stop.wait()
    finally:
        if mission is not None:
            await mission.close()


if __name__ == "__main__":
    asyncio.run(run())
