"""Camera-only guidance from the operator's live CSRT tracker."""

import json
import math
import time


def held(vehicle):
    return (
        vehicle["armed"]
        and vehicle["landed_state"] == 2
        and vehicle["mode"] in ("BRAKE", "LOITER")
        and not vehicle["link_lost"]
        and vehicle["ekf_healthy"]
        and vehicle["camera_available"]
        and 0 <= time.time() * 1000 - vehicle["received_at_ms"] <= 2000
    )


class VisualFollow:
    def __init__(self, inspections):
        self.inspections = inspections
        self.owners = {}

    def cancel(self, drone_id):
        if drone_id in self.owners:
            self.inspections.cameras[drone_id].cancel_follow()
            del self.owners[drone_id]

    def disconnect(self, socket):
        for name, owner in list(self.owners.items()):
            if owner["socket"] is socket:
                self.cancel(name)

    async def update(self, message, socket):
        received = time.monotonic()
        inspections = self.inspections
        mission = inspections.mission
        sequence = message["sequence"]
        assert type(sequence) is int and sequence > 0, "Invalid camera guidance sequence"
        assert type(message["track_id"]) is int and message["track_id"] > 0
        assert type(message["camera_revision"]) is int and message["camera_revision"] >= 0
        age = message["frame_age_ms"]
        assert type(age) in (float, int) and math.isfinite(age) and age >= 0
        box = message["box"]
        if box is not None:
            assert isinstance(box, list) and len(box) == 4
            assert all(type(v) in (int, float) and math.isfinite(v) for v in box)
            assert 0 <= box[0] < box[2] <= 960 and 0 <= box[1] < box[3] <= 540
        row = inspections.rows[message["inspection_id"]]
        name, rescue = row["drone_id"], row["rescue"]
        context = (row["id"], message["review_id"], message["track_id"], message["camera_revision"])
        result = {"type": "camera_follow", "sequence": sequence, "status": "stopped"}
        if box is None:
            if (
                name in self.owners
                and self.owners[name]["socket"] is socket
                and self.owners[name]["context"] == context
            ):
                self.cancel(name)
            return result
        vehicle = next(v for v in mission.vehicles() if v["id"] == name)
        active = row["status"] in ("running", "candidate") and rescue["phase"] in (
            "reviewing",
            "tracking",
            "held",
        )
        budget_hold = row["status"] == "failed" and rescue["visual_hold"]
        if (
            not held(vehicle)
            or not (active or budget_hold)
            or mission.plan_active is None
            or mission.plan_active["plan"]["version"] != row["plan_version"]
            or rescue["review_id"] != message["review_id"]
            or rescue["selected_track_id"] != message["track_id"]
            or row["camera_changing"]
            or row["camera_view"]["revision"] != message["camera_revision"]
        ):
            if name in self.owners and self.owners[name]["socket"] is socket:
                self.cancel(name)
            return result
        if age > 500:
            return {**result, "status": "stale"}
        camera = inspections.cameras[name]
        if name in self.owners:
            owner = self.owners[name]
            assert owner["socket"] is socket, "Another viewer owns this drone's camera"
            assert sequence > owner["sequence"], "Out-of-order camera guidance"
            if owner["context"] != context:
                self.cancel(name)
        if name not in self.owners:
            self.owners[name] = {
                "socket": socket,
                "context": context,
                "sequence": sequence,
                "generation": camera.begin_follow(),
            }
        owner = self.owners[name]
        owner["sequence"] = sequence
        moved = await camera.follow(box, owner["generation"], received + (500 - age) / 1000)
        with (mission.directory / "camera-follow.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "timestamp_ms": time.time() * 1000,
                        "drone_id": name,
                        "inspection_id": row["id"],
                        "track_id": message["track_id"],
                        "camera_revision": message["camera_revision"],
                        "sequence": sequence,
                        "frame_age_ms": age,
                        "box": box,
                        **moved,
                    },
                    allow_nan=False,
                )
                + "\n"
            )
        return {**result, "status": moved["status"], "motion": moved}
