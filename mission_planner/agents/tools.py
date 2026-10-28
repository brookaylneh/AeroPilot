"""Implemented mission tools; flight execution belongs to MAVLink."""

from jsonschema import Draft202012Validator

from mission_planner.gcs.geodesy import (
    BOUND_M,
    LAUNCH_HALF_WIDTH_M,
    LAUNCH_SOUTH_M,
    MAX_ALTITUDE_M,
    MAX_MAP_RADIUS_M,
    MAX_SPEED_MPS,
    MIN_ALTITUDE_M,
    MIN_MAP_RADIUS_M,
    MIN_SPEED_MPS,
    NAMES,
)
from mission_planner.search.tools import (
    DEFINITIONS as SEARCH_DEFINITIONS,
)
from mission_planner.search.tools import (
    INSTRUCTIONS as SEARCH_INSTRUCTIONS,
)

DRONE = {"type": "string", "enum": list(NAMES)}
POSITION = {"type": "number", "minimum": -BOUND_M, "maximum": BOUND_M}
NORTH = {"type": "number", "minimum": LAUNCH_SOUTH_M, "maximum": BOUND_M}
ALTITUDE = {"type": "number", "minimum": MIN_ALTITUDE_M, "maximum": MAX_ALTITUDE_M}
SPEED = {"type": "number", "minimum": MIN_SPEED_MPS, "maximum": MAX_SPEED_MPS}


def parameters(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


POINT = parameters({"north_m": NORTH, "east_m": POSITION, "altitude_above_home_m": ALTITUDE})
DEFINITIONS = [
    (
        "get_mission",
        "read",
        "Use to read the rescue briefing, mission clock, autonomy, fence and alerts. Requires an open mission.",
        {},
    ),
    (
        "get_fleet",
        "read",
        "Use for compact live status of all four drones, including link health and mission progress. Requires MAVLink telemetry.",
        {},
    ),
    (
        "get_drone",
        "read",
        "Use for detailed live state of one known drone. Requires Drone1, Drone2, Drone3 or Drone4.",
        {"drone_id": DRONE},
    ),
    (
        "show_on_map",
        "view",
        "Use to focus the operator map on a drone, point or circular area. Include only the fields for the selected target; points use metres north/east of search centre.",
        {
            "target": {"type": "string", "enum": ["drone", "point", "area"]},
            "drone_id": DRONE,
            "north_m": NORTH,
            "east_m": POSITION,
            "radius_m": {
                "type": "number",
                "minimum": MIN_MAP_RADIUS_M,
                "maximum": MAX_MAP_RADIUS_M,
            },
        },
    ),
    (
        "take_off",
        "flight",
        "Use to arm and take off to 10–100 m above home. Requires healthy landed drone inside its read-back fence; service verifies all arming checks.",
        {"drone_id": DRONE, "altitude_above_home_m": ALTITUDE},
    ),
    (
        "fly_route",
        "flight",
        "Use to upload ordered waypoints, read the mission back, and start AUTO. Requires healthy armed airborne drone, inside-fence route, 10–100 m altitude and route separation.",
        {
            "drone_id": DRONE,
            "waypoints": {"type": "array", "items": POINT, "minItems": 1, "maxItems": 50},
            "speed_mps": SPEED,
        },
    ),
    (
        "fly_to",
        "flight",
        "Use for one destination in GUIDED. Requires healthy armed airborne drone, inside-fence destination, 10–100 m altitude and route separation.",
        {"drone_id": DRONE, **POINT["properties"], "speed_mps": SPEED},
    ),
    (
        "hold",
        "flight",
        "Use to brake and hold position in BRAKE. Requires healthy armed airborne drone inside its read-back fence.",
        {"drone_id": DRONE},
    ),
    (
        "resume",
        "flight",
        "Use to resume the current uploaded mission item in AUTO. Requires healthy armed airborne drone and a valid separated read-back route.",
        {"drone_id": DRONE},
    ),
    (
        "return_home",
        "flight",
        "Use to return to the autopilot home in RTL. Requires healthy armed airborne drone and a home and path inside the fence.",
        {"drone_id": DRONE},
    ),
    (
        "land",
        "flight",
        "Use to descend and land at the current position in LAND. Requires healthy armed airborne drone inside the read-back fence.",
        {"drone_id": DRONE},
    ),
]
DEFINITIONS.extend(SEARCH_DEFINITIONS)
TOOLS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters(properties),
        "strict": True,
    }
    for name, _, description, properties in DEFINITIONS
]
map_tool = next(tool for tool in TOOLS if tool["name"] == "show_on_map")
map_tool["strict"] = False
map_tool["parameters"]["required"] = ["target"]
CATEGORIES = {name: category for name, category, _, _ in DEFINITIONS}
SCHEMAS = {tool["name"]: Draft202012Validator(tool["parameters"]) for tool in TOOLS}
assert len(SCHEMAS) == len(TOOLS)
AGENT_INSTRUCTIONS = f"""You are Wonder, the search-and-rescue commander's mission assistant.
Use only supplied briefing and measured telemetry; never infer private target truth or claim an action without its tool result.
Use the registered tools for live information and requested actions. When all arguments are supplied, call the requested tool directly: the service validates safety and manages operator approval.
Only Drone1, Drone2, Drone3 and Drone4 exist. Reject unknown drone identities without substituting another drone.
Positions are metres north/east of the search centre. The search square is ±{BOUND_M:g} m on each axis. An authorized launch corridor extends south to north {LAUNCH_SOUTH_M:g} m only within east ±{LAUNCH_HALF_WIDTH_M:g} m; flight altitude is {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m above home and speed is {MIN_SPEED_MPS:g}–{MAX_SPEED_MPS:g} m/s. Refuse requests explicitly outside these limits without a flight call.
Supervised flight actions require an approval card; never imply approval yourself. An operator decline is final for that request.
Never retry a failed tool, invent a replacement route, or disable safety checks. Report the precise error and await a new operator instruction.
Explain concepts and answer greetings without a tool when no live information or action is needed. Ask for missing flight arguments; do not guess them.
{SEARCH_INSTRUCTIONS}
Operator actions in the supplied event context have the same authority as chat instructions. Keep replies short, concrete and operational.
"""


