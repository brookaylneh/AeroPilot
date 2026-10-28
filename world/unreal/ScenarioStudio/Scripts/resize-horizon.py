"""Clip existing horizon triangles out of the search rectangle; preserve southern scenery."""

import hashlib
import json
import os
import struct
import sys
from collections import Counter
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env
from world.unreal.horizon import outside_rectangle

load_env()
world = json.loads((ROOT / "world/cosys/world.json").read_text())
lo, hi = world["searchBoundsCm"]["min"], world["searchBoundsCm"]["max"]
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "horizon-authoring.json"
assert not report_path.exists(), "Archive previous horizon evidence before a new run"
lib = unreal.EditorAssetLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
queries = unreal.GeometryScript_MeshQueries
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
backgrounds = [
    a
    for a in actors.get_all_level_actors()
    if a.get_actor_label() == "scenery.non-placeable-horizon"
]
assert len(backgrounds) == 1
background = backgrounds[0]
assert background.get_actor_transform() == unreal.Transform()
source = unreal.load_asset("/Game/OpenMeadow/SM_Horizon_020")
assert source


def mesh_triangles(asset):
    dynamic, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(
        asset,
        unreal.DynamicMesh(),
        unreal.GeometryScriptCopyMeshFromAssetOptions(),
        unreal.GeometryScriptMeshReadLOD(
            lod_type=unreal.GeometryScriptLODType.SOURCE_MODEL, lod_index=0
        ),
        use_section_materials=False,
    )
    assert outcome == unreal.GeometryScriptOutcomePins.SUCCESS
    assert not queries.get_has_triangle_id_gaps(dynamic)
    result = []
    for index in range(queries.get_num_triangle_ids(dynamic)):
        valid, p1, p2, p3 = queries.get_triangle_positions(dynamic, index)
        uv1, uv2, uv3, valid_uvs = queries.get_triangle_u_vs(dynamic, 0, index)
        _, n1, n2, n3, valid_normals = queries.get_triangle_normals(dynamic, index)
        assert valid and valid_uvs and valid_normals
        result.append(
            tuple(
                (p.x, p.y, p.z, n.x, n.y, n.z, uv.x, uv.y)
                for p, n, uv in zip((p1, p2, p3), (n1, n2, n3), (uv1, uv2, uv3))
            )
        )
    return result


def triangle_bytes(triangle):
    ordered = min(triangle, triangle[1:] + triangle[:1], triangle[2:] + triangle[:2])
    values = tuple(value for vertex in ordered for value in vertex)
    return struct.pack("<" + "d" * len(values), *values)


original = mesh_triangles(source)
retained = [
    triangle
    for source_triangle in original
    for triangle in outside_rectangle(source_triangle, (lo["x"], lo["y"]), (hi["x"], hi["y"]))
]
assert retained and retained != original
vertices, normals, uvs, indices = [], [], [], []
for triangle in retained:
    start = len(vertices)
    indices.append(unreal.IntVector(start, start + 1, start + 2))
    for x, y, z, nx, ny, nz, u, v in triangle:
        vertices.append(unreal.Vector(x, y, z))
        normals.append(unreal.Vector(nx, ny, nz))
        uvs.append(unreal.Vector2D(u, v))
dynamic = unreal.DynamicMesh()
unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(
    dynamic,
    unreal.GeometryScriptSimpleMeshBuffers(
        vertices=vertices, normals=normals, uv0=uvs, triangles=indices
    ),
)
path = "/Game/OpenMeadow/WorldTerrain/SM_Horizon"
if lib.does_asset_exist(path):
    mesh = unreal.load_asset(path)
    _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(
        dynamic,
        mesh,
        unreal.GeometryScriptCopyMeshToAssetOptions(
            enable_recompute_normals=False, enable_recompute_tangents=True
        ),
        unreal.GeometryScriptMeshWriteLOD(lod_index=0),
        use_section_materials=False,
    )
else:
    mesh, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dynamic,
        path,
        unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
            enable_collision=False, enable_recompute_normals=False, enable_recompute_tangents=True
        ),
    )
assert mesh and outcome == unreal.GeometryScriptOutcomePins.SUCCESS
mesh.set_editor_property("static_materials", list(source.get_editor_property("static_materials")))
assert lib.save_loaded_asset(mesh)
written = mesh_triangles(mesh)
source_south = Counter(triangle_bytes(t) for t in original if max(v[0] for v in t) <= lo["x"])
written_triangles = Counter(triangle_bytes(t) for t in written)
assert source_south and not source_south - written_triangles
for triangle in written:
    x, y = (sum(v[axis] for v in triangle) / 3 for axis in (0, 1))
    assert not (lo["x"] + 0.01 < x < hi["x"] - 0.01 and lo["y"] + 0.01 < y < hi["y"] - 0.01)
material = background.static_mesh_component.get_material(0)
background.static_mesh_component.set_static_mesh(mesh)
background.static_mesh_component.set_material(0, material)
assert background.get_actor_transform() == unreal.Transform()
assert levels.save_current_level()
retained_south = source_south & written_triangles
report_path.write_text(
    json.dumps(
        {
            "environmentAssetVersion": world["environmentAssetVersion"],
            "sourceMesh": source.get_path_name(),
            "outputMesh": mesh.get_path_name(),
            "sourceTriangles": len(original),
            "outputTriangles": len(written),
            "southernTrianglesPreserved": sum(source_south.values()),
            "originalSouthernGeometrySha256": hashlib.sha256(
                b"".join(sorted(source_south.elements()))
            ).hexdigest(),
            "retainedSouthernGeometrySha256": hashlib.sha256(
                b"".join(sorted(retained_south.elements()))
            ).hexdigest(),
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_HORIZON_RESIZED")
