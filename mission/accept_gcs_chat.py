"""Explicit live Supervised acceptance flight, driven through Wonder and approvals."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from websockets.asyncio.client import connect

from mission.environment import load_env
from mission.tools import CATEGORIES


class Acceptance:
    def __init__(self, socket, output):
        self.socket = socket
        self.output = output
        self.state = None
        self.last_type = None
        self.events = []
        self.approved = set()
        self.approve = True
        self.samples = []
        self.log = (output / "websocket.jsonl").open("x")

    async def send(self, value):
        self.log.write(json.dumps({"sent_at_s": time.time(), "message": value}) + "\n")
        self.log.flush()
        await self.socket.send(json.dumps(value))

    async def read(self, timeout):
        value = json.loads(await asyncio.wait_for(self.socket.recv(), timeout))
        self.last_type = value["type"]
        self.log.write(json.dumps({"received_at_s": time.time(), "message": value}) + "\n")
        self.log.flush()
        if value["type"] == "state":
            self.state = value
            for card in value["approvals"]:
                if card["id"] not in self.approved:
                    self.approved.add(card["id"])
                    await self.send(
                        {"type": "approval", "id": card["id"], "approved": self.approve}
                    )
        elif value["type"] == "event":
            self.events.append(value["event"])
            assert value["event"]["kind"] != "error", value["event"]["text"]
        elif value["type"] == "error":
            raise RuntimeError(value["message"])

    async def wait(self, predicate, timeout, label):
        deadline = time.monotonic() + timeout
        while self.state is None or not predicate():
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"Timed out waiting for {label}"
            await self.read(remaining)
        self.samples.append(
            {"label": label, "at_s": time.time(), "vehicles": self.state["vehicles"]}
        )
        print(label, flush=True)

    def drone(self, name):
        return next(v for v in self.state["vehicles"] if v["id"] == name)

    async def chat(self, text, expected, declined=False):
        index = len(self.events)
        self.approve = not declined
        await self.send({"type": "chat", "text": text})

        def finished():
            fresh = self.events[index:]
            return (
                self.last_type == "state"
                and any(e["kind"] == "operator" and e["text"] == text for e in fresh)
                and any(e["kind"] == "agent" for e in fresh)
                and not self.state["agent_busy"]
            )

        await self.wait(finished, 210, "Chat completed: " + text)
        results = [e for e in self.events[index:] if e["kind"] == "tool_result"]
        calls = [
            e["name"] for e in results if e["name"] in expected or CATEGORIES[e["name"]] == "flight"
        ]
        assert calls == expected, ("Unexpected tool selection", text, calls, expected)
        for item in results:
            if declined and item["name"] in expected:
                assert not item["result"]["ok"] and "operator declined" in item["result"]["error"]
            else:
                assert item["result"]["ok"], item["result"]
        self.approve = True

    async def run(self, case_id):
        await self.send({"type": "connect", "case_id": case_id})
        await self.wait(lambda: len(self.state["vehicles"]) == 4, 120, "Four real autopilots ready")
        assert self.state["mission"]["autonomy"] == "Supervised"
        await self.chat("Read the current rescue briefing and alerts.", ["get_mission"])
        await self.chat("How is the whole fleet doing right now?", ["get_fleet"])
        await self.chat("Give me Drone1's battery, GPS and mode.", ["get_drone"])
        await self.chat("Centre the map on Drone1.", ["show_on_map"])
        for name in ("Drone1", "Drone2", "Drone3", "Drone4"):
            await self.chat(f"Take off {name} to 30 metres above home.", ["take_off"])
            await self.wait(
                lambda: (
                    self.drone(name)["armed"]
                    and self.drone(name)["landed_state"] == 2
                    and abs(self.drone(name)["altitude_above_home_m"] - 30) <= 1
                ),
                90,
                name + " airborne at 30 m",
            )
        await self.chat(
            "Fly Drone1 an AUTO route through north -790 east -2.5, north -700 east -100, then north -500 east -100. All waypoints are 30 metres above home, speed 5 metres per second.",
            ["fly_route"],
        )
        await self.chat(
            "Fly Drone2 an AUTO route through north -790 east 2.5, north -700 east 100, then north -500 east 100. All waypoints are 30 metres above home, speed 5 metres per second.",
            ["fly_route"],
        )
        await self.wait(
            lambda: self.drone("Drone1")["north_m"] > -780, 45, "Drone1 following route"
        )
        await self.chat("Hold Drone1 now.", ["hold"])
        await self.wait(
            lambda: self.drone("Drone1")["ground_speed_mps"] < 0.5, 20, "Drone1 stopped in BRAKE"
        )
        await self.chat(
            "Move Drone1 in GUIDED to north -760 metres, east -20 metres, 30 metres above home at 3 metres per second.",
            ["fly_to"],
        )
        await self.wait(
            lambda: (
                abs(self.drone("Drone1")["north_m"] + 760) <= 1.5
                and abs(self.drone("Drone1")["east_m"] + 20) <= 1.5
                and self.drone("Drone1")["ground_speed_mps"] < 0.5
            ),
            60,
            "Drone1 reached GUIDED destination after BRAKE",
        )
        await self.chat("Hold Drone1 now.", ["hold"])
        await self.chat("Resume Drone1's uploaded mission from its current item.", ["resume"])
        await self.chat("Hold Drone2 now.", ["hold"], declined=True)
        await self.chat("Return Drone1 home.", ["return_home"])
        await self.wait(
            lambda: not self.drone("Drone1")["armed"] and self.drone("Drone1")["landed_state"] == 1,
            180,
            "Drone1 returned and landed",
        )
        for name in ("Drone2", "Drone3", "Drone4"):
            await self.chat(f"Land {name} where it is.", ["land"])
        await self.wait(
            lambda: all(not v["armed"] and v["landed_state"] == 1 for v in self.state["vehicles"]),
            120,
            "All four landed and disarmed",
        )
        await self.chat(
            "Fly Drone1 an AUTO route through north 900 east 0 then north 1000 east 0, all at 30 metres above home and 5 metres per second.",
            [],
        )
        await self.chat("Take off Drone9 to 30 metres above home.", [])
        result = {
            "passed": True,
            "mission_id": self.state["mission"]["id"],
            "supervised": True,
            "approval_decisions": len(self.approved),
            "operator_declines": 1,
            "samples": self.samples,
            "tool_results": [e for e in self.events if e["kind"] == "tool_result"],
        }
        (self.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


async def run(args):
    load_env()
    args.output.mkdir(parents=True, exist_ok=False)
    async with connect(f"ws://127.0.0.1:{args.port}", max_size=2**22) as socket:
        acceptance = Acceptance(socket, args.output)
        try:
            await acceptance.run(args.case)
        finally:
            acceptance.log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
