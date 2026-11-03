"""Bounded browser video diagnostics, separate from the command and model history."""

import json
import math
import re
import time

from mission_planner.gcs.geodesy import NAMES


def record(directory, sample):
    assert isinstance(sample, dict) and set(sample) == {
        "source",
        "drone_id",
        "timestamp_ms",
        "metrics",
    }
    assert sample["source"] in ("video", "tracker") and sample["drone_id"] in NAMES
    assert type(sample["timestamp_ms"]) in (int, float) and math.isfinite(sample["timestamp_ms"])
    metrics = sample["metrics"]
    assert isinstance(metrics, dict) and 1 <= len(metrics) <= 24
    for key, value in metrics.items():
        assert isinstance(key, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", key)
        assert value is None or type(value) in (str, int, float, bool)
        if type(value) in (int, float):
            assert math.isfinite(value)
        if isinstance(value, str):
            assert len(value) <= 1000
    payload = json.dumps({**sample, "received_at_ms": time.time() * 1000}, allow_nan=False) + "\n"
    assert len(payload) <= 8192
    path = directory / "browser-video.jsonl"
    if path.exists() and path.stat().st_size + len(payload) > 4 * 1024 * 1024:
        path.replace(directory / "browser-video-previous.jsonl")
    with path.open("a") as output:
        output.write(payload)
