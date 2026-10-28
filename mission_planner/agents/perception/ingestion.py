"""One durable capture entry point with ordered lanes and visible terminal failures."""

import asyncio
import inspect
import json
import time
from pathlib import Path

from mission_planner.agents.perception.configuration import digest, encode, read_capture

LANES = ("Drone1", "Drone2", "Drone3", "Drone4")


def capture_id(record):
    return digest(f"{record['vehicleId']}:{record['timestampNs']}:{record['sha256']}".encode())[:24]


def load_replay(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    assert set(value) == {"schemaVersion", "id", "label", "source", "records"}
    assert value["schemaVersion"] == 1 and value["source"] == "recorded" and value["records"]
    assert len({r["frame_id"] for r in value["records"]}) == len(value["records"])
    records = []
    for row in value["records"]:
        assert set(row) == {"record", "replay_lane", "frame_id"} and row["replay_lane"] in LANES
        record = (path.parent / row["record"]).resolve()
        assert record.is_relative_to(path.parent) and record.is_file()
        records.append({**row, "record": str(record)})
    return {**value, "records": records}


class Ingestion:
    def __init__(self, directory, source, processor, on_complete, on_change):
        assert source in ("live", "recorded")
        self.directory, self.source = Path(directory), source
        self.directory.mkdir(parents=True, exist_ok=True)
        assert not self.directory.joinpath("frames.json").exists(), "Capture run already exists"
        self.processor, self.on_complete, self.on_change = processor, on_complete, on_change
        self.frames, self.last_timestamp, self.tasks = {}, dict.fromkeys(LANES, 0), []
        self.queues = {name: asyncio.Queue() for name in LANES}
        self.closed = False
        self.loop = asyncio.get_running_loop()
        self.tasks = [asyncio.create_task(self.worker(name)) for name in LANES]

    def changed(self):
        self.directory.joinpath("frames.json").write_text(encode(list(self.frames.values())) + "\n")
        self.on_change()

    async def ingest(self, path, replay_lane=None):
        received_at_ms, received_monotonic_s = time.time() * 1000, time.monotonic()
        assert not self.closed
        assert (self.source == "recorded" and replay_lane in LANES) or (
            self.source == "live" and replay_lane is None
        )
        record, payload = await asyncio.to_thread(read_capture, path)
        assert not self.closed, "Capture ingestion closed while reading this record"
        if self.source == "live":
            assert record["physicsPausedForCapture"] is False
            assert (
                record["vehiclePoseTiming"]
                == "Ground-truth brackets; image pose and timestamp sampled together by Cosys"
            )
        lane = replay_lane if self.source == "recorded" else record["vehicleId"]
        assert lane in LANES and record["timestampNs"] > self.last_timestamp[lane], (
            "Capture order is not increasing"
        )
        identity = capture_id(record)
        assert identity not in self.frames, "Capture was already ingested; no duplicate or retry"
        frame_ref = identity + "-frame.jpg"
        self.directory.joinpath(frame_ref).write_bytes(payload)
        saved = {**record, "file": frame_ref}
        self.directory.joinpath(identity + "-capture.json").write_text(encode(saved) + "\n")
        state = {
            "id": identity,
            "source": self.source,
            "drone_id": record["vehicleId"],
            "replay_lane": replay_lane,
            "lane": lane,
            "status": "queued",
            "stage": "queued",
            "error": None,
            "belief_committed": False,
            "failure_stage": None,
            "queued_at_ms": time.time() * 1000,
            "received_at_ms": received_at_ms,
            "received_monotonic_s": received_monotonic_s,
            "started_at_ms": None,
            "queued_monotonic_s": time.monotonic(),
            "completed_at_ms": None,
            "timestamp_ns": record["timestampNs"],
            "frame_ref": frame_ref,
        }
        self.frames[identity] = state
        self.last_timestamp[lane] = record["timestampNs"]
        self.queues[lane].put_nowait((identity, saved, payload))
        self.changed()
        return identity

    def stage(self, identity, stage):
        self.frames[identity]["stage"] = stage
        self.on_change()

    async def worker(self, lane):
        queue = self.queues[lane]
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                return
            identity, record, payload = item
            state = self.frames[identity]
            state.update(status="processing", stage="preparing", started_at_ms=time.time() * 1000)
            self.changed()
            result = await asyncio.gather(
                asyncio.to_thread(
                    self.processor.process,
                    record,
                    payload,
                    identity,
                    self.source,
                    state["replay_lane"],
                    lambda stage: self.loop.call_soon_threadsafe(self.stage, identity, stage),
                ),
                return_exceptions=True,
            )
            outcome = result[0]
            if not isinstance(outcome, BaseException):
                state["stage"] = "Belief"
                outcome["timings"]["queued_at_ms"] = state["queued_at_ms"]
                outcome["timings"]["queued_monotonic_s"] = state["queued_monotonic_s"]
                outcome["timings"]["received_at_ms"] = state["received_at_ms"]
                outcome["timings"]["received_monotonic_s"] = state["received_monotonic_s"]
                commit = await asyncio.gather(self.complete(outcome), return_exceptions=True)
                state["belief_committed"] = "belief" in outcome
                outcome = commit[0]
            state["completed_at_ms"] = time.time() * 1000
            if isinstance(outcome, BaseException):
                state.update(
                    status="failed",
                    failure_stage=state["stage"],
                    stage="failed",
                    error=f"{type(outcome).__name__}: {outcome}",
                )
            else:
                state.update(status="complete", stage="complete")
            self.changed()
            queue.task_done()

    async def complete(self, result):
        value = self.on_complete(result)
        if inspect.isawaitable(value):
            await value
        result["timings"]["belief_updated_at_ms"] = time.time() * 1000
        result["timings"]["release_to_belief_s"] = (
            time.monotonic() - result["timings"]["queued_monotonic_s"]
        )
        result["timings"]["record_arrival_to_belief_s"] = (
            time.monotonic() - result["timings"]["received_monotonic_s"]
        )
        self.directory.joinpath(result["frame_id"] + "-result.json").write_text(
            encode(result) + "\n"
        )

    def summary(self):
        now = time.time() * 1000
        lanes = []
        for name in LANES:
            frames = [f for f in self.frames.values() if f["lane"] == name]
            waiting = [f for f in frames if f["status"] == "queued"]
            current = [f for f in frames if f["status"] == "processing"]
            pending = [f for f in frames if f["status"] in ("queued", "processing")]
            assert len(current) <= 1
            lanes.append(
                {
                    "lane": name,
                    "queued": len(waiting),
                    "oldest_age_s": (now - pending[0]["queued_at_ms"]) / 1000 if pending else 0.0,
                    "current_frame": current[0]["id"] if current else None,
                    "current_stage": current[0]["stage"] if current else None,
                    "completed": sum(f["status"] == "complete" for f in frames),
                    "failed": sum(f["status"] == "failed" for f in frames),
                }
            )
        return {"source": self.source, "lanes": lanes, "frames": list(self.frames.values())}

    async def drain(self):
        waiting = asyncio.gather(*(q.join() for q in self.queues.values()))
        done, _ = await asyncio.wait([waiting, *self.tasks], return_when=asyncio.FIRST_COMPLETED)
        if waiting not in done:
            waiting.cancel()
            for task in done:
                task.result()
            raise AssertionError("Capture worker stopped before the queue drained")
        await waiting

    async def close(self):
        self.closed = True
        await self.drain()
        for queue in self.queues.values():
            queue.put_nowait(None)
        await asyncio.gather(*self.tasks)
