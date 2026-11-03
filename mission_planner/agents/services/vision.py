"""Local SAM 3.1 service: one GPU worker and up to four independent video sessions."""

import asyncio
import importlib.metadata
import json
import math
import os
import resource
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mission_planner.agents.services.sam_video import (
    ALLOCATOR_BYTES,
    GPU_BUDGET_BYTES,
    SAM_COMMIT,
    SAM_SHA256,
    SAM_VARIANT,
    SamVideo,
    prepare_frame,
    warmup,
)


class VideoService:
    def __init__(self, model, executor):
        self.model, self.executor = model, executor
        self.preparation = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sam-prepare")
        self.gpu_lock = asyncio.Lock()
        self.instance_id = uuid.uuid4().hex
        self.sessions = {}
        self.error = None
        self.compilation = model.compile_stats()

    def start(self, body):
        assert self.error is None, self.error
        assert set(body) == {"session_id", "drone_id", "target", "threshold", "inference_height"}
        identity = str(uuid.UUID(body["session_id"]))
        assert identity == body["session_id"] and identity not in self.sessions
        assert body["drone_id"] in ("Drone1", "Drone2", "Drone3", "Drone4")
        assert all(r["drone_id"] != body["drone_id"] for r in self.sessions.values()), (
            "This drone already has a SAM session"
        )
        assert len(self.sessions) < 4, "SAM session capacity exceeded"
        assert type(body["threshold"]) in (int, float) and math.isfinite(body["threshold"])
        assert body["inference_height"] == 1080, "SAM requires its qualified 1080p profile"
        state = self.model.open(body["target"], body["threshold"])
        self.sessions[identity] = {
            **body,
            "state": state,
            "frame_index": -1,
            "timestamp_ns": 0,
            "status": "open",
            "error": None,
            "usage": self.model.usage(state),
        }
        return {
            **body,
            "instance_id": self.instance_id,
            "model": "sam3.1",
            "model_variant": SAM_VARIANT,
            "tracker": "Object Multiplex",
        }

    def session(self, body):
        assert body["instance_id"] == self.instance_id, "SAM worker identity changed"
        return self.sessions[body["session_id"]]

    def _infer(self, state, prepared):
        result = self.model.advance(state, prepared)
        return result, self.model.usage(state), self.model.compile_stats()

    async def advance(self, body):
        assert set(body) == {"session_id", "instance_id", "frame_index", "timestamp_ns", "jpeg"}
        assert self.error is None, self.error
        row = self.session(body)
        assert row["status"] == "open", "SAM session is busy or has failed"
        assert type(body["frame_index"]) is int and body["frame_index"] == row["frame_index"] + 1, (
            "SAM frames arrived out of order"
        )
        assert type(body["timestamp_ns"]) is int and body["timestamp_ns"] > row["timestamp_ns"], (
            "SAM capture timestamp did not advance"
        )
        row["status"] = "pending"
        submitted = time.monotonic()
        loop = asyncio.get_running_loop()
        try:
            prepared = await loop.run_in_executor(self.preparation, prepare_frame, body["jpeg"])
            waiting = time.monotonic()
            # asyncio.Lock serves waiting requests in arrival order. Each drone may
            # have only one outstanding frame, bounding the queue and preserving fairness.
            async with self.gpu_lock:
                assert self.error is None, self.error
                started = time.monotonic()
                try:
                    result, usage, compilation = await loop.run_in_executor(
                        self.executor, self._infer, row["state"], prepared
                    )
                except Exception as error:
                    self.error = f"SAM inference failed: {type(error).__name__}: {error}"
                    raise RuntimeError(self.error) from error
            row.update(
                frame_index=body["frame_index"],
                timestamp_ns=body["timestamp_ns"],
                status="open",
                usage=usage,
            )
            self.compilation = compilation
            result["timings"].update(
                queue_s=started - waiting, total_s=time.monotonic() - submitted
            )
            return {
                **{
                    key: body[key]
                    for key in ("session_id", "instance_id", "frame_index", "timestamp_ns")
                },
                **result,
            }
        except Exception as error:
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise RuntimeError(f"SAM session {body['session_id']}: {row['error']}") from error

    async def close(self, body):
        assert set(body) == {"session_id", "instance_id"}
        row = self.session(body)
        assert row["status"] != "pending", "Cannot close a SAM frame in progress"
        async with self.gpu_lock:
            del self.sessions[body["session_id"]]
        return {**body, "status": "closed"}

    def health(self):
        return {
            "status": "ready" if self.error is None else "failed",
            "error": self.error,
            "instance_id": self.instance_id,
            "model": "sam3.1",
            "model_variant": SAM_VARIANT,
            "model_sha256": SAM_SHA256,
            "sam_commit": SAM_COMMIT,
            "tracker": "Object Multiplex",
            "model_instances": 1,
            "max_sessions": 4,
            "inference_height": 1080,
            "model_image_size": 1008,
            "autocast": "bfloat16",
            "tf32": True,
            "compiled": self.model.compiled,
            "compilation": self.compilation,
            "gpu_budget_bytes": GPU_BUDGET_BYTES,
            "allocator_budget_bytes": ALLOCATOR_BYTES,
            "sessions": {
                identity: {
                    key: row[key]
                    for key in ("drone_id", "target", "frame_index", "status", "error", "usage")
                }
                for identity, row in self.sessions.items()
            },
        }


