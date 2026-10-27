"""Inspect assembled runtime assets before authoring wardrobe bindings."""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
assembly = json.loads((root / 'evidence/metahuman-assembly.json').read_text())
assembly['assets'] = list(unreal.EditorAssetLibrary.list_assets('/Game/Scenario/MetaHumans/Adult', recursive=True))
assembly['blueprints'] = [p for p in assembly['assets'] if '/BP_Adult.' in p]
assembly['assembled'] = bool(assembly['blueprints'])
(root / 'evidence/metahuman-assembly.json').write_text(json.dumps(assembly, indent=2) + '\n')
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
report = {'blueprints': [], 'inspected': False}
for path in assembly['blueprints']:
    if '/BP_Adult.' not in path:
        continue
    cls = unreal.EditorAssetLibrary.load_blueprint_class(path)
    actor = actors.spawn_actor_from_class(cls, unreal.Vector())
    item = {'path': path, 'components': []}
    for owner in [actor] + list(actor.get_all_child_actors(True)):
        for component in owner.get_components_by_class(unreal.MeshComponent):
            entry = {'name': component.get_name(), 'class': component.get_class().get_name(), 'materials': []}
            for index in range(component.get_num_materials()):
                material = component.get_material(index)
                if not material:
                    continue
                entry['materials'].append({'slot': index, 'path': material.get_path_name(),
                    'vectorParameters': [str(n) for n in unreal.MaterialEditingLibrary.get_vector_parameter_names(material)],
                    'scalarParameters': [str(n) for n in unreal.MaterialEditingLibrary.get_scalar_parameter_names(material)]})
            if isinstance(component, unreal.SkeletalMeshComponent):
                mesh = component.get_skinned_asset()
                entry['mesh'] = mesh.get_path_name() if mesh else None
                entry['animationMode'] = str(component.get_editor_property('animation_mode'))
                entry['skeleton'] = mesh.get_editor_property('skeleton').get_path_name() if mesh else None
            item['components'].append(entry)
    report['blueprints'].append(item)
    actors.destroy_actor(actor)
report['inspected'] = bool(report['blueprints']) and all(item['components'] for item in report['blueprints'])
(root / 'evidence/metahuman-runtime-assets.json').write_text(json.dumps(report, indent=2) + '\n')
if not report['inspected']:
    raise RuntimeError('Runtime character has no mesh components; check Blueprint/plugin load errors')
unreal.log('SNR_METAHUMAN_INSPECTED')
