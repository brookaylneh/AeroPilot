"""Truth-free capture contract and the exact calibrated image transformations."""

import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image

from mission_planner.agents.perception.geometry import Camera
from mission_planner.environment import ROOT
from world.cosys.terrain import terrain_height

CAPTURE_FIELDS = frozenset(
    (
        "schemaVersion",
        "vehicleId",
        "cameraId",
        "imageType",
        "file",
        "width",
        "height",
        "timestampNs",
        "cameraPoseLocalNed",
        "cameraPositionWorldM",
        "localNedOriginUeCm",
        "coordinateSystem",
        "horizontalFovDegrees",
        "vehicleStateBefore",
        "vehicleStateAfter",
        "physicsPausedForCapture",
        "vehiclePoseTiming",
        "captureLatencySeconds",
        "transportBytes",
        "jpegBytes",
        "sha256",
    )
)
STATE_FIELDS = frozenset(
    (
        "id",
        "timestampNs",
        "positionLocalNedM",
        "positionWorldM",
        "orientationNed",
        "velocityLocalNedMps",
    )
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_configuration():
    value = json.loads((ROOT / "mission_planner/measurements/commander.json").read_text())
    config, model = value["calibrated_configuration"], value["observation_model"]
    assert config["native_size"] == [3840, 2160] and config["sam_size"] == [1920, 1080]
    assert config["person_prompt"] == "person" and config["sam_threshold"] == 0.3
    assert config["question"] == "Does this image show a person?"
    assert config["glimmer_threshold"] == model["glimmer_threshold"]
    assert config["crop_native_pixels"] == 512 and config["jpeg_quality"] == 95
    assert config["jpeg_subsampling"] == 0 and config["resize"] == "Pillow Lanczos"
    assert config["person_batching"] in ("dedicated", "verified_decisions")
    if config["person_batching"] == "verified_decisions":
        prompts = config["person_batching_prompts"]
        assert len(prompts) == len(set(prompts)) == 4 and prompts[0] == "person"
    return config, model


def read_capture(path):
    path = Path(path).resolve()
    record = json.loads(path.read_text())
    assert set(record) == CAPTURE_FIELDS, "Capture metadata must contain only the camera contract"
    for key in ("schemaVersion", "width", "height", "timestampNs", "transportBytes", "jpegBytes"):
        assert type(record[key]) is int and record[key] > 0
    assert type(record["physicsPausedForCapture"]) is bool
    assert isinstance(record["cameraId"], str) and record["cameraId"].strip()
    assert isinstance(record["vehicleId"], str) and record["vehicleId"].strip()
    assert type(record["captureLatencySeconds"]) in (int, float)
    assert math.isfinite(record["captureLatencySeconds"])

    def coordinates(value, axes):
        assert set(value) == set(axes), "Camera vectors must contain only their coordinate axes"
        assert all(type(v) in (int, float) and math.isfinite(v) for v in value.values())
        if axes == "xyzw":
            assert abs(math.sqrt(sum(v * v for v in value.values())) - 1) < 1e-5

    for key in ("cameraPositionWorldM", "localNedOriginUeCm"):
        coordinates(record[key], "xyz")
    assert set(record["cameraPoseLocalNed"]) == {"position", "orientation"}
    coordinates(record["cameraPoseLocalNed"]["position"], "xyz")
    coordinates(record["cameraPoseLocalNed"]["orientation"], "xyzw")
    for key in ("vehicleStateBefore", "vehicleStateAfter"):
        assert set(record[key]) == STATE_FIELDS
        assert record[key]["id"] == record["vehicleId"]
        assert type(record[key]["timestampNs"]) is int and record[key]["timestampNs"] > 0
        for field in ("positionLocalNedM", "positionWorldM", "velocityLocalNedMps"):
            coordinates(record[key][field], "xyz")
        coordinates(record[key]["orientationNed"], "xyzw")
    assert (
        record["vehicleStateBefore"]["timestampNs"]
        <= record["timestampNs"]
        <= record["vehicleStateAfter"]["timestampNs"]
    ), "Image timestamp must lie within the recorded vehicle-pose bracket"
    assert (record["physicsPausedForCapture"], record["vehiclePoseTiming"]) in (
        (True, "Frozen physics state"),
        (False, "Ground-truth brackets; image pose and timestamp sampled together by Cosys"),
    ), "Capture pose timing is unverified"
    assert record["imageType"] == "RGB" and record["timestampNs"] > 0
    assert record["captureLatencySeconds"] >= 0 and record["jpegBytes"] > 0
    image = (path.parent / record["file"]).resolve()
    assert image.parent == path.parent and image.is_file(), (
        "Capture image must be adjacent to metadata"
    )
    payload = image.read_bytes()
    assert len(payload) == record["jpegBytes"] and digest(payload) == record["sha256"]
    with Image.open(io.BytesIO(payload)) as frame:
        assert frame.format == "JPEG" and frame.mode == "RGB"
        assert frame.size == (record["width"], record["height"])
    Camera(record)
    return record, payload


def jpeg(image):
    stream = io.BytesIO()
    image.convert("RGB").save(stream, format="JPEG", quality=95, subsampling=0)
    return stream.getvalue()


def prepare_frame(payload):
    with Image.open(io.BytesIO(payload)) as image:
        assert image.width * 9 == image.height * 16
        return jpeg(image.resize((1920, 1080), Image.Resampling.LANCZOS))


def crop(payload, box_xyxy):
    with Image.open(io.BytesIO(payload)) as image:
        scale = image.width / 1920
        x = (box_xyxy[0] + box_xyxy[2]) * scale / 2
        y = (box_xyxy[1] + box_xyxy[3]) * scale / 2
        half = 256 * image.height / 2160
        box = [round(v) for v in (x - half, y - half, x + half, y + half)]
        padding = [
            max(0, -box[0]),
            max(0, -box[1]),
            max(0, box[2] - image.width),
            max(0, box[3] - image.height),
        ]
        return (
            jpeg(image.crop(tuple(box)).resize((512, 512), Image.Resampling.LANCZOS)),
            box,
            padding,
        )


def calibrated(record, config):
    camera = Camera(record)
    agl = camera.world["z"] - terrain_height(camera.world["x"], camera.world["y"])
    return bool(
        [record["width"], record["height"]] == config["native_size"]
        and abs(agl - config["agl_m"]) <= config["flight_tolerance_m"]
        and camera.rotation[2, 0] > 0.99
    )
