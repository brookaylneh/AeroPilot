"""Explicit SITL models, isolated listeners and the verified safety baseline."""

import json
import os
import subprocess
import time
from pathlib import Path

from mission_planner.gcs.battery import CAPACITY_AH
from mission_planner.gcs.fences import BOUNDARY_CLEARANCE_M
from mission_planner.gcs.geodesy import NAMES, ROOT, RTL_ALTITUDE_M, homes

SAFETY_PARAMETERS = {
    "ARMING_SKIPCHK": 0,
    "FS_THR_ENABLE": 1,
    "FS_CRASH_CHECK": 1,
    "DISARM_DELAY": 10,
    "RTL_ALT_M": RTL_ALTITUDE_M,
    "RTL_ALT_FINAL_M": 0,
    "RTL_CLIMB_MIN_M": 0,
    "RTL_CONE_SLOPE": 0,
    "RTL_ALT_TYPE": 0,
    "FENCE_ALT_MAX": 100,
    "FENCE_MARGIN": 2,
    "FENCE_ACTION": 1,
    "OA_TYPE": 2,
    "OA_MARGIN_MAX": BOUNDARY_CLEARANCE_M,
    "OA_OPTIONS": 4,
    "WP_RADIUS_M": 1,
    "BATT_MONITOR": 4,
    "BATT_LOW_VOLT": 10.5,
    "BATT_LOW_TIMER": 10,
    "BATT_FS_LOW_ACT": 2,
}
AIRSIM_PARAMETERS = {
    "SCHED_LOOP_RATE": 184,
    "ATC_ACC_Y_MAX": 20,
    "BATT_CAPACITY": CAPACITY_AH * 1000,
    "SIM_BATT_CAP_AH": CAPACITY_AH,
    "SIM_BATT_VOLTAGE": 12.6,
}


def check_ports(ports):
    assert len(ports) == len(set(ports)), f"Repeated listener ports: {ports}"
    assert all(isinstance(port, int) and 0 < port < 65536 for port in ports), ports
    listeners = subprocess.run(
        ["ss", "-H", "-lntup"], check=True, capture_output=True, text=True, timeout=5
    ).stdout
    for line in listeners.splitlines():
        local = line.split()[4]
        assert int(local.rsplit(":", 1)[1]) not in ports, f"Listener collision: {line}"
    return {"ports": ports, "checked_at_ms": time.time() * 1000, "listeners": listeners}


def launches(directory):
    binary = Path(os.environ["SNR_SITL_BINARY"]).resolve()
    assert binary.is_file() and os.access(binary, os.X_OK), f"Build required: {binary}"
    model = os.environ["SNR_SITL_MODEL"]
    assert model in ("airsim-copter", "quad"), f"Unsupported SITL model: {model}"
    instance_base = int(os.environ["SNR_SITL_INSTANCE_BASE"])
    assert 0 <= instance_base <= 252, instance_base
    port_step = int(os.environ["SNR_SITL_PORT_STEP"])
    assert port_step == 10, "Pinned SITL assigns ten ports per instance"
    mavlink_base = int(os.environ["SNR_MAVLINK_BASE_PORT"])
    rc_base = int(os.environ["SNR_SITL_RC_BASE_PORT"])
    defaults = [ROOT / ".local/vendor/ardupilot/Tools/autotest/default_params/copter.parm"]
    settings = json.loads(Path(os.environ["SNR_COSYS_SETTINGS"]).read_text())
    assert set(settings["Vehicles"]) == set(NAMES)
    if model == "airsim-copter":
        assert settings["ApiServerPort"] == int(os.environ["SNR_COSYS_PORT"])
        defaults.append(
            ROOT / ".local/vendor/ardupilot/Tools/autotest/default_params/airsim-quadX.parm"
        )
    safety = Path(directory) / "safety.parm"
    parameters = SAFETY_PARAMETERS | (AIRSIM_PARAMETERS if model == "airsim-copter" else {})
    with safety.open("x") as stream:
        stream.write("".join(f"{key} {value}\n" for key, value in parameters.items()))
    defaults.append(safety)
    assert all(path.is_file() for path in defaults), defaults
    spawn_settings = json.loads((ROOT / "world/cosys/settings.json").read_text())
    assert settings["OriginGeopoint"] == spawn_settings["OriginGeopoint"], "OriginGeopoint mismatch"
    assert settings["LocalHostIp"] == "127.0.0.1"
    result = []
    for index, (name, home) in enumerate(homes().items()):
        instance = instance_base + index
        mavlink_port = mavlink_base + index
        rc_port = rc_base + index * port_step
        ports = [mavlink_port, rc_port]
        vehicle = settings["Vehicles"][name]
        for key in ("X", "Y", "Z", "Yaw"):
            assert vehicle[key] == spawn_settings["Vehicles"][name][key], (name, key)
        if model == "airsim-copter":
            assert vehicle["VehicleType"] == "ArduCopter", name
            assert vehicle["LocalHostIp"] == "127.0.0.1", name
            assert vehicle["UdpIp"] == "127.0.0.1", name
            assert vehicle["UdpPort"] == 9003 + instance * port_step, name
            assert vehicle["ControlPortLocal"] == 9002 + instance * port_step, name
            ports.append(vehicle["UdpPort"])
        result.append(
            {
                "name": name,
                "home": home,
                "mavlink_port": mavlink_port,
                "ports": ports,
                "expected_parameters": SAFETY_PARAMETERS | AIRSIM_PARAMETERS
                if model == "airsim-copter"
                else SAFETY_PARAMETERS,
                "arguments": [
                    str(binary),
                    "--model",
                    model,
                    "--instance",
                    str(instance),
                    "--sysid",
                    str(index + 1),
                    "--home",
                    f"{home['latitude_deg']},{home['longitude_deg']},{home['altitude_msl_m']},{home['heading_deg']}",
                    "--sim-address",
                    "127.0.0.1",
                    "--rc-in-port",
                    str(rc_port),
                    "--serial0",
                    "none",
                    "--serial1",
                    f"udpclient:127.0.0.1:{mavlink_port}",
                    "--serial2",
                    "none",
                    "--defaults",
                    ",".join(str(path) for path in defaults),
                ],
            }
        )
    return result
