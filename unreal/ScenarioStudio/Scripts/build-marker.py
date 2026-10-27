"""Terrain-projected placement feedback, hidden in inspection camera views."""
import unreal
lib = unreal.EditorAssetLibrary
edit = unreal.MaterialEditingLibrary
path = '/Game/Scenario/Equipment/M_PlacementMarker'
material = unreal.load_asset(path) if lib.does_asset_exist(path) else unreal.AssetToolsHelpers.get_asset_tools().create_asset(
    'M_PlacementMarker', '/Game/Scenario/Equipment', unreal.Material, unreal.MaterialFactoryNew())
edit.delete_all_material_expressions(material)
material.set_editor_property('material_domain', unreal.MaterialDomain.MD_DEFERRED_DECAL)
material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
def node(cls, **properties):
    result = edit.create_material_expression(material, cls)
    for key, value in properties.items():
        result.set_editor_property(key, value)
    return result
def wire(a, b, pin, output=''):
    if not edit.connect_material_expressions(a, output, b, pin):
        raise RuntimeError('Placement material connection failed')
uv = node(unreal.MaterialExpressionTextureCoordinate)
center = node(unreal.MaterialExpressionConstant2Vector, r=.5, g=.5)
offset = node(unreal.MaterialExpressionSubtract)
wire(uv, offset, 'A'); wire(center, offset, 'B')
dot = node(unreal.MaterialExpressionDotProduct)
wire(offset, dot, 'A'); wire(offset, dot, 'B')
length = node(unreal.MaterialExpressionSquareRoot); wire(dot, length, '')
radius = node(unreal.MaterialExpressionSubtract, const_b=.42); wire(length, radius, 'A')
distance = node(unreal.MaterialExpressionAbs); wire(radius, distance, '')
scale = node(unreal.MaterialExpressionMultiply, const_b=70); wire(distance, scale, 'A')
inverse = node(unreal.MaterialExpressionOneMinus); wire(scale, inverse, '')
alpha = node(unreal.MaterialExpressionSaturate); wire(inverse, alpha, '')
color = node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.7, .37, .04, 1))
edit.connect_material_property(color, '', unreal.MaterialProperty.MP_BASE_COLOR)
edit.connect_material_property(alpha, '', unreal.MaterialProperty.MP_OPACITY)
edit.recompile_material(material)
lib.save_loaded_asset(material)
unreal.log('SNR_PLACEMENT_MARKER_SAVED')
