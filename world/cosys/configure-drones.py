"""Generate ArduCopter settings from the authored geometry and mission geodesy."""

import argparse
import json
import math
from pathlib import Path

from mission_planner.environment import load_env
from mission_planner.gcs import geodesy
from world.cosys.evidence import CAMERA_ID, FOV_DEG, MOUNT_NED_M

ROOT = Path(__file__).resolve().parents[2]


def generate(world, rpc_port, instance_base):
    assert 1024 <= rpc_port <= 65535
    assert 0 <= instance_base and 9003 + 10 * (instance_base + 3) <= 65535
    assert world["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
    assert world["north"] == "+X" and world["east"] == "+Y"
    assert world["moveWorldOrigin"] is False
    assert set(world["vehicles"]) == set(geodesy.NAMES)
    origin = world["playerStartUeCm"]
    assert origin == {"x": 0, "y": 0, "z": 4000}
    ev100 = json.loads((ROOT / "world/unreal/valley-lighting.json").read_text())["exposureEv100"]
    # Manual exposure without the physical camera meters at EV100 0; the compensation sets the EV.
    exposure = {
        "AutoExposureMethod": 2,
        "AutoExposureApplyPhysicalCameraExposure": False,
        "AutoExposureCompensation": -ev100,
        "WhiteBalanceTemperature": 6500,
        "WhiteBalanceTint": 0,
        "MotionBlurAmount": 0,
        "MotionBlurMax": 0,
        "BloomIntensity": 0,
        "LumenGIEnable": True,
        "LumenReflectionEnable": True,
    }
    settings = {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "PhysicsEngineName": "FastPhysicsEngine",
        "ClockType": "SteppableClock",
        "ClockSpeed": 1,
        "ViewMode": "Manual",
        "MoveWorldOrigin": False,
        "EnableRpc": True,
        "LocalHostIp": "127.0.0.1",
        "ApiServerPort": rpc_port,
        "InitialInstanceSegmentation": False,
        "RecordUIVisible": False,
        "LogMessagesVisible": False,
        "SubWindows": [],
        "Recording": {"Enabled": False},
        "OriginGeopoint": {
            "Latitude": geodesy.ORIGIN["latitude_deg"],
            "Longitude": geodesy.ORIGIN["longitude_deg"],
            "Altitude": geodesy.ORIGIN["altitude_msl_m"] + origin["z"] / 100,
        },
        "CameraDefaults": {
            "CaptureSettings": [
                dict(ImageType=-1, **exposure),
                dict(ImageType=0, Width=3840, Height=2160, ForceUpdate=False, **exposure),
            ]
        },
        "Vehicles": {},
    }
    port = world["port"]
    centre = port["centerUeCm"]
    assert all(math.isfinite(centre[axis]) for axis in "xyz")
    assert len(port["dimensionsMetres"]) == 3
    assert all(math.isfinite(value) and value > 0 for value in port["dimensionsMetres"])
    for axis, width in zip("xy", port["dimensionsMetres"]):
        assert world["searchBoundsCm"]["min"][axis] < centre[axis] - width * 50
        assert centre[axis] + width * 50 < world["searchBoundsCm"]["max"][axis]
    for index, name in enumerate(geodesy.NAMES):
        item = world["vehicles"][name]
        p = item["spawnUeCm"]
        assert set(p) == {"x", "y", "z"}
        assert all(math.isfinite(p[axis]) for axis in "xyz")
        assert p == {
            "x": centre["x"] + (250 if index < 2 else -250),
            "y": centre["y"] + (-250 if index % 2 == 0 else 250),
            "z": centre["z"] + 20,
        }, f"{name}: spawn must match the port's 5 m grid and 0.20 m top offset"
        assert 0 <= item["yawDegrees"] < 360
        instance = instance_base + index
        vehicle = {
            "VehicleType": "ArduCopter",
            "AutoCreate": True,
            "UseSerial": False,
            "LocalHostIp": "127.0.0.1",
            "UdpIp": "127.0.0.1",
            "UdpPort": 9003 + 10 * instance,
            "ControlPortLocal": 9002 + 10 * instance,
            "X": (p["x"] - origin["x"]) / 100,
            "Y": (p["y"] - origin["y"]) / 100,
            "Z": -(p["z"] - origin["z"]) / 100,
            "Yaw": item["yawDegrees"],
            "EnableCollisions": True,
            "EnableCollisionPassthrough": False,
            "RC": {"RemoteControlID": -1},
            "Sensors": {
                "Imu": {"SensorType": 2, "Enabled": True},
                "Gps": {
                    "SensorType": 3,
                    "Enabled": True,
                    "UpdateLatency": 0,
                    "UpdateFrequency": 50,
                },
            },
            "Cameras": {
                "front_center": {
                    "X": 0.6,
                    "Y": 0,
                    "Z": 0.05,
                    "Pitch": -90,
                    "Yaw": 0,
                    "Roll": 0,
                    "Gimbal": {"Stabilization": 1, "Pitch": -90, "Roll": 0},
                    "CaptureSettings": [
                        dict(ImageType=-1, FOV_Degrees=70, **exposure),
                        dict(
                            ImageType=0,
                            Width=3840,
                            Height=2160,
                            FOV_Degrees=70,
                            ForceUpdate=False,
                            **exposure,
                        ),
                    ],
                },
                CAMERA_ID: {
                    **dict(zip(("X", "Y", "Z"), MOUNT_NED_M)),
                    "Pitch": 0,
                    "Yaw": 0,
                    "Roll": 0,
                    "Gimbal": {"Stabilization": 1, "Pitch": 0, "Yaw": 0, "Roll": 0},
                    "CaptureSettings": [
                        dict(ImageType=-1, FOV_Degrees=FOV_DEG, **exposure),
                        dict(
                            ImageType=0,
                            Width=3840,
                            Height=2160,
                            FOV_Degrees=FOV_DEG,
                            ForceUpdate=False,
                            **exposure,
                        ),
                    ],
                },
            },
        }
        assert (
            math.dist(
                (vehicle["X"], vehicle["Y"]),
                geodesy.to_local(*geodesy.to_geodetic(vehicle["X"], vehicle["Y"])),
            )
            < 1e-6
        )
        assert math.isclose(
            settings["OriginGeopoint"]["Altitude"] - vehicle["Z"],
            geodesy.ORIGIN["altitude_msl_m"] + p["z"] / 100,
            abs_tol=1e-9,
        )
        settings["Vehicles"][name] = vehicle
    return settings


def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-port", type=int, required=True)
    parser.add_argument("--instance-base", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    world = json.loads((ROOT / "world/cosys/world.json").read_text())
    settings = generate(world, args.rpc_port, args.instance_base)
    args.output.write_text(json.dumps(settings, indent=2, allow_nan=False) + "\n")
    print(f"Generated {len(settings['Vehicles'])} ArduCopter vehicles: {args.output}")


if __name__ == "__main__":
    main()
