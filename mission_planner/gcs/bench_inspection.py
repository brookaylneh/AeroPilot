"""Measure four recorded SAM image streams at one frame per second, without retries."""

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

from mission_planner.agents.models import endpoint, post
from mission_planner.agents.services.benchmark import percentiles, prepare_frame, save
from mission_planner.environment import ROOT, load_env
from mission_planner.gcs.geodesy import NAMES
from mission_planner.gcs.inspection import annotated_frame, sampling_start

INTERVAL_S = 1.0
FRAMES_PER_DRONE = 10


def recorded_frames():
    protocol = json.loads((ROOT / "mission_planner/measurements/modal.json").read_text())["sam"][
        "protocol"
    ]
    assert len(protocol["frames"]) == len(NAMES) * FRAMES_PER_DRONE
    assert len({source["path"] for source in protocol["frames"]}) == len(protocol["frames"])
    assert protocol["prompts"] == ["person"]
    frames = []
    for source in protocol["frames"]:
        native = (ROOT / source["path"]).read_bytes()
        assert hashlib.sha256(native).hexdigest() == source["sha256"], (
            f"Recorded source changed: {source['path']}"
        )
        jpeg = prepare_frame(native)
        frames.append((source, jpeg))
    return protocol, frames


async def measure(directory):
    directory.mkdir(parents=True, exist_ok=False)
    protocol, frames = recorded_frames()
    save(
        directory / "protocol.json",
        {
            "scope": "Recorded-image SAM HTTP and mask validation only; excludes capture, input preparation, flight, Wonder and browser rendering.",
            "drone_ids": list(NAMES),
            "frames_per_drone": FRAMES_PER_DRONE,
            "interval_s": INTERVAL_S,
            "phase_offsets_s": {name: i * INTERVAL_S / len(NAMES) for i, name in enumerate(NAMES)},
            "request_timeout_s": 5,
            "prompts": protocol["prompts"],
            "threshold": protocol["sam_threshold"],
            "frames": protocol["frames"],
        },
    )
    clients = [endpoint("SAM") for _ in NAMES]
    for client in clients:
        client.timeout = httpx.Timeout(5)
    started, started_at = time.monotonic(), time.time()
    rows = []

    async def lane(index):
        name, client = NAMES[index], clients[index]
        first_slot = sampling_start(name, started / INTERVAL_S) * INTERVAL_S

        def record(response):
            response.read()
            row["http_status"] = response.status_code
            with (directory / (row["id"] + ".response.json")).open("xb") as stream:
                stream.write(response.content)

        client.event_hooks["response"].append(record)
        for number in range(FRAMES_PER_DRONE):
            scheduled = first_slot + number * INTERVAL_S
            await asyncio.sleep(max(0, scheduled - time.monotonic()))
            source, jpeg = frames[index * FRAMES_PER_DRONE + number]
            body = {
                "jpeg": base64.b64encode(jpeg).decode(),
                "prompts": protocol["prompts"],
                "threshold": protocol["sam_threshold"],
            }
            began = time.monotonic()
            row = {
                "id": f"{name}-{number:02d}",
                "drone_id": name,
                "frame": number,
                "source": source,
                "jpeg_sha256": hashlib.sha256(jpeg).hexdigest(),
                "jpeg_bytes": len(jpeg),
                "request": {"prompts": body["prompts"], "threshold": body["threshold"]},
                "scheduled_at_unix_s": started_at + scheduled - started,
                "started_at_unix_s": time.time(),
                "started_after_run_s": began - started,
                "start_lateness_s": began - scheduled,
                "http_status": None,
                "timing": {},
                "mask_valid": False,
                "masks_validated": 0,
                "validation_s": None,
            }
            try:
                response = await asyncio.to_thread(post, client, "", body, row["timing"])
                validation_started = time.monotonic()
                _, detections = annotated_frame(jpeg, response, protocol["prompts"][0])
                row.update(
                    mask_valid=True,
                    masks_validated=len(detections),
                    validation_s=time.monotonic() - validation_started,
                    server=response["server"],
                    server_timings=response["timings"],
                )
                overrun = time.monotonic() - (scheduled + INTERVAL_S)
                assert overrun <= 0, (
                    f"{name}: frame {number} missed the next {INTERVAL_S:g} s slot by {overrun:.6f} s"
                )
            finally:
                error = sys.exc_info()[1]
                row.update(
                    total_s=time.monotonic() - began,
                    finished_at_unix_s=time.time(),
                    error=None if error is None else f"{type(error).__name__}: {error}",
                )
                save(directory / (row["id"] + ".json"), row)
                rows.append(row)

    try:
        results = await asyncio.gather(
            *(lane(i) for i in range(len(NAMES))), return_exceptions=True
        )
    finally:
        for client in clients:
            client.close()
    failures = [error for error in results if isinstance(error, BaseException)]
    successful = [row for row in rows if row["error"] is None]
    masks = sum(row["masks_validated"] for row in rows)
    result = {
        "scope": "Recorded images only; not a live four-camera capacity measurement.",
        "accepted": len(successful) == len(NAMES) * FRAMES_PER_DRONE and masks > 0,
        "attempted": len(rows),
        "successful": len(successful),
        "masks_validated": masks,
        "mask_valid_frames": sum(row["mask_valid"] for row in rows),
        "total_s": time.monotonic() - started,
        "latency_s": percentiles([row["total_s"] for row in rows]) if rows else None,
        "latency_scope": "HTTP round trip plus mask decoding and annotation validation; raw HTTP timings are retained per request.",
        "streams": {},
        "failures": [f"{type(error).__name__}: {error}" for error in failures],
    }
    for name in NAMES:
        stream = [row for row in rows if row["drone_id"] == name]
        intervals = [
            after["started_after_run_s"] - before["started_after_run_s"]
            for before, after in zip(stream, stream[1:])
        ]
        result["streams"][name] = {
            "attempted": len(stream),
            "successful": sum(row["error"] is None for row in stream),
            "start_intervals_s": intervals,
            "interval_s": percentiles(intervals) if intervals else None,
            "latency_s": percentiles([row["total_s"] for row in stream]) if stream else None,
        }
    if masks == 0:
        result["failures"].append("No masks returned; the mask output contract was not exercised")
    save(directory / "result.json", result)
    print(json.dumps(result, allow_nan=False), flush=True)
    if failures:
        raise RuntimeError(
            "Recorded SAM rate test failed; see " + str(directory / "result.json")
        ) from failures[0]
    assert result["accepted"], "Recorded SAM rate test did not validate any masks"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_env()
    asyncio.run(measure(args.output))


if __name__ == "__main__":
    main()
