import fcntl
import math
import os
import time

from mission.environment import ROOT, load_env


def run():
    load_env()
    os.environ.update(SNR_DRONE_DATA_DIR=str(ROOT / "data/four-drones"), SNR_COSYS_PORT="41451")
    from simulation.phase2a import client as sim

    c, name, frames = sim.connect(), "Drone1", []
    camera = ("front_center", sim.airsim.ImageType.Scene)
    actors = c.simListSceneObjects(".*BP_Person_.*")
    assert len(actors) == 1 and "_C_" in actors[0], actors
    pattern = actors[0].split("_C_", 1)[0] + "*"
    p, origin = c.simGetObjectPose(actors[0]).position, sim.native()["globalNedOriginUeCm"]
    target = dict(x=origin["x"] / 100 + p.x_val, y=origin["y"] / 100 + p.y_val)
    heading = math.radians(sim.WORLD["vehicles"][name]["yawDegrees"])
    with open(sim.DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sim.configure_capture(2160)
        try:
            if sim.state(c, name)["landed"]:
                sim.takeoff(name)
            for tag, dx in [("target", 0), ("empty", 160)]:
                x, y = target["x"] + dx, target["y"]
                ground = sim.terrain_height(x, y)
                point = dict(x=x, y=y, z=ground + 30 + sim.CAMERA_DOWN)
                for axis, direction in zip("xy", (math.cos(heading), math.sin(heading))):
                    point[axis] -= sim.CAMERA_FORWARD * direction
                sim.move(name, {**sim.home(name), "z": 65}, 8)
                sim.move(name, {**point, "z": 65}, 8)
                sim.move(name, point, 4)
                sim.set_view(c, name, -90)
                time.sleep(1.5)
                c.simClearDetectionMeshNames(*camera, vehicle_name=name)
                c.simAddDetectionFilterMeshName(*camera, pattern, vehicle_name=name)
                c.simSetDetectionFilterRadius(*camera, 20000, vehicle_name=name)
                frame = sim.capture(name, ROOT / "evidence/models", tag, freeze=True)
                boxes = c.simGetDetections(*camera, vehicle_name=name)
                frame["evaluationOnlyBoxes"] = [
                    [v for p in (d.box2D.min, d.box2D.max) for v in (p.x_val, p.y_val)]
                    for d in boxes
                ]
                frame["actualCameraAGLM"] = frame["cameraPositionWorldM"]["z"] - ground
                assert (frame["width"], frame["height"]) == (3840, 2160)
                frames.append(frame)
                sim.write_json(ROOT / "measurements/smoke-frames.json", frames)
        finally:
            c.simClearDetectionMeshNames(*camera, vehicle_name=name)
            sim.move(name, {**sim.state(c, name)["positionWorldM"], "z": 65}, 6)
            sim.move(name, {**sim.home(name), "z": 65}, 8)
            sim.land(name)


if __name__ == "__main__":
    run()
