"""Run the immutable SAM device container and notify systemd only after HTTP readiness."""

import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

from mission_planner.agents.services.sam_video import (
    GPU_BUDGET_BYTES,
    SAM_COMMIT,
    SAM_SHA256,
    SAM_VARIANT,
)
from mission_planner.agents.vision import vision_http
from mission_planner.environment import ROOT, load_env


def main():
    load_env()
    image = os.environ["SNR_SAM_IMAGE"]
    assert re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", image), (
        "SNR_SAM_IMAGE must be an immutable image ID or repository digest"
    )
    gpu = os.environ["SNR_SAM_GPU"]
    assert re.fullmatch(r"GPU-[0-9a-f-]{36}", gpu), "Select one explicit GPU UUID"
    name = os.environ["SNR_SAM_CONTAINER"]
    assert re.fullmatch(r"snr-sam31-[a-z0-9-]+", name)
    checkpoint, warmup, cache = (
        Path(os.environ[key]) for key in ("SNR_SAM_CHECKPOINT", "SNR_SAM_WARMUP", "SNR_SAM_CACHE")
    )
    vision_socket = Path(os.environ["SNR_VISION_SOCKET"])
    assert all(path.is_absolute() for path in (checkpoint, warmup, cache, vision_socket))
    assert checkpoint.is_file() and warmup.is_file() and cache.is_dir()
    assert vision_socket.parent.is_dir() and not vision_socket.exists(), (
        "SAM socket must be absent in an existing directory; inspect any previous owner"
    )
    assert (os.getuid(), os.getgid()) == (1002, 1003), "SAM mounts require uid 1002, gid 1003"
    command = [
        "docker",
        "create",
        "--pull=never",
        "--rm",
        "--name",
        name,
        "--gpus",
        f"device={gpu}",
        "--pid=host",
        "--network=none",
        "--read-only",
        "--user",
        "1002:1003",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=8",
        "--memory=24g",
        "--shm-size=512m",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,size=4g",
        "--mount",
        f"type=bind,source={ROOT / '.env'},target=/app/.env,readonly",
        "--mount",
        f"type=bind,source={checkpoint},target=/models/sam3.1_multiplex.pt,readonly",
        "--mount",
        f"type=bind,source={warmup.parent},target=/models/warmup,readonly",
        "--mount",
        f"type=bind,source={cache},target=/cache",
        "--mount",
        f"type=bind,source={vision_socket.parent},target=/run/snr",
        "--env",
        f"SNR_SAM_WARMUP=/models/warmup/{warmup.name}",
        "--env",
        f"SNR_VISION_SOCKET=/run/snr/{vision_socket.name}",
        image,
    ]
    try:
        identity = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"SAM container creation failed: {error.stderr.strip()}") from error
    assert re.fullmatch(r"[0-9a-f]{64}", identity)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        assert not stopping, "SAM shutdown already requested"
        stopping = True
        subprocess.run(["docker", "kill", "--signal=TERM", identity], check=True, timeout=10)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    process = subprocess.Popen(["docker", "start", "--attach", identity])
    try:
        deadline = time.monotonic() + 900
        while not vision_socket.exists() and not stopping:
            assert process.poll() is None, "SAM container exited before readiness; inspect its log"
            assert time.monotonic() < deadline, "SAM startup exceeded 900 seconds"
            time.sleep(0.1)
        if stopping:
            assert process.wait(timeout=75) in (0, 128 + signal.SIGTERM), (
                "SAM failed during requested startup shutdown"
            )
            return
        with vision_http() as http:
            response = http.get("health")
            response.raise_for_status()
            health = response.json()
        assert health["status"] == "ready" and health["model"] == "sam3.1"
        assert health["sam_commit"] == SAM_COMMIT and health["model_sha256"] == SAM_SHA256
        assert health["model_variant"] == SAM_VARIANT
        assert health["compiled"] and health["model_instances"] == 1 and not health["sessions"]
        assert health["gpu_budget_bytes"] == GPU_BUDGET_BYTES
        assert 0 < health["peak_process_gpu_bytes"] <= GPU_BUDGET_BYTES
        print(
            json.dumps({"container": identity, "image": image, "ready_s": health["ready_s"]}),
            flush=True,
        )
        if "NOTIFY_SOCKET" in os.environ:
            address = os.environ["NOTIFY_SOCKET"]
            if address.startswith("@"):
                address = "\0" + address[1:]
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.connect(address)
                notify.sendall(b"READY=1")
        code = process.wait()
        assert stopping and code in (0, 128 + signal.SIGTERM), (
            f"SAM container exited unexpectedly with status {code}"
        )
    finally:
        if process.poll() is None:
            if not stopping:
                stop(signal.SIGTERM, None)
            process.wait(timeout=75)
        if vision_socket.exists():
            assert vision_socket.is_socket()
            vision_socket.unlink()


if __name__ == "__main__":
    main()