def create_app(service, runtime):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/vision/{action}")
    async def request(action: str, request: Request):
        try:
            assert action in ("start", "frame", "close"), "Unknown vision action"
            if action == "frame":
                assert request.headers["content-type"] == "image/jpeg"
                assert int(request.headers["content-length"]) <= 16_000_000, "JPEG is too large"
                body = dict(request.query_params)
                assert set(body) == {"session_id", "instance_id", "frame_index", "timestamp_ns"}
                body["frame_index"] = int(body["frame_index"])
                body["timestamp_ns"] = int(body["timestamp_ns"])
                body["jpeg"] = await request.body()
                task = asyncio.create_task(service.advance(body))
                try:
                    return await asyncio.shield(task)
                finally:
                    if not task.done():
                        await task
            body = await request.json()
            if action == "start":
                return service.start(body)
            return await service.close(body)
        except (AssertionError, ValueError, KeyError) as error:
            raise HTTPException(400, detail=f"{type(error).__name__}: {error}") from error
        except Exception as error:
            raise HTTPException(500, detail=f"SAM 3.1: {type(error).__name__}: {error}") from error

    @app.get("/health")
    async def health():
        result = {
            **service.health(),
            **runtime,
            "rss_bytes": int(Path("/proc/self/statm").read_text().split()[1])
            * os.sysconf("SC_PAGE_SIZE"),
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        }
        return JSONResponse(result, status_code=200 if service.error is None else 503)

    return app


def main():
    import cv2
    import pynvml
    import torch
    import uvicorn

    from mission_planner.environment import load_env

    load_env()
    assert torch.cuda.device_count() == 1, "SAM requires exactly one visible GPU"
    torch.set_num_threads(1)
    cv2.setNumThreads(1)
    torch.cuda.set_per_process_memory_fraction(
        ALLOCATOR_BYTES / torch.cuda.get_device_properties(0).total_memory
    )
    checkpoint = Path(os.environ["SNR_SAM_CHECKPOINT"])
    protocol = Path(os.environ["SNR_SAM_WARMUP"])
    vision_socket = os.environ["SNR_VISION_SOCKET"]
    assert checkpoint.is_absolute() and protocol.is_absolute() and vision_socket.startswith("/")
    assert not Path(vision_socket).exists(), "SAM socket already exists; inspect its owner"
    pynvml.nvmlInit()
    device = pynvml.nvmlDeviceGetHandleByUUID(
        "GPU-" + str(torch.cuda.get_device_properties(0).uuid)
    )
    runtime = {
        "gpu": torch.cuda.get_device_name(),
        "gpu_count": torch.cuda.device_count(),
        "process_gpu_bytes": 0,
        "peak_process_gpu_bytes": 0,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "torchvision", "sam3", "fastapi", "uvicorn", "opencv-python")
        },
    }
    stopped = threading.Event()

    def monitor():
        while not stopped.is_set():
            processes = pynvml.nvmlDeviceGetComputeRunningProcesses(device)
            used = [p.usedGpuMemory for p in processes if p.pid == os.getpid()]
            assert len(used) <= 1
            if used:
                runtime["process_gpu_bytes"] = used[0]
                runtime["peak_process_gpu_bytes"] = max(runtime["peak_process_gpu_bytes"], used[0])
                if used[0] > GPU_BUDGET_BYTES:
                    print(
                        f"SAM process GPU budget exceeded: {used[0]}", file=sys.stderr, flush=True
                    )
                    os._exit(78)
            stopped.wait(0.02)

    def thread_failed(error):
        print(f"SAM monitor failed: {error.exc_value}", file=sys.stderr, flush=True)
        os._exit(79)

    threading.excepthook = thread_failed
    monitor_thread = threading.Thread(target=monitor, name="sam-memory")
    monitor_thread.start()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam-gpu")
    service = None
    try:
        started = time.monotonic()
        model = executor.submit(SamVideo, checkpoint).result()
        runtime["warmup"] = executor.submit(warmup, model, protocol).result()
        runtime["ready_s"] = time.monotonic() - started
        assert runtime["process_gpu_bytes"] > 0, "NVML could not identify this SAM process"
        service = VideoService(model, executor)
        print(json.dumps({**service.health(), **runtime}, allow_nan=False), flush=True)
        uvicorn.run(create_app(service, runtime), uds=vision_socket, access_log=False)
    finally:
        if service is not None:
            service.preparation.shutdown(wait=True)
        executor.shutdown(wait=True)
        stopped.set()
        monitor_thread.join(timeout=5)
        assert not monitor_thread.is_alive()
        print(json.dumps({"shutdown": runtime}, allow_nan=False), flush=True)
        pynvml.nvmlShutdown()


if __name__ == "__main__":
    main()
