"""Compact sensor messages; full capture records stay in the mission directory."""

from mission_planner.gcs.geodesy import ORIGIN


def sensor_message(record, preview):
    assert record["validation"]["accepted"]
    assert preview is not None
    position = record["cameraPositionWorldM"]
    return {
        "type": "sensor_frame",
        "frame": {
            "drone_id": record["vehicleId"],
            "capture_id": record["captureId"],
            "captured_at_utc": record["capturedAtUtc"],
            "timestamp_ns": str(record["timestampNs"]),
            "image_path": record["imagePath"],
            "preview": preview,
            "pose": {
                "north_m": position["x"],
                "east_m": position["y"],
                "altitude_msl_m": position["z"] + ORIGIN["altitude_msl_m"],
                "nadir_error_deg": record["validation"]["nadirErrorDegrees"],
            },
        },
    }
