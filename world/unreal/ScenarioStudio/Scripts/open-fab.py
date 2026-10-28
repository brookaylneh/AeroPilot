"""Open Epic's Fab UI in a regular editor, with private local control.

This does not expose authentication, asset importing or console access through
the public scenario application. The native Fab API handles all authentication.
"""
from pathlib import Path
import datetime
import json
import time
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
CONTROL = ROOT / '.secrets/fab-editor-command.json'
STATUS = ROOT / '.local/evidence/fab-editor-status.json'
api_class = unreal.load_class(None, '/Script/Fab.FabBrowserApi')
assert api_class, 'Fab native API failed to load'
api = unreal.new_object(api_class)
unreal.EditorPythonScripting.set_keep_python_script_alive(True)
api.call_method('OpenInNewTab', args=('',))
last_status = 0.0

def tick(delta):
    global last_status
    if time.monotonic() - last_status < 5:
        return
    last_status = time.monotonic()
    if CONTROL.exists():
        command = json.loads(CONTROL.read_text())
        CONTROL.unlink()
        if command == {'type': 'login'}:
            api.call_method('Login')
        elif command == {'type': 'quit'}:
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
    authenticated = bool(api.call_method('GetAuthToken'))
    state = {'updatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'nativeApiLoaded': True, 'authenticated': authenticated}
    temp = STATUS.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2) + '\n')
    temp.replace(STATUS)

callback = unreal.register_slate_post_tick_callback(tick)
unreal.log('SNR_FAB_EDITOR_OPENED')
