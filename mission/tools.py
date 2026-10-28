"""The eleven implemented mission tools; flight execution belongs to MAVLink."""

from jsonschema import Draft202012Validator

NAMES = tuple(f"Drone{i}" for i in range(1, 5))
BOUND_M = 804.672
LAUNCH_SOUTH_M = -844.672
LAUNCH_HALF_WIDTH_M = 25
ALTITUDE_MIN_M = 10
ALTITUDE_MAX_M = 100
DRONE = {"type": "string", "enum": list(NAMES)}
POSITION = {"type": "number", "minimum": -BOUND_M, "maximum": BOUND_M}
NORTH = {"type": "number", "minimum": LAUNCH_SOUTH_M, "maximum": BOUND_M}
ALTITUDE = {"type": "number", "minimum": ALTITUDE_MIN_M, "maximum": ALTITUDE_MAX_M}
SPEED = {"type": "number", "minimum": 0.5, "maximum": 15}


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
        "Use for live status of all four drones, including stale links and read-back missions. Requires MAVLink telemetry.",
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
            "radius_m": {"type": "number", "minimum": 1, "maximum": 804},
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
assert len(TOOLS) == 11 and len(SCHEMAS) == len(TOOLS)
AGENT_INSTRUCTIONS = """You are Wonder, the search-and-rescue commander's mission assistant.
Use only supplied briefing and measured telemetry; never infer private target truth or claim an action without its tool result.
Use the registered tools for live information and requested actions. When all arguments are supplied, call the requested tool directly: the service validates safety and manages operator approval.
Only Drone1, Drone2, Drone3 and Drone4 exist. Reject unknown drone identities without substituting another drone.
Positions are metres north/east of the search centre. The search square is ±804.672 m on each axis. An authorized launch corridor extends south to north -844.672 m only within east ±25 m; flight altitude is 10–100 m above home and speed is 0.5–15 m/s. Refuse requests explicitly outside these limits without a flight call.
Supervised flight actions require an approval card; never imply approval yourself. An operator decline is final for that request.
Never retry a failed tool, invent a replacement route, or disable safety checks. Report the precise error and await a new operator instruction.
Explain concepts and answer greetings without a tool when no live information or action is needed. Ask for missing flight arguments; do not guess them.
Perception, search planning, belief maps, ground-team messaging and video are unavailable in Phase 3A. Say so; do not pretend to invoke them.
Operator actions in the supplied event context have the same authority as chat instructions. Keep replies short, concrete and operational.
"""


def validate_arguments(name, arguments):
    assert name in SCHEMAS, f"Unknown tool: {name}"
    assert isinstance(arguments, dict), "Tool arguments must be an object"
    if "drone_id" in arguments and arguments["drone_id"] is not None:
        assert arguments["drone_id"] in NAMES, f"Unknown drone: {arguments['drone_id']}"
    errors = list(SCHEMAS[name].iter_errors(arguments))
    assert not errors, "; ".join(
        f"{'/'.join(map(str, error.path))}: {error.message}" for error in errors
    )
    points = []
    if name == "fly_route":
        points = arguments["waypoints"]
    elif name == "fly_to":
        points = [arguments]
    for point in points:
        assert point["north_m"] >= -BOUND_M or abs(point["east_m"]) <= LAUNCH_HALF_WIDTH_M, (
            "Point outside fence: southern launch corridor is only 50 m wide"
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
