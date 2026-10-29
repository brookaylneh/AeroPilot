"""Record the authored meadow's lighting, grass geometry and material dependencies."""

import json
import sys
from pathlib import Path

import unreal

output = Path(sys.argv[1])
output.mkdir()
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
scene = {a.get_actor_label(): a for a in actors.get_all_level_actors()}
sun = scene["light.sun"]
rotation = sun.get_actor_rotation()
report = {
    "map": "/Game/OpenMeadow/OpenMeadow",
    "sun": {
        "rotationDegrees": dict(pitch=rotation.pitch, yaw=rotation.yaw, roll=rotation.roll),
        "intensityLux": sun.light_component.get_editor_property("intensity"),
        "sourceAngleDegrees": sun.light_component.get_editor_property("light_source_angle"),
    },
    "skyIntensity": scene["light.sky"].light_component.get_editor_property("intensity"),
    "fogDensity": scene["sky.haze"].component.get_editor_property("fog_density"),
    "volumetricFog": scene["sky.haze"].component.get_editor_property("enable_volumetric_fog"),
    "grass": [],
}
post = scene["look.daylight"].get_editor_property("settings")
report["postprocess"] = {
    key: post.get_editor_property(key)
    for key in (
        "auto_exposure_min_brightness",
        "auto_exposure_max_brightness",
        "auto_exposure_bias",
        "bloom_intensity",
        "motion_blur_amount",
        "white_temp",
        "override_dynamic_global_illumination_method",
        "override_reflection_method",
    )
}
landscape = next(a for a in scene.values() if isinstance(a, unreal.Landscape))
material = landscape.get_editor_property("landscape_material")
report["landscapeMaterial"] = material.get_path_name()
expressions = unreal.MaterialEditingLibrary.get_material_expressions(material)
outputs = [e for e in expressions if isinstance(e, unreal.MaterialExpressionLandscapeGrassOutput)]
assert len(outputs) == 1
for entry in outputs[0].get_editor_property("grass_types"):
    grass = entry.get_editor_property("grass_type")
    for variety in grass.get_editor_property("grass_varieties"):
        mesh = variety.get_editor_property("grass_mesh")
        settings = mesh.get_editor_property("nanite_settings")
        report["grass"].append(
            {
                "type": grass.get_path_name(),
                "mesh": mesh.get_path_name(),
                "nanite": settings.get_editor_property("enabled"),
                "lodVertices": [mesh.get_num_vertices(i) for i in range(mesh.get_num_lods())],
                "density": variety.get_editor_property("grass_density").get_editor_property(
                    "default"
                ),
                "startCullCm": variety.get_editor_property(
                    "start_cull_distance"
                ).get_editor_property("default"),
                "endCullCm": variety.get_editor_property("end_cull_distance").get_editor_property(
                    "default"
                ),
                "overrideMaterials": [
                    m.get_path_name() for m in variety.get_editor_property("override_materials")
                ],
                "materials": [
                    m.material_interface.get_path_name()
                    for m in mesh.get_editor_property("static_materials")
                ],
            }
        )
(output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_RENDER_CONTENT_INSPECTED")
