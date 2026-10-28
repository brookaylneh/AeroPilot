"""Place a SAM GLB at floor level without changing its shape, scale or topology."""

import argparse
import json
import struct
from pathlib import Path

import numpy as np

from mission_planner.environment import load_env
from world.unreal.characters.generate import digest, write_json
from world.unreal.characters.inspect_mesh import accessor, read_glb


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    document, source_binary = read_glb(args.source)
    assert document["nodes"] == [{"name": "person_00", "mesh": 0}]
    assert len(document["meshes"]) == 1
    primitives = document["meshes"][0]["primitives"]
    assert len(primitives) == 1
    position_index = primitives[0]["attributes"]["POSITION"]
    binary = bytearray(source_binary)
    positions = accessor(document, binary, position_index)
    original = positions.copy()
    minimum, maximum = original.min(0), original.max(0)
    offset = (minimum + maximum) / 2
    offset[1] = minimum[1]
    positions -= offset
    assert np.allclose(positions + offset, original, atol=1e-6, rtol=0)
    document["accessors"][position_index]["min"] = positions.min(0).tolist()
    document["accessors"][position_index]["max"] = positions.max(0).tolist()
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    with args.target.open("xb") as stream:
        stream.write(struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(binary)))
        stream.write(struct.pack("<II", len(encoded), 0x4E4F534A))
        stream.write(encoded)
        stream.write(struct.pack("<II", len(binary), 0x004E4942))
        stream.write(binary)
    write_json(
        args.report,
        {
            "source_sha256": digest(args.source),
            "target_sha256": digest(args.target),
            "subtract_translation_m": offset.tolist(),
            "target_bounds_m": [positions.min(0).tolist(), positions.max(0).tolist()],
            "shape_scale_topology_preserved": True,
            "coordinate_frame": "glTF metres, +Y up; horizontal bounds centered; feet at Y=0",
        },
    )


if __name__ == "__main__":
    main()
