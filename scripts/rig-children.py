"""Prepare the five licensed scanned children in Blender for Unreal.

Run with Blender --background --python scripts/rig-children.py. The purchased/
acquired GLB is kept outside public source. Bones follow the scanned proportions;
no adult mesh or adult animation is scaled into a child.
"""

import json
import math
from pathlib import Path
import bpy
import bmesh
from array import array
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "evidence/phase3").mkdir(parents=True, exist_ok=True)
OUT = ROOT / "vendor/children-fab/rigged"
OUT.mkdir(exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(ROOT / "vendor/children-fab/blend.glb"))
bpy.context.view_layer.update()
source = {index: bpy.data.objects[f"Object_{2 * index + 2}"] for index in range(1, 6)}
# Heights are uniform physical scale for each genuine child scan, in metres.
heights = [1.28, 1.32, 1.38, 1.39, 1.42]
# Joint landmarks fitted to the scans in normalized model coordinates. The
# asymmetric legs follow each photographed stance, including crossed legs.
legs = [
    [
        [(-0.095, -0.050, 0.50), (-0.104, -0.067, 0.275), (-0.111, -0.061, 0.065)],
        [(0.021, -0.008, 0.50), (0.038, 0.001, 0.275), (-0.001, -0.021, 0.065)],
    ],
    [
        [(-0.039, 0.081, 0.50), (-0.063, 0.108, 0.275), (-0.073, 0.170, 0.065)],
        [(0.064, 0.025, 0.50), (0.054, 0.042, 0.275), (0.048, 0.057, 0.065)],
    ],
    [
        [(-0.108, -0.074, 0.50), (-0.110, -0.158, 0.32), (-0.115, -0.21, 0.18)],
        [(0.038, 0, 0.50), (0, 0.022, 0.275), (-0.017, 0.003, 0.065)],
    ],
    [
        [(-0.071, -0.077, 0.50), (-0.049, -0.035, 0.275), (-0.014, -0.036, 0.065)],
        [(0.030, 0.01, 0.50), (-0.003, 0.069, 0.275), (-0.081, 0.219, 0.065)],
    ],
    [
        [(-0.065, 0.072, 0.50), (-0.080, 0.040, 0.275), (-0.041, 0.055, 0.065)],
        [(0.064, -0.028, 0.50), (0.056, -0.037, 0.275), (0.092, 0.022, 0.065)],
    ],
]
report = []
for number, obj in source.items():
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    world = obj.matrix_world.copy()
    obj.parent = None
    obj.matrix_world = world
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    points = [v.co for v in obj.data.vertices]
    top = max(v.z for v in points)
    head = [v for v in points if v.z > top * 0.86]
    center = sum(head, Vector()) / len(head)
    # Ground comes from the shoes beneath the body, excluding carried objects.
    shoes = [v.z for v in points if abs(v.x - center.x) < 0.13 and abs(v.y - center.y) < 0.14]
    floor = min(shoes)
    scale = heights[number - 1] / (top - floor)
    for vertex in obj.data.vertices:
        vertex.co = (vertex.co - Vector((center.x, center.y, floor))) * scale
    obj.name = f"Child_{number:02d}"
    # Weld the GLB's split UV seams before heat weighting; UVs remain per corner.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.00001)
    bpy.ops.object.mode_set(mode="OBJECT")
    # Remove tiny disconnected scan fragments, including a stray triangle below
    # the skateboard scan. The complete person must remain one connected mesh.
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    unseen = set(mesh.verts)
    components = []
    while unseen:
        component = {unseen.pop()}
        frontier = list(component)
        while frontier:
            vertex = frontier.pop()
            for edge in vertex.link_edges:
                neighbor = edge.other_vert(vertex)
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        components.append(component)
    largest = max(components, key=len)
    discarded = set(mesh.verts) - largest
    assert len(discarded) < len(mesh.verts) * 0.01, "Unexpected disconnected anatomy"
    bmesh.ops.delete(mesh, geom=list(discarded), context="VERTS")
    bmesh.ops.dissolve_degenerate(mesh, edges=list(mesh.edges), dist=0.0000001)
    mesh.to_mesh(obj.data)
    mesh.free()
    height = heights[number - 1]
    armature = bpy.data.armatures.new(f"Child_{number:02d}_Skeleton")
    rig = bpy.data.objects.new(f"Child_{number:02d}_Rig", armature)
    bpy.context.collection.objects.link(rig)
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name, start, end, parent=None):
        result = armature.edit_bones.new(name)
        result.head = Vector(start) * height
        result.tail = Vector(end) * height
        if parent:
            result.parent = armature.edit_bones[parent]
        result.use_deform = name != "root"
        return result

    bone("root", (0, 0, 0), (0, 0, 0.08))
    bone("pelvis", (0, 0, 0.50), (0, 0, 0.57), "root")
    bone("spine_01", (0, 0, 0.57), (0, 0, 0.65), "pelvis")
    bone("spine_02", (0, 0, 0.65), (0, 0, 0.71), "spine_01")
    bone("spine_03", (0, 0, 0.71), (0, 0, 0.78), "spine_02")
    bone("neck_01", (0, 0, 0.78), (0, 0, 0.86), "spine_03")
    bone("head", (0, 0, 0.86), (0, 0, 0.99), "neck_01")
    for side, sign in [("l", 1), ("r", -1)]:
        hip, knee, ankle = legs[number - 1][0 if sign == -1 else 1]
        bone("thigh_" + side, hip, knee, "pelvis")
        bone("calf_" + side, knee, ankle, "thigh_" + side)
        bone("foot_" + side, ankle, (ankle[0], ankle[1] - 0.09, ankle[2] - 0.025), "calf_" + side)
        shoulder = (sign * 0.105, 0, 0.775)
        elbow = (sign * 0.19, -0.01, 0.65) if number <= 2 else (sign * 0.12, -0.005, 0.64)
        wrist = (sign * 0.085, -0.045, 0.56) if number <= 2 else (sign * 0.12, -0.018, 0.52)
        bone("clavicle_" + side, (0, 0, 0.77), shoulder, "spine_03")
        bone("upperarm_" + side, shoulder, elbow, "clavicle_" + side)
        bone("lowerarm_" + side, elbow, wrist, "upperarm_" + side)
        bone(
            "hand_" + side, wrist, (wrist[0], wrist[1] - 0.01, wrist[2] - 0.045), "lowerarm_" + side
        )
    bpy.ops.object.mode_set(mode="OBJECT")
    # Scans contain non-manifold seams. Bind a closed voxel proxy, then transfer
    # its smooth skin weights onto the unchanged high-resolution scan.
    proxy = obj.copy()
    proxy.data = obj.data.copy()
    bpy.context.collection.objects.link(proxy)
    bpy.ops.object.select_all(action="DESELECT")
    proxy.select_set(True)
    bpy.context.view_layer.objects.active = proxy
    proxy.data.remesh_voxel_size = height * 0.008
    bpy.ops.object.voxel_remesh()
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    bpy.context.view_layer.objects.active = proxy
    bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    assert all(sum(g.weight for g in v.groups) > 0.99 for v in proxy.data.vertices), (
        f"Incomplete proxy weights: {obj.name}"
    )
    for group in proxy.vertex_groups:
        obj.vertex_groups.new(name=group.name)
    transfer = obj.modifiers.new("Skin weights from closed proxy", "DATA_TRANSFER")
    transfer.object = proxy
    transfer.use_vert_data = True
    transfer.data_types_verts = {"VGROUP_WEIGHTS"}
    transfer.vert_mapping = "POLYINTERP_NEAREST"
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=transfer.name)
    bpy.data.objects.remove(proxy, do_unlink=True)
    obj.parent = rig
    modifier = obj.modifiers.new("Child skeleton", "ARMATURE")
    modifier.object = rig
    assert all(sum(g.weight for g in v.groups) > 0.99 for v in obj.data.vertices), (
        f"Incomplete weights: {obj.name}"
    )
    image = next(n.image for n in obj.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE")
    pixels = array("f", [0]) * len(image.pixels)
    image.pixels.foreach_get(pixels)
    uv = obj.data.uv_layers.active.data
    surface_colors = [None] * len(obj.data.vertices)
    for loop in obj.data.loops:
        u, v = uv[loop.index].uv
        offset = 4 * (
            min(image.size[1] - 1, max(0, int(v * image.size[1]))) * image.size[0]
            + min(image.size[0] - 1, max(0, int(u * image.size[0])))
        )
        surface_colors[loop.vertex_index] = pixels[offset : offset + 3]
    # Material masks stay with the vertices during deformation. Face, hair,
    # hands, uncovered legs and footwear retain their scanned appearance.
    mask = obj.data.color_attributes.new(name="ClothingMask", type="FLOAT_COLOR", domain="POINT")
    for vertex in obj.data.vertices:
        x, y, z = vertex.co / height
        upper = 0.49 < z < 0.80 and abs(x) < (0.20 if number <= 3 else 0.105)
        if number <= 2 and z < 0.59 and abs(x) > 0.06:
            upper = False  # hands at the waist
        r, g, b = surface_colors[vertex.index]
        if number == 1:
            upper = 0.44 < z < 0.83 and r < g * 1.45 and g > b * 1.2
        if number >= 3:
            denim = b > r * 1.05 and b > g
            neutral_fabric = max(r, g, b) - min(r, g, b) < max(r, g, b) * 0.15
            upper = 0.44 < z < 0.80 and neutral_fabric and not denim
            if number >= 4:
                upper = upper and y < 0.10 and abs(x) < 0.105 and max(r, g, b) > 0.3
        lower = 0.095 < z < 0.50 and not upper
        if number == 1:
            lower = 0.22 < z < 0.50 and r > g * 1.8 and not upper
        if number >= 3:
            lower = 0.095 < z < (0.50 if number == 3 else 0.46) and denim and not upper
            if number >= 4:
                lower = lower and abs(x) < 0.16 and y < 0.2
        dominant = max(vertex.groups, key=lambda group: group.weight)
        body_part = obj.vertex_groups[dominant.group].name
        if body_part.startswith(("hand_", "foot_")):
            upper = lower = False
        mask.data[vertex.index].color = (float(upper), float(lower), 0, 1)
    image.filepath_raw = str(OUT / f"Child_{number:02d}_Albedo.png")
    image.file_format = "PNG"
    image.save()
    rig.animation_data_create()

    def evaluate():
        bpy.context.view_layer.update()

    def aim(name, target):
        evaluate()
        pb = rig.pose.bones[name]
        delta = (pb.tail - pb.head).rotation_difference(Vector(target) * height - pb.head)
        head = pb.head.copy()
        pb.matrix = (
            Matrix.Translation(head)
            @ delta.to_matrix().to_4x4()
            @ Matrix.Translation(-head)
            @ pb.matrix
        )
        evaluate()

    supported = ["waiting", "lost"] if number == 5 else ["waiting", "lost", "injured"]
    for situation in supported:
        action = bpy.data.actions.new(f"AS_Child_{number:02d}_{situation}")
        rig.animation_data.action = action
        for pb in rig.pose.bones:
            pb.rotation_mode = "QUATERNION"
            pb.matrix_basis = Matrix.Identity(4)
        evaluate()
        if number == 3:
            aim("thigh_r", (-0.075, -0.012, 0.275))
            aim("calf_r", (-0.08, -0.008, 0.065))
        if situation == "injured":
            mat = rig.pose.bones["pelvis"].matrix.copy()
            mat.translation.z = 0.09 * height
            rig.pose.bones["pelvis"].matrix = mat
            for side, sign in [("l", 1), ("r", -1)]:
                aim("thigh_" + side, (sign * 0.075, -0.23, 0.08))
                aim("calf_" + side, (sign * 0.085, -0.46, 0.055))
                aim("foot_" + side, (sign * 0.085, -0.55, 0.025))
                aim("upperarm_" + side, (sign * 0.17, -0.065, 0.28))
                aim("lowerarm_" + side, (sign * 0.10, -0.20, 0.16))
        evaluate()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        floor = min(vertex.co.z for vertex in evaluated.data.vertices)
        ground_transform = rig.pose.bones["root"].matrix.copy()
        ground_transform.translation.z -= floor
        rig.pose.bones["root"].matrix = ground_transform
        evaluate()
        neutral = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        for frame in range(121):
            t = frame / 120 * 2 * math.pi
            for pb in rig.pose.bones:
                pb.matrix_basis = neutral[pb.name]
                if pb.name == "spine_03":
                    pb.rotation_quaternion @= Quaternion(
                        (1, 0, 0), math.radians(0.45 * math.sin(t))
                    )
                if pb.name == "head":
                    pb.rotation_quaternion @= Quaternion(
                        (0, 1, 0), math.radians((10 if situation == "lost" else 1.0) * math.sin(t))
                    )
                pb.keyframe_insert("location", frame=frame)
                pb.keyframe_insert("rotation_quaternion", frame=frame)
                pb.keyframe_insert("scale", frame=frame)
        action.use_fake_user = True
    rig.animation_data.action = bpy.data.actions[f"AS_Child_{number:02d}_waiting"]
    bpy.context.scene.frame_start = 0
    bpy.context.scene.frame_end = 120
    bpy.context.scene.render.fps = 30
    bpy.context.scene.frame_set(0)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    # Only this character's actions are eligible for this rig's FBX export.
    for action in bpy.data.actions:
        action.use_fake_user = action.name.startswith(f"AS_Child_{number:02d}_")
    bpy.ops.export_scene.fbx(
        filepath=str(OUT / f"Child_{number:02d}.fbx"),
        use_selection=True,
        object_types={"MESH", "ARMATURE"},
        add_leaf_bones=False,
        bake_anim=True,
        mesh_smooth_type="FACE",
        bake_anim_use_all_actions=False,
        bake_anim_use_nla_strips=False,
        axis_forward="-Y",
        axis_up="Z",
        bake_anim_simplify_factor=0.0,
        path_mode="COPY",
        embed_textures=False,
    )
    for situation in supported:
        rig.animation_data.action = bpy.data.actions[f"AS_Child_{number:02d}_{situation}"]
        bpy.context.scene.frame_set(0)
        bpy.ops.export_scene.fbx(
            filepath=str(OUT / f"Child_{number:02d}_{situation}.fbx"),
            use_selection=True,
            object_types={"ARMATURE"},
            add_leaf_bones=False,
            bake_anim=True,
            bake_anim_use_all_actions=False,
            bake_anim_use_nla_strips=False,
            axis_forward="-Y",
            axis_up="Z",
            bake_anim_simplify_factor=0.0,
        )
    rig.animation_data.action = bpy.data.actions[f"AS_Child_{number:02d}_waiting"]
    bpy.context.scene.frame_set(0)
    report.append(
        {
            "id": f"child-{number:02d}",
            "heightCm": height * 100,
            "vertices": len(obj.data.vertices),
            "bones": len(armature.bones),
            "animations": supported,
        }
    )
    # Retain every animation in the review .blend.
    for action in bpy.data.actions:
        action.use_fake_user = True
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "children-rigged.blend"))
(ROOT / "evidence/phase3/children-rigging.json").write_text(json.dumps(report, indent=2))
print("SNR_CHILDREN_RIGGED")
