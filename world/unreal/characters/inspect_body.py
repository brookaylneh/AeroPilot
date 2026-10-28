"""Audit the untextured, single-person GLB exported by SAM 3D Body."""

import argparse
import json
from pathlib import Path

import numpy as np

from mission_planner.environment import load_env
from world.unreal.characters.generate import digest, write_json
from world.unreal.characters.inspect_mesh import accessor, read_glb


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document, binary = read_glb(args.model)
    assert len(document["meshes"]) == 1
    assert len(document["meshes"][0]["primitives"]) == 1
    assert all(key not in document for key in ("textures", "skins", "animations"))
    primitive = document["meshes"][0]["primitives"][0]
    assert primitive["mode"] == 4
    vertices = accessor(document, binary, primitive["attributes"]["POSITION"])
    indices = accessor(document, binary, primitive["indices"]).reshape(-1, 3)
    assert vertices.shape[1] == 3 and indices.max() < len(vertices)
    triangles = vertices[indices]
    areas = (
        np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        )
        / 2
    )
    edges = np.sort(
        np.concatenate([indices[:, [0, 1]], indices[:, [1, 2]], indices[:, [2, 0]]]), axis=1
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    report = {
        "sha256": digest(args.model),
        "vertices": len(vertices),
        "triangles": len(indices),
        "degenerate_triangles": int((areas <= 1e-12).sum()),
        "boundary_edges": int((counts == 1).sum()),
        "nonmanifold_edges": int((counts > 2).sum()),
        "bounds_m": [vertices.min(0).tolist(), vertices.max(0).tolist()],
        "textures": 0,
        "skins": 0,
        "animations": 0,
        "coordinate_frame": "glTF metres, +Y up; camera translation retained",
        "stature_note": "Single-image inferred scale, not independently measured height",
    }
    write_json(args.output, report)
    assert report["degenerate_triangles"] == report["nonmanifold_edges"] == 0
    assert report["boundary_edges"] == 0
    print(json.dumps(report))


if __name__ == "__main__":
    main()
