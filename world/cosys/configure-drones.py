"""Generate pinned stock SimpleFlight settings from inspected, authored geometry."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
world = json.loads((root / "world/cosys/world.json").read_text())
origin = world["playerStartUeCm"]
exposure = {
    "AutoExposureMinBrightness": 13,
    "AutoExposureMaxBrightness": 13,
    "AutoExposureBias": 0,
    "MotionBlurAmount": 0,
    "BloomIntensity": 0,
}
settings = {
    "SettingsVersion": 2.0,
    "SimMode": "Multirotor",
    "ViewMode": "Manual",
    "MoveWorldOrigin": False,
    "EnableRpc": True,
    "LocalHostIp": "127.0.0.1",
    "ApiServerPort": 41451,
    "ClockSpeed": 1,
    "InitialInstanceSegmentation": False,
    "RecordUIVisible": False,
    "LogMessagesVisible": False,
    "SubWindows": [],
    "Recording": {"Enabled": False},
    "OriginGeopoint": {"Latitude": 0, "Longitude": 0, "Altitude": 0},
    "CameraDefaults": {"CaptureSettings": [dict(ImageType=-1, **exposure)]},
    "Vehicles": {},
}
for name, item in world["vehicles"].items():
    p = item["spawnUeCm"]
    settings["Vehicles"][name] = {
        "VehicleType": "SimpleFlight",
        "AutoCreate": True,
        "X": (p["x"] - origin["x"]) / 100,
        "Y": (p["y"] - origin["y"]) / 100,
        "Z": -(p["z"] - origin["z"]) / 100,
        "Yaw": item["yawDegrees"],
        "EnableCollisions": True,
        "EnableCollisionPassthrogh": False,
        "RC": {"RemoteControlID": -1, "AllowAPIAlways": True},
        "Cameras": {
            "front_center": {
                "X": 0.6,
                "Y": 0,
                "Z": 0.05,
                "Pitch": -90,
                "Yaw": 0,
                "Roll": 0,
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
(root / "world/cosys/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
print("Generated four default SimpleFlight vehicles; RPC bound to loopback only.")
