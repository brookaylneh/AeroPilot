"""Read mesh complexity and collision from the authored world."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
source = json.loads((root / 'evidence/wilderness-import.json').read_text())
mesh_sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
report = {'meshes': []}
for asset, entries in source['models'].items():
    for entry in entries:
        mesh = unreal.load_asset(entry['path'])
        count = mesh.get_num_lods()
        report['meshes'].append({'path': entry['path'], 'lods': count,
            'vertices': [mesh.get_num_vertices(lod) for lod in range(count)]})
(root / 'evidence/wilderness-complexity.json').write_text(json.dumps(report, indent=2) + '\n')
