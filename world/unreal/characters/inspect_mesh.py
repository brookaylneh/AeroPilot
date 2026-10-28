"""Inventory and validate uncompressed GLB geometry and embedded PBR textures."""

import argparse
import hashlib
import io
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from mission_planner.environment import load_env


def read_glb(path):
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", raw)
    assert magic == b"glTF" and version == 2 and length == len(raw)
    json_length, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A
    document = json.loads(raw[20 : 20 + json_length])
    offset = 20 + json_length
    binary_length, binary_type = struct.unpack_from("<II", raw, offset)
    assert binary_type == 0x004E4942 and offset + 8 + binary_length == len(raw)
    binary = raw[offset + 8 :]
    assert len(document["buffers"]) == 1
    assert document["buffers"][0]["byteLength"] <= len(binary)
    return document, binary


def accessor(document, binary, index):
    item = document["accessors"][index]
    assert "sparse" not in item
    view = document["bufferViews"][item["bufferView"]]
    assert view["buffer"] == 0
    dtype = np.dtype({5123: "<u2", 5125: "<u4", 5126: "<f4"}[item["componentType"]])
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[item["type"]]
    # glTF specifies omitted offsets as zero and omitted stride as tightly packed.
    start = view["byteOffset"] if "byteOffset" in view else 0
    relative = item["byteOffset"] if "byteOffset" in item else 0
    stride = view["byteStride"] if "byteStride" in view else dtype.itemsize * width
    end = relative + (item["count"] - 1) * stride + dtype.itemsize * width
    assert end <= view["byteLength"] and start + view["byteLength"] <= len(binary)
    values = np.ndarray(
        (item["count"], width),
        dtype=dtype,
        buffer=binary,
        offset=start + relative,
        strides=(stride, dtype.itemsize),
    )
    assert np.isfinite(values).all()
    return values


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document, binary = read_glb(args.model)
    required = document["extensionsRequired"] if "extensionsRequired" in document else []
    assert set(required) <= {"EXT_texture_webp"}, "Required extension needs its decoder"
    primitives = []
    for mesh in document["meshes"]:
        for primitive in mesh["primitives"]:
            assert "mode" not in primitive or primitive["mode"] == 4
            assert "extensions" not in primitive
            attributes = primitive["attributes"]
            positions = accessor(document, binary, attributes["POSITION"])
            normals = accessor(document, binary, attributes["NORMAL"])
            uv = accessor(document, binary, attributes["TEXCOORD_0"])
            indices = accessor(document, binary, primitive["indices"]).reshape(-1)
            assert positions.shape == normals.shape == (len(positions), 3)
            assert uv.shape == (len(positions), 2)
            assert len(indices) % 3 == 0 and indices.max() < len(positions)
            triangles = positions[indices.reshape(-1, 3)]
            areas = (
                np.linalg.norm(
                    np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
                    axis=1,
                )
                / 2
            )
            lengths = np.linalg.norm(normals, axis=1)
            primitives.append(
                {
                    "vertices": len(positions),
                    "triangles": len(indices) // 3,
                    "degenerate_triangles": int(np.count_nonzero(areas <= 1e-12)),
                    "normal_length_min": float(lengths.min()),
                    "normal_length_max": float(lengths.max()),
                    "local_bounds_min": positions.min(axis=0).tolist(),
                    "local_bounds_max": positions.max(axis=0).tolist(),
                    "material": primitive["material"],
                }
            )
    images = []
    for index, item in enumerate(document["images"]):
        view = document["bufferViews"][item["bufferView"]]
        start = view["byteOffset"] if "byteOffset" in view else 0
        payload = binary[start : start + view["byteLength"]]
        assert len(payload) == view["byteLength"]
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            images.append(
                {
                    "index": index,
                    "size": list(image.size),
                    "format": image.format,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
    materials = []
    for item in document["materials"]:
        pbr = item["pbrMetallicRoughness"]
        materials.append(
            {
                "base_color_texture": pbr["baseColorTexture"]["index"],
                "metallic_roughness_texture": (
                    pbr["metallicRoughnessTexture"]["index"]
                    if "metallicRoughnessTexture" in pbr
                    else None
                ),
                "normal_texture": item["normalTexture"]["index"]
                if "normalTexture" in item
                else None,
                "definition": item,
            }
        )
    report = {
        "model": str(args.model),
        "sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "vertices": sum(p["vertices"] for p in primitives),
        "triangles": sum(p["triangles"] for p in primitives),
        "primitives": primitives,
        "images": images,
        "materials": materials,
        "textures": document["textures"],
        "nodes": document["nodes"],
        "skins": len(document["skins"]) if "skins" in document else 0,
        "animations": len(document["animations"]) if "animations" in document else 0,
        "unit_note": "glTF declares metres; generated physical stature has not been calibrated",
        "pbr_channels_complete": all(
            m["metallic_roughness_texture"] is not None and m["normal_texture"] is not None
            for m in materials
        ),
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "model",
                    "vertices",
                    "triangles",
                    "skins",
                    "pbr_channels_complete",
                    "images",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
