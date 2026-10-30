"""Reduce saved request and frame clocks without inferring time from file writes."""

import argparse
import json
from pathlib import Path

import numpy as np

from mission_planner.agents.perception.configuration import encode


def percentiles(values):
    known = [value for value in values if value is not None]
    assert np.isfinite(known).all() and all(value >= 0 for value in known)
    return {
        "measured": len(known),
        "unmeasured": len(values) - len(known),
        "sum": float(sum(known)),
        "mean": float(np.mean(known)) if known else None,
        "percentiles": {f"p{p}": float(np.percentile(known, p)) for p in (50, 95, 100)}
        if known
        else None,
    }


def occupancy(intervals):
    events = []
    for start, end in intervals:
        assert np.isfinite([start, end]).all() and end > start
        events.extend(((start, 1), (end, -1)))
    events.sort()
    assert events
    previous, count, seconds = events[0][0], 0, {}
    for instant, delta in events:
        seconds[count] = seconds.setdefault(count, 0.0) + instant - previous
        count += delta
        assert count >= 0
        previous = instant
    assert count == 0
    return {str(n): duration for n, duration in sorted(seconds.items()) if duration > 0}


def summarize(run, request_source):
    run = Path(run)
    assert request_source in ("results", "sidecars")
    states = json.loads(run.joinpath("evidence/frames.json").read_text())
    assert states and all(frame["status"] in ("complete", "failed") for frame in states)
    frames = [
        json.loads(run.joinpath("evidence", frame["id"] + "-result.json").read_text())
        for frame in states
        if frame["status"] == "complete"
    ]
    if request_source == "results":
        assert len(frames) == len(states), "Legacy results cannot account for failed request time"
        requests = [r for frame in frames for r in frame["timings"]["requests"]]
    else:
        requests = [
            request
            for path in sorted(run.joinpath("evidence").glob("*-requests.json"))
            for request in json.loads(path.read_text())
        ]
    assert requests
    sam = [request for request in requests if request["model"] == "sam"]
    measured_sam = [request for request in sam if "server" in request]
    assert measured_sam
    instances = {}
    for request in measured_sam:
        timing, server = request["server_timings"], request["server"]
        values = [timing[key] for key in ("decode_s", "tiling_s", "forward_s", "postprocess_s")]
        assert np.isfinite(values).all() and min(values) >= 0
        assert sum(values) <= timing["total_s"] + 1e-6
        start, end = server["started_at_unix_s"], server["finished_at_unix_s"]
        assert abs(end - start - timing["total_s"]) < 0.01
        instances.setdefault(server["instance_id"], []).append((start, end))
    utilization = {}
    for identity, intervals in instances.items():
        intervals.sort()
        times = occupancy(intervals)
        busy = sum(value for count, value in times.items() if int(count) > 0)
        span = max(end for _, end in intervals) - min(start for start, _ in intervals)
        starts = [start for start, _ in intervals]
        last_end, gaps = intervals[0][1], []
        for start, end in intervals[1:]:
            if start > last_end:
                gaps.append(start - last_end)
            last_end = max(last_end, end)
        utilization[identity] = {
            "requests": len(intervals),
            "span_s": span,
            "busy_union_s": busy,
            "busy_fraction": busy / span,
            "idle_s": span - busy,
            "start_interval_s": percentiles(np.diff(starts).tolist()),
            "idle_gap_s": percentiles(gaps),
            "handler_concurrency_seconds": times,
        }
    timings = [frame["timings"] for frame in frames]
    result = {
        "run": str(run),
        "request_source": request_source,
        "frames": len(states),
        "completed": len(frames),
        "failed": sum(frame["status"] == "failed" for frame in states),
        "sam_requests": len(sam),
        "sam_requests_without_server_timing": len(sam) - len(measured_sam),
        "sam_server_stage_seconds": {
            key: percentiles([request["server_timings"][key] for request in measured_sam])
            for key in ("decode_s", "tiling_s", "forward_s", "postprocess_s", "total_s")
        },
        "sam_instances": utilization,
        "sam_busy_fraction": sum(row["busy_union_s"] for row in utilization.values())
        / sum(row["span_s"] for row in utilization.values()),
        "sam_async_wrapper_seconds": {
            key: percentiles(
                [
                    r["server_timings"][key] if key in r["server_timings"] else None
                    for r in measured_sam
                ]
            )
            for key in ("dispatch_wait_s", "handler_total_s")
        },
        "sam_roundtrip_outside_inference_s": percentiles(
            [r["roundtrip_s"] - r["server_timings"]["total_s"] for r in measured_sam]
        ),
        "client_request_seconds": {
            model: {
                key: percentiles([r[key] for r in requests if r["model"] == model])
                for key in (
                    "prepare_s",
                    "client_queue_s",
                    "upload_s",
                    "roundtrip_s",
                    "response_decode_s",
                )
            }
            for model in ("sam", "glimmer")
        },
        "sam_client_inflight_seconds": occupancy(
            [
                (
                    r["started_at_unix_s"] + r["prepare_s"],
                    r["started_at_unix_s"] + r["prepare_s"] + r["roundtrip_s"],
                )
                for r in sam
            ]
        ),
        "frame_seconds": {
            "queue_wait": percentiles(
                [(t["processing_started_at_ms"] - t["queued_at_ms"]) / 1000 for t in timings]
            ),
            "intake": percentiles(
                [(t["queued_at_ms"] - t["received_at_ms"]) / 1000 for t in timings]
            ),
            "belief_commit": percentiles([t["belief_commit_s"] for t in timings]),
            "arrival_to_belief": percentiles([t["record_arrival_to_belief_s"] for t in timings]),
        },
        "scope": "Frame requests only; startup and warmup excluded. Busy means union of SAM decode/tiling/forward/postprocessing intervals, excluding the async wrapper lock wait; it is not measured GPU kernel utilization. Roundtrip outside inference includes async dispatch/lock wait, transport and platform time. Older synchronous responses lack async wrapper timings. Client upload is socket body-write duration, not independently measured last-byte arrival. Server span uses only each instance's own clock; client in-flight spans use the client clock.",
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--request-source", choices=("results", "sidecars"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Profile outputs are immutable"
    args.output.write_text(encode(summarize(args.run, args.request_source)) + "\n")


if __name__ == "__main__":
    main()
