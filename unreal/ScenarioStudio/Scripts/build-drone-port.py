"""Targeted, repeatable level update. Search landscape and catalog stay intact."""
import json, math, struct, hashlib
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
ml = unreal.MaterialEditingLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')
catalog = unreal.load_asset('/Game/OpenMeadow/Catalog')
regions = catalog.get_editor_property('regions')
assert len(regions) == 1
r = regions[0]; lo = r.get_editor_property('min'); hi = r.get_editor_property('max')
assert abs(hi.x-lo.x-160934.4) < .1 and abs(hi.y-lo.y-160934.4) < .1
starts = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.PlayerStart)]
assert len(starts) == 1
start = starts[0].get_actor_location()
assert abs(start.x) < .01 and abs(start.y) < .01 and abs(start.z-4000) < .01
catalog.set_editor_property('cosys_origin_cm', start)
lib.save_loaded_asset(catalog)
for a in actors.get_all_level_actors():
    if a.get_actor_label().startswith('drone.port'):
        actors.destroy_actor(a)
folder = '/Game/OpenMeadow/DronePort'
source = json.loads((root/'evidence/phase2a-concrete-source.json').read_text())
textures = {}
for key, entry in source['files'].items():
    file = root/entry['file']; path = folder+'/'+file.stem
    if not lib.does_asset_exist(path):
        task = unreal.AssetImportTask(); task.set_editor_property('filename', str(file))
        task.set_editor_property('destination_path', folder); task.set_editor_property('automated', True)
        task.set_editor_property('save', True); tools.import_asset_tasks([task])
    texture = unreal.load_asset(path); assert texture
    if key != 'diff': texture.set_editor_property('srgb', False)
    if key == 'nor_dx': texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
    lib.save_loaded_asset(texture); textures[key] = texture
path = folder+'/M_Concrete'
material = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset('M_Concrete', folder, unreal.Material, unreal.MaterialFactoryNew())
ml.delete_all_material_expressions(material)
uv = ml.create_material_expression(material, unreal.MaterialExpressionTextureCoordinate)
uv.set_editor_property('u_tiling', 7.); uv.set_editor_property('v_tiling', 7.)
for key, prop in [('diff', unreal.MaterialProperty.MP_BASE_COLOR), ('nor_dx', unreal.MaterialProperty.MP_NORMAL), ('rough', unreal.MaterialProperty.MP_ROUGHNESS)]:
    sample = ml.create_material_expression(material, unreal.MaterialExpressionTextureSample)
    sample.set_editor_property('texture', textures[key])
    sample.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if key=='nor_dx' else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR if key=='rough' else unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
    ml.connect_material_expressions(uv, '', sample, 'UVs')
    ml.connect_material_property(sample, 'RGB' if key!='rough' else 'R', prop)
ml.recompile_material(material); lib.save_loaded_asset(material)

center_x, center_y, top = lo.x-1200., 0., 1125.
slab = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(center_x, center_y, top-15))
slab.set_actor_label('drone.port'); slab.set_editor_property('tags', [unreal.Name('drone.port')])
slab.static_mesh_component.set_static_mesh(unreal.load_asset('/Engine/BasicShapes/Cube'))
slab.set_actor_scale3d(unreal.Vector(14,14,.3)); slab.static_mesh_component.set_material(0,material)
slab.static_mesh_component.set_collision_profile_name('ScenarioObstacle')

# Locally grade supporting ground. Nothing is added to the searchable bounds.
# The original scenery has no collision; this patch provides physical support.
heightfile = root/'vendor/open-meadow/height.r16'; raw = heightfile.read_bytes()
def edge_height(y):
    # Heightfield rows run east (+Y); the southern edge is column zero.
    f = max(0,min(2016,(y-lo.y)/(hi.y-lo.y)*2016)); i = min(2015,int(f)); t=f-i
    a=struct.unpack_from('<H',raw,i*2017*2)[0]
    b=struct.unpack_from('<H',raw,(i+1)*2017*2)[0]
    return ((a*(1-t)+b*t)-32768)/128*100
