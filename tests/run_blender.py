# Blender USD tests: blender --background --python tests/run_blender.py

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
# Blender may preload the installed add-on before this script runs.
for name in list(sys.modules):
    if name in ('bnglink', 'bngusd') or name.startswith(('bnglink.', 'bngusd.')):
        del sys.modules[name]

suite = unittest.defaultTestLoader.discover(str(ROOT_DIR / 'tests'), pattern='test_usd.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
