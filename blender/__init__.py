import os
import sys

bl_info = {
    'name': 'BNGLink',
    'author': 'BeamNG GmbH',
    'version': (1, 0, 0),
    'blender': (5, 2, 0),
    'location': 'View3D > Sidebar > BNGLink',
    'description': 'Live link with BeamNG, and record/replay USD takes',
    'category': 'Import-Export',
}

addon_dir = os.path.dirname(os.path.realpath(__file__))
for p in (os.path.dirname(addon_dir), addon_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from ui import register, unregister
