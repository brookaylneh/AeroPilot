"""Generate ArduCopter settings from the authored geometry and mission geodesy."""

import argparse
import json
import math
from pathlib import Path

from mission_planner.environment import load_env
from mission_planner.gcs import geodesy

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
        "CameraDefaults": {"CaptureSettings": [dict(ImageType=-1, **exposure)]},
        "Vehicles": {},
    }
    homes = geodesy.homes()
    for index, name in enumerate(geodesy.NAMES):
        item = world["vehicles"][name]
        p = item["spawnUeCm"]
        assert set(p) == {"x", "y", "z"}
        assert all(math.isfinite(p[axis]) for axis in "xyz")
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
                }
            },
        }
        home = homes[name]
        assert (vehicle["X"], vehicle["Y"]) == (home["north_m"], home["east_m"])
        assert (
            math.dist(
                (vehicle["X"], vehicle["Y"]),
                geodesy.to_local(home["latitude_deg"], home["longitude_deg"]),
            )
            < 1e-6
        )
        assert settings["OriginGeopoint"]["Altitude"] - vehicle["Z"] == home["altitude_msl_m"]
        assert vehicle["Yaw"] == home["heading_deg"]
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