def background(x,y):
    x,y=x/100*2,y/100*2
    return 100*(8+3*math.sin(x/470)*math.cos(y/590)+1.3*math.sin(y/95+x/180)
        +24*math.exp(-((x+1100)/500)**2-((y-700)/650)**2)
        +36*math.exp(-((x-1200)/650)**2-((y-600)/450)**2)
        +26*math.exp(-((x-600)/550)**2-((y+1400)/600)**2)
        +.06*math.sin(x/6)*math.sin(y/7))
vertices, triangles, uvs = [],[],[]
nx,ny=39,51; south=center_x-2600
for j in range(ny):
    y=-2500+5000*j/(ny-1)
    for i in range(nx):
        x=south+(lo.x-south)*i/(nx-1)
        distance=max(abs(x-center_x)-720, abs(y)-720, 0)
        blend=max(0,1-distance/1500); blend=blend*blend*(3-2*blend)
        z=background(x,y)*(1-blend)+(top-12)*blend+3
        if x>center_x+720:
            t=(x-(center_x+720))/(lo.x-(center_x+720)); z=z*(1-t)+(edge_height(y)+1)*t
        vertices.append(unreal.Vector(x,y,z)); uvs.append(unreal.Vector2D((x-lo.x)/(hi.x-lo.x),(y-lo.y)/(hi.y-lo.y)))
        if i<nx-1 and j<ny-1:
            a=j*nx+i; triangles.extend([unreal.IntVector(a,a+nx,a+1),unreal.IntVector(a+1,a+nx,a+nx+1)])
path=folder+'/SM_Support'
if lib.does_asset_exist(path): lib.delete_asset(path)
dynamic=unreal.DynamicMesh()
unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dynamic,unreal.GeometryScriptSimpleMeshBuffers(vertices=vertices,uv0=uvs,triangles=triangles))
mesh,outcome=unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic,path,
    unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_collision=True,enable_recompute_normals=True,enable_recompute_tangents=True))
assert mesh, str(outcome)
landscape=next(a for a in actors.get_all_level_actors() if isinstance(a,unreal.Landscape))
ground=landscape.get_editor_property('landscape_material')
mesh.set_editor_property('static_materials',[unreal.StaticMaterial(material_interface=ground,material_slot_name='Ground')])
mesh.get_editor_property('body_setup').set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
lib.save_loaded_asset(mesh)
support=actors.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector())
support.set_actor_label('drone.port.support');support.static_mesh_component.set_static_mesh(mesh)
support.static_mesh_component.set_collision_profile_name('ScenarioObstacle')
assert levels.save_current_level()

def vec(v):return {'x':v.x,'y':v.y,'z':v.z}
report={'schemaVersion':1,'worldId':'open-meadow-unreal','environmentAssetVersion':str(catalog.get_editor_property('asset_version')),
    'infrastructureVersion':'four-drone-port-2-south','coordinateSystem':'UE_LH_Z_UP_CENTIMETERS','north':'+X','east':'+Y',
    'searchBoundsCm':{'min':vec(lo),'max':vec(hi)},'widthMetres':1609.344,
    'playerStartUeCm':vec(start),'moveWorldOrigin':False,
    'port':{'boundary':'south','centerUeCm':{'x':center_x,'y':center_y,'z':top},'dimensionsMetres':[14,14,.3],'nearestBoundaryGapMetres':5,'searchable':False},
    'heightfieldSha256':hashlib.sha256(raw).hexdigest(),'vehicles':{}}
for n,(east,north) in enumerate([(-2.5,2.5),(2.5,2.5),(-2.5,-2.5),(2.5,-2.5)],1):
    report['vehicles']['Drone'+str(n)]={'spawnUeCm':{'x':center_x+north*100,'y':center_y+east*100,'z':top+20},'yawDegrees':0}
(root/'simulation/phase2a/world.json').write_text(json.dumps(report,indent=2)+'\n')
(root/'evidence/phase2a-port-authoring.json').write_text(json.dumps(report,indent=2)+'\n')
unreal.log('SNR_PHASE2A_PORT_AUTHORED')
