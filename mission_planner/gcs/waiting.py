"""Telemetry conditions for a suspended Wonder turn; no flight or model calls."""

import time


def validate_conditions(conditions, planner, vehicles, inspections):
    names = {v["id"] for v in vehicles}
    for condition in conditions:
        kind = condition["kind"]
        if kind == "inspection":
            assert any(row["id"] == condition["inspection_id"] for row in inspections), (
                "Unknown inspection: " + condition["inspection_id"]
            )
            continue
        assert condition["drone_id"] in names, "Drone telemetry is unavailable"
        if kind == "step":
            active = planner["active"]
            assert active is not None, "A step wait requires an active flight plan"
            assert condition["version"] == active["plan"]["version"], (
                "Wait version must match the active plan"
            )
            plans = [p for p in active["plan"]["drones"] if p["drone_id"] == condition["drone_id"]]
            assert plans, "Drone is not in the active plan"
            assert 0 <= condition["step_index"] < len(plans[0]["steps"]), (
                "Wait step index is outside this drone's plan"
            )
            assert (
                condition["step_index"]
                not in planner["progress"][condition["drone_id"]]["completed_steps"]
            ), (
                f"{condition['drone_id']} step {condition['step_index']} is already complete; "
                "wait only for unfinished steps"
            )
        else:
            assert kind == "landed", "Unknown wait condition"


def ready(conditions, planner, vehicles, inspections):
    results = []
    for condition in conditions:
        kind = condition["kind"]
        if kind == "inspection":
            row = next(row for row in inspections if row["id"] == condition["inspection_id"])
            if "rescue" in row:
                done = row["rescue"]["pending"] or row["status"] in (
                    "candidate",
                    "complete",
                    "failed",
                    "stopped",
                )
            else:
                done = row["status"] != "running"
            if done:
                results.append({"condition": condition, "inspection": row})
            continue
        vehicle = next(v for v in vehicles if v["id"] == condition["drone_id"])
        if (
            vehicle["link_lost"]
            or not vehicle["ekf_healthy"]
            or not 0 <= time.time() * 1000 - vehicle["received_at_ms"] <= 2000
        ):
            return {"reason": "error", "error": vehicle["id"] + ": telemetry or EKF unhealthy"}
        landed = not vehicle["armed"] and vehicle["landed_state"] == 1
        if kind == "landed":
            if landed:
                results.append({"condition": condition, "vehicle": vehicle})
        else:
            active = planner["active"]
            if active is None or active["plan"]["version"] != condition["version"]:
                return {"reason": "error", "error": "The awaited plan version is no longer active"}
            progress = planner["progress"][vehicle["id"]]
            if condition["step_index"] in progress["completed_steps"]:
                result = {"condition": condition, "progress": progress}
                branch = next(
                    branch
                    for branch in active["plan"]["drones"]
                    if branch["drone_id"] == vehicle["id"]
                )
                if "observe" in branch["steps"][condition["step_index"]]:
                    inspection = next(
                        (
                            row
                            for row in inspections
                            if row["drone_id"] == vehicle["id"]
                            and "rescue" in row
                            and row["plan_version"] == condition["version"]
                        ),
                        None,
                    )
                    if inspection is not None:
                        if inspection["status"] == "running" and (
                            inspection["rescue"]["step_index"] == condition["step_index"]
                            or not any(
                                step["step_index"] == condition["step_index"]
                                for step in inspection["rescue"]["completed_steps"]
                            )
                        ):
                            continue
                        result["inspection"] = inspection
                results.append(result)
            elif any(
                row["drone_id"] == vehicle["id"]
                and "rescue" in row
                and row["rescue"]["phase"] in ("reviewing", "tracking", "held", "resuming")
                and row["plan_version"] == condition["version"]
                for row in inspections
            ):
                continue
            elif landed or vehicle["mode"] != "AUTO":
                return {"reason": "error", "error": vehicle["id"] + ": awaited flight step stopped"}
    return {"reason": "condition", "results": results} if results else None
