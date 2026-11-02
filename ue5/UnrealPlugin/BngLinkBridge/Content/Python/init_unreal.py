# Unreal runs this on editor startup, before a utility widget tab can be spawned.
import os
import sys
import traceback

import unreal

python_dir = os.path.dirname(__file__)
if os.path.isfile(os.path.join(python_dir, 'ui.py')):
    paths = (python_dir,)
else:
    # The source plugin is nested four folders below the ue5 adapter.
    ue5_dir = os.path.realpath(os.path.join(python_dir, '..', '..', '..', '..'))
    paths = (os.path.dirname(ue5_dir), ue5_dir)
for path in paths:
    if path not in sys.path:
        sys.path.insert(0, path)


def _open(_delta_time):
    unreal.unregister_slate_post_tick_callback(tick_handle)
    try:
        import ui
        ui.open_ui()
    except Exception:
        unreal.log_error('BNGLink Unreal: failed to open the Editor Utility Widget')
        traceback.print_exc()


tick_handle = unreal.register_slate_post_tick_callback(_open)
