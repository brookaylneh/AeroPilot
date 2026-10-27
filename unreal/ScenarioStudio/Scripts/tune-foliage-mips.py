"""Preserve masked leaf coverage as textures become smaller in the distance."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
assets = json.loads((root / 'evidence/wilderness-import.json').read_text())
for entries in assets['textures'].values():
    for name, path in entries.items():
        if '_alpha_' not in name:
            continue
        texture = unreal.load_asset(path)
        texture.set_editor_property('do_scale_mips_for_alpha_coverage', True)
        texture.set_editor_property('alpha_coverage_thresholds', unreal.Vector4(.333, 0, 0, 0))
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
        unreal.EditorAssetLibrary.save_loaded_asset(texture)
# The material sampler must agree with the mask texture's compression mode.
# Editor caches can hide this mismatch; cooked builds cannot compile a fallback.
for path in unreal.EditorAssetLibrary.list_assets('/Game/Wilderness/Materials'):
    material = unreal.load_asset(path)
    if not isinstance(material, unreal.Material):
        continue
    changed = False
    for expression in unreal.MaterialEditingLibrary.get_material_expressions(material):
        if isinstance(expression, unreal.MaterialExpressionTextureSample):
            texture = expression.get_editor_property('texture')
            if texture and texture.get_editor_property('compression_settings') == unreal.TextureCompressionSettings.TC_MASKS:
                expression.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
                changed = True
    if changed:
        unreal.MaterialEditingLibrary.recompile_material(material)
        unreal.EditorAssetLibrary.save_loaded_asset(material)
unreal.log('SNR_FOLIAGE_ALPHA_COVERAGE_SAVED')
