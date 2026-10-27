"""Private evaluator: acquire target and empty controls with stock flight helpers."""
import fcntl
import json
import math
import sys
import time
from mission.environment import ROOT, os

os.environ.update(SNR_DRONE_DATA_DIR=str(ROOT / 'data/four-drones'), SNR_COSYS_PORT='41451')
sys.path.insert(0, str(ROOT / 'simulation/phase2a'))
from client import (CAMERA_DOWN, CAMERA_FORWARD, DATA, WORLD, capture, configure_capture,
                    connect, home, land, move, native, set_view, state, takeoff, terrain_height)
import cosysairsim as airsim
def run():
    c, name, out = connect(), 'Drone1', ROOT / 'evidence/models'
    actors = c.simListSceneObjects('.*BP_Person_.*')
    assert len(actors) == 1, actors
    p, origin = c.simGetObjectPose(actors[0]).position, native()['globalNedOriginUeCm']
    target = dict(x=origin['x']/100+p.x_val, y=origin['y']/100+p.y_val)
    heading = math.radians(WORLD['vehicles'][name]['yawDegrees'])
    configure_capture(2160)
    frames = []
    try:
        if state(c, name)['landed']:
            takeoff(name)
        move(name, {**home(name), 'z': 65}, 8)
        for tag, xy in [('target', target), ('empty', dict(x=target['x']+160, y=target['y']))]:
            ground = terrain_height(xy['x'], xy['y'])
            point = dict(x=xy['x']-CAMERA_FORWARD*math.cos(heading),
                         y=xy['y']-CAMERA_FORWARD*math.sin(heading), z=ground+30+CAMERA_DOWN)
            move(name, {**point, 'z': 65}, 8)
            move(name, point, 4)
            set_view(c, name, -90)
            time.sleep(1.5)
            capture(name, out / 'warmup', tag)
            c.simClearDetectionMeshNames('front_center', airsim.ImageType.Scene, vehicle_name=name)
            c.simAddDetectionFilterMeshName('front_center', airsim.ImageType.Scene, actors[0], vehicle_name=name)
            c.simSetDetectionFilterRadius('front_center', airsim.ImageType.Scene, 20000, vehicle_name=name)
            c.simPause(True)
            try:
                frame = capture(name, out, tag)
                boxes = c.simGetDetections('front_center', airsim.ImageType.Scene, vehicle_name=name)
                frame.update(physicsPausedForCapture=True, control=tag, groundWorldM=ground, actualCameraAGLM=frame['cameraPositionWorldM']['z']-ground,
                             evaluationOnlyBoxes=[[d.box2D.min.x_val,d.box2D.min.y_val,d.box2D.max.x_val,d.box2D.max.y_val] for d in boxes])
                assert (frame['width'], frame['height']) == (3840, 2160)
                frames.append(frame)
                (ROOT / 'measurements/smoke-frames.json').write_text(json.dumps(frames, indent=2)+'\n')
                print(tag, 'AGL', frame['actualCameraAGLM'], 'boxes', frame['evaluationOnlyBoxes'], flush=True)
            finally:
                c.simPause(False)
    finally:
        c.simClearDetectionMeshNames('front_center', airsim.ImageType.Scene, vehicle_name=name)
        move(name, {**state(c, name)['positionWorldM'], 'z': 65}, 6)
        move(name, {**home(name), 'z': 65}, 8)
        land(name)

if __name__ == '__main__':
    with open(DATA / 'controller.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
