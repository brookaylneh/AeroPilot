"""Small local command adapter used by the website; no unrestricted RPC proxy."""

import argparse
import fcntl
import json

from client import (
    DATA,
    NAMES,
    WORLD,
    all_states,
    capture,
    concurrent,
    configure_capture,
    connect,
    home,
    land,
    move,
    native,
    set_view,
    state,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "capture", "flight-test", "return"])
    parser.add_argument("--drone", choices=NAMES, default="Drone1")
    parser.add_argument("--height", type=int, choices=[1080, 2160, 4320], default=2160)
    parser.add_argument("--view", choices=["down", "oblique"], default="down")
    args = parser.parse_args()
    if args.action == "status":
        c = connect()
        assert c.ping()
        r = native()
        print(
            json.dumps(
                {
                    "ready": True,
                    "vehicles": list(all_states().values()),
                    "timing": r["timing"],
                    "captureHeight": r["vehicles"][0]["height"],
                    "world": WORLD,
                }
            )
        )
        return
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / "controller.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "capture":
            configure_capture(args.height)
            c = connect()
            set_view(c, args.drone, -90 if args.view == "down" else -45)
            capture(args.drone, DATA / "preview-warmup", "warmup")
            result = capture(args.drone, DATA / "observations", "latest")
            print(json.dumps(result))
        elif args.action == "flight-test":
            from verify import run_flight_tests

            run_flight_tests(DATA / "flight-test")
        else:

            def back(name):
                c = connect()
                s = state(c, name)
                if s["landed"]:
                    return s
                c.cancelLastTask(vehicle_name=name)
                h = max(s["positionWorldM"]["z"], home(name)["z"] + 20 + NAMES.index(name) * 6)
                move(name, {**s["positionWorldM"], "z": h}, 4)
                move(name, {**home(name), "z": h}, 5)
                return land(name)

            print(json.dumps(concurrent(back)))


if __name__ == "__main__":
    main()
