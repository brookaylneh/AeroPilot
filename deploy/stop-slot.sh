#!/usr/bin/env bash
# Coupled, world-first shutdown of one environment slot: end the grounded mission, signal Unreal,
# wait for its EndPlay, stop the mission unit, then prove every listener is gone.
set -euo pipefail
cd "$(dirname "$0")/.."
export SLOT="${1:?Usage: stop-slot.sh SLOT}"
[[ "$SLOT" =~ ^[0-9]+$ ]]
set -a
. .local/runtime.env
. ".local/slots/$SLOT/runtime.env"
set +a
sudo -v
.venv-cosys/bin/python - <<'PY'
import json
import os
import select
import subprocess
import time
from datetime import datetime, timezone
from websockets.sync.client import connect

mission_url = f"ws://127.0.0.1:{os.environ['SNR_MISSION_PORT']}"
with connect(mission_url, open_timeout=10) as socket:
    socket.send(json.dumps({"type": "connect", "case_id": None}))

    def state():
        while True:
            message = json.loads(socket.recv(timeout=30))
            assert message["type"] != "error", message["message"]
            if message["type"] == "state":
                return message

    current = state()
    assert len(current["vehicles"]) == 4 and all(
        not v["armed"] and v["landed_state"] == 1 and not v["link_lost"] and v["ekf_healthy"]
        for v in current["vehicles"]
    ), "Fleet must be healthy, landed and disarmed"
    if current["mission"]["active"]:
        socket.send(json.dumps({"type": "end_mission"}))
        while state()["mission"]["active"]:
            pass

slot = os.environ["SLOT"]
container = os.environ["SNR_CONTAINER_NAME"]
assert container == f"snr-unreal-slot{slot}"
since = datetime.now(timezone.utc).isoformat()
logs = subprocess.Popen(
    ["docker", "logs", "--follow", "--since", since, container],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:
    subprocess.run(["docker", "kill", "--signal=TERM", container], check=True)
    deadline, buffer = time.monotonic() + 60, b""
    while b"UWorld::CleanupWorld for OpenMeadow," not in buffer:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "Unreal EndPlay marker timed out"
        assert select.select([logs.stdout], [], [], remaining)[0], "Unreal EndPlay marker timed out"
        chunk = os.read(logs.stdout.fileno(), 65536)
        assert chunk, "Unreal log ended before EndPlay"
        buffer = (buffer + chunk)[-131072:]
    subprocess.run(["sudo", "-n", "systemctl", "stop", f"snr-mission@{slot}"], check=True)
finally:
    logs.terminate()
    logs.wait(timeout=5)
PY
timeout 180 bash -c "while systemctl is-active --quiet snr-unreal@$SLOT; do sleep 1; done"
test "$(systemctl show "snr-unreal@$SLOT" -p ActiveState --value)" = inactive
test "$(systemctl show "snr-unreal@$SLOT" -p Result --value)" = success
systemctl show "snr-unreal@$SLOT" -p ExecMainStatus -p Result
test -z "$(docker ps -aq --filter "name=^/$SNR_CONTAINER_NAME$")"
.venv-cosys/bin/python - <<'PY'
import os
from mission_planner.gcs.sitl import check_ports
port = lambda name: int(os.environ[name])
instances = [port("SNR_SITL_INSTANCE_BASE") + i for i in range(4)]
check_ports([port("SNR_MISSION_PORT"), port("SNR_COSYS_PORT"),
             *[9002 + 10 * i for i in instances], *[9003 + 10 * i for i in instances],
             *[port("SNR_MAVLINK_BASE_PORT") + i for i in range(4)],
             *[port("SNR_SITL_RC_BASE_PORT") + 10 * i for i in range(4)]])
PY
