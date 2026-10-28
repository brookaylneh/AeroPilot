"""Persisted rescue leads, acknowledged only by a receiving team connection."""

import asyncio
import json
import re
import time
import uuid
from urllib.parse import urlencode

from mission_planner.gcs.geodesy import to_geodetic


class GroundTeam:
    def __init__(self, mission):
        self.mission = mission
        self.deliveries = {}

    def save(self):
        self.mission.directory.joinpath("deliveries.json").write_text(
            json.dumps(self.deliveries, indent=2, allow_nan=False) + "\n"
        )
        self.mission.search.changed()

    def send(self, team_id, detection_id, message):
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", team_id), "Invalid team identity"
        assert isinstance(message, str) and 1 <= len(message.strip()) <= 1000
        review = self.mission.search.review_detection(detection_id)
        assert review["status"] == "confirmed_lead", "An operator must confirm this lead first"
        detection = next(d for d in review["members"] if d["id"] == detection_id)
        ground = review["localization"]["ground"]
        latitude, longitude = to_geodetic(ground["north_m"], ground["east_m"])
        delivery = {
            "id": str(uuid.uuid4()),
            "team_id": team_id,
            "detection_id": detection_id,
            "message": message,
            "status": "sent",
            "sent_at_ms": time.time() * 1000,
            "acknowledged_at_ms": None,
            "frame_url": detection["frame_ref"],
            "crop_url": detection["crop_ref"],
            "timestamp_ms": detection["timestamp_ns"] / 1e6,
            "north_m": ground["north_m"],
            "east_m": ground["east_m"],
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "sam_confidence": detection["sam_confidence"],
            "glimmer_yes": detection["glimmer_yes"],
            "glimmer_outside_mass": detection["outside_mass"],
            "posterior_mass": review["posterior_mass"],
            "calibrated": detection["calibrated"],
            "source": detection["source"],
            "drone_id": detection["drone_id"],
            "replay_lane": detection["replay_lane"],
            "navigation_url": "https://www.google.com/maps/dir/?"
            + urlencode(
                {"api": 1, "destination": f"{latitude},{longitude}", "travelmode": "walking"}
            ),
        }
        self.deliveries[delivery["id"]] = delivery
        self.save()
        return {
            "delivery_id": delivery["id"],
            "status": "sent",
            "message": "Sent; awaiting acknowledgement from the receiving page",
            "recipient_url": "/team?"
            + urlencode({"case": self.mission.case["id"], "team": team_id}),
        }

    def acknowledge(self, delivery_id, team_id):
        delivery = self.deliveries[delivery_id]
        assert delivery["team_id"] == team_id, "Delivery belongs to another team"
        assert delivery["status"] == "sent", "Delivery already acknowledged"
        delivery.update(status="acknowledged", acknowledged_at_ms=time.time() * 1000)
        self.save()
        self.mission.event(
            "team_acknowledgement", "Ground team acknowledged lead", result=delivery, source="team"
        )

    async def connection(self, websocket):
        first = json.loads(await asyncio.wait_for(websocket.recv(), 10))
        assert set(first) == {"type", "case_id", "team_id"} and first["type"] == "connect"
        assert first["case_id"].lower() == self.mission.case["id"].lower()
        team = first["team_id"]
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", team)

        async def publish():
            previous = None
            while True:
                payload = json.dumps(
                    {
                        "type": "team",
                        "team_id": team,
                        "deliveries": [d for d in self.deliveries.values() if d["team_id"] == team],
                    },
                    allow_nan=False,
                )
                if payload != previous:
                    await websocket.send(payload)
                    previous = payload
                await asyncio.sleep(0.2)

        task = asyncio.create_task(publish())
        try:
            async for raw in websocket:
                message = json.loads(raw)
                assert set(message) == {"type", "delivery_id"} and message["type"] == "acknowledge"
                self.acknowledge(message["delivery_id"], team)
        finally:
            task.cancel()
            await asyncio.wait([task])
            if not task.cancelled():
                task.result()