def argument_error(error):
    path = list(error.path)
    field = str(path[-1]) if path else "arguments"
    label = f"Waypoint {path[1] + 1}" if path and path[0] == "waypoints" else "Destination"
    if error.validator in ("minimum", "maximum"):
        value, limit = error.instance, error.validator_value
        if field in ("north_m", "east_m"):
            direction = {
                "north_m": "north" if value >= 0 else "south",
                "east_m": "east" if value >= 0 else "west",
            }[field]
            return (
                f"{label} is {abs(value):g} m {direction}; the fence limit is {abs(limit):.1f} m."
            )
        if field == "altitude_above_home_m":
            return f"Requested altitude is {value:g} m above home; use {MIN_ALTITUDE_M:g}–{MAX_ALTITUDE_M:g} m."
        if field == "speed_mps":
            return f"Requested speed is {value:g} m/s; use {MIN_SPEED_MPS:g}–{MAX_SPEED_MPS:g} m/s."
        if field == "radius_m":
            return (
                f"Requested radius is {value:g} m; use {MIN_MAP_RADIUS_M:g}–{MAX_MAP_RADIUS_M:g} m."
            )
    if error.validator == "required":
        missing = [
            key.replace("_", " ") for key in error.validator_value if key not in error.instance
        ]
        return "Required values missing: " + ", ".join(missing) + "."
    if error.validator == "additionalProperties":
        extra = sorted(set(error.instance) - set(error.schema["properties"]))
        return "Unrecognized arguments: " + ", ".join(extra) + "."
    return f"Invalid {field.replace('_', ' ')}: {error.message}."


def validate_arguments(name, arguments):
    assert name in SCHEMAS, f"Unknown tool: {name}"
    assert isinstance(arguments, dict), "Tool arguments must be an object"
    if "drone_id" in arguments and arguments["drone_id"] is not None:
        assert arguments["drone_id"] in NAMES, f"Unknown drone: {arguments['drone_id']}"
    errors = list(SCHEMAS[name].iter_errors(arguments))
    assert not errors, " ".join(argument_error(error) for error in errors)
    points = []
    if name == "fly_route":
        points = arguments["waypoints"]
    elif name == "fly_to":
        points = [arguments]
    for point in points:
        assert point["north_m"] >= -BOUND_M or abs(point["east_m"]) <= LAUNCH_HALF_WIDTH_M, (
            f"Destination is outside the fence; the southern launch corridor extends only {LAUNCH_HALF_WIDTH_M:g} m east and west."
        )
    if name == "show_on_map":
        target = arguments["target"]
        fields = {
            "drone": {"target", "drone_id"},
            "point": {"target", "north_m", "east_m"},
            "area": {"target", "north_m", "east_m", "radius_m"},
        }
        assert set(arguments) == fields[target], (
            f"{target} map focus requires exactly {sorted(fields[target])}"
        )
        if target == "area":
            assert all(
                abs(arguments[k]) + arguments["radius_m"] <= BOUND_M for k in ("north_m", "east_m")
            ), "Map area extends outside the search fence"
