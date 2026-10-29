"""Approved leads and structure checks, with recipient-owned acknowledgement and reports."""

import asyncio
import copy
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
            "kind": "lead",
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
            "region_cell_count": review["region_cell_count"],
            "region_area_m2": review["region_area_m2"],
            "directions": review["directions"],
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
        return self.receipt(delivery)

    def receipt(self, delivery):
        return {
            "delivery_id": delivery["id"],
            "status": "sent",
            "message": "Sent; awaiting acknowledgement from the receiving page",
            "recipient_url": "/team?"
            + urlencode({"case": self.mission.case["id"], "team": delivery["team_id"]}),
        }

    def structure(self, place_id):
        search = self.mission.search
        assert search.landmarks is not None, "Authored landmark catalogue required"
        assert search.commander.evidence is not None, "Initialize the probability map first"
        matches = [place for place in search.landmarks if place["id"] == place_id]
        assert len(matches) == 1, "Unknown structure id"
        place = matches[0]
        assert place["kind"] == "structure" and place["geometry"]["type"] == "polygon", (
            "A structure check requires an authored structure polygon"
        )
        return copy.deepcopy(place)

    def send_structure(self, team_id, place_id, message):
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", team_id), "Invalid team identity"
        assert isinstance(message, str) and 1 <= len(message.strip()) <= 1000
        place = self.structure(place_id)
        point = place["geometry"]["points"][0]
        north, east = point["x"] / 100, point["y"] / 100
        latitude, longitude = to_geodetic(north, east)
        task = {
            "id": str(uuid.uuid4()),
            "kind": "structure_check",
            "team_id": team_id,
            "message": message,
            "status": "sent",
            "sent_at_ms": time.time() * 1000,
            "acknowledged_at_ms": None,
            "place_id": place["id"],
            "place_name": place["name"],
            "geometry": place["geometry"],
            "mass_at_dispatch": self.mission.search.structure_mass(place["id"], place["geometry"]),
            "north_m": north,
            "east_m": east,
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "directions": self.mission.search.directions(north, east),
            "navigation_url": "https://www.google.com/maps/dir/?"
            + urlencode(
                {"api": 1, "destination": f"{latitude},{longitude}", "travelmode": "walking"}
            ),
            "negative_report": None,
            "negative_confirmation": None,
        }
        self.deliveries[task["id"]] = task
        self.save()
        return self.receipt(task)

    def acknowledge(self, delivery_id, team_id):
        delivery = self.deliveries[delivery_id]
        assert delivery["team_id"] == team_id, "Delivery belongs to another team"
        assert delivery["status"] == "sent", "Delivery already acknowledged"
        delivery.update(status="acknowledged", acknowledged_at_ms=time.time() * 1000)
        self.save()
        self.mission.event(
            "team_acknowledgement",
            "Ground team acknowledged delivery",
            result=delivery,
            source="team",
        )

    def report_negative(self, delivery_id, team_id, notes):
        task = self.deliveries[delivery_id]
        assert task["team_id"] == team_id, "Delivery belongs to another team"
        assert task["kind"] == "structure_check", "Negative reports require a structure task"
        assert task["status"] == "acknowledged", "Acknowledge this task before its single report"
        assert isinstance(notes, str) and 1 <= len(notes.strip()) <= 1000
        task.update(
            status="negative_reported",
            negative_report={"reported_at_ms": time.time() * 1000, "notes": notes},
        )
        self.save()
        self.mission.event(
            "team_report",
            "Structure checked negative; awaiting operator confirmation",
            result=task,
            source="team",
        )

    def confirm_negative(self, delivery_id, reason):
        task = self.deliveries[delivery_id]
        assert task["kind"] == "structure_check" and task["status"] == "negative_reported", (
            "A receiving-team negative report awaiting confirmation is required"
        )
        assert isinstance(reason, str) and 1 <= len(reason.strip()) <= 1000
        place = self.structure(task["place_id"])
        assert place["geometry"] == task["geometry"], "Structure geometry changed since dispatch"
        effect = self.mission.search.confirm_structure_negative(
            delivery_id, task["place_id"], task["geometry"]
        )
        task.update(
            status="negative_confirmed",
            negative_confirmation={
                "confirmed_at_ms": time.time() * 1000,
                "reason": reason,
                "belief_update": effect,
            },
        )
        self.save()
        self.mission.event(
            "operator",
            "Structure negative report confirmed by operator",
            result=task,
            source="operator",
        )
        return {"delivery_id": delivery_id, "status": task["status"], **effect}

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
                if message["type"] == "acknowledge":
                    assert set(message) == {"type", "delivery_id"}
                    self.acknowledge(message["delivery_id"], team)
                else:
                    assert message["type"] == "report_negative" and set(message) == {
                        "type",
                        "delivery_id",
                        "notes",
                    }
                    self.report_negative(message["delivery_id"], team, message["notes"])
        finally:
            task.cancel()
            await asyncio.wait([task])
            if not task.cancelled():
                task.result()
