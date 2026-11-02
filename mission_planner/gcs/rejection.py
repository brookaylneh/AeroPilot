"""One structured reason a plan cannot fly; prose counts steps from one, fields from zero."""

RULES = (
    "schema",
    "version",
    "participants",
    "aircraft_state",
    "fence",
    "altitude",
    "terrain_clearance",
    "obstacle_clearance",
    "separation",
    "battery",
    "aftermath",
    "observation_area",
    "spacing_overlap",
    "coverage",
    "transit_ceiling",
    "lane_blocked",
    "band_behind_pads",
    "disconnected_sector",
    "endurance",
    "no_distance_table",
    "reference",
    "compile",
)


class PlanRejected(AssertionError):
    def __init__(
        self,
        rule,
        message,
        *,
        drone_id=None,
        step_index=None,
        step_kind=None,
        detail=None,
        conflict=None,
    ):
        assert rule in RULES, f"Unknown rejection rule: {rule}"
        assert isinstance(message, str) and message.strip(), "Rejection requires a message"
        assert step_index is None or (type(step_index) is int and step_index >= 0)
        self.rejection = {
            "rule": rule,
            "version": None,
            "drone_id": drone_id,
            "step_index": step_index,
            "step_kind": step_kind,
            "message": message,
            "detail": detail,
            "conflict": conflict,
        }
        super().__init__(message)

    def __str__(self):
        return self.rejection["message"]

    def at(self, drone_id, step_index, step_kind):
        """Locate a rejection raised inside one plan step; an already located one keeps its place."""
        rejection = self.rejection
        if rejection["drone_id"] is None:
            rejection.update(drone_id=drone_id, step_index=step_index, step_kind=step_kind)
            rejection["message"] = (
                f"{drone_id} step {step_index + 1} ({step_kind}): {rejection['message']}"
            )
            self.args = (rejection["message"],)
        return self


def conflict_text(conflict):
    def leg(prefix):
        name = conflict[prefix + "drone_id"]
        index = conflict[prefix + "step_index"]
        if index is not None:
            name += f" step {index + 1} ({conflict[prefix + 'step_kind']})"
        return name

    def position(point):
        return (
            f"N {point['north_m']:.3f}, E {point['east_m']:.3f}, "
            f"{point['altitude_above_home_m']:.3f} m above home"
        )

    return (
        f"{leg('')} conflicts with {leg('other_')}; "
        f"minimum separation {conflict['minimum_m']:g} m, "
        f"measured {conflict['distance_m']:.6f} m. "
        + "; ".join(
            f"{leg(prefix)}: "
            + " → ".join(position(point) for point in conflict[prefix + "segment"])
            for prefix in ("", "other_")
        )
        + ". Entire routes reserve space regardless of timing."
    )


def separation(conflict):
    return PlanRejected(
        "separation",
        conflict_text(conflict),
        drone_id=conflict["drone_id"],
        step_index=conflict["step_index"],
        step_kind=conflict["step_kind"],
        conflict=conflict,
    )
