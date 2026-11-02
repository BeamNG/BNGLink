import importlib
import os
import sys
import types
import unittest
from unittest import mock

if 'numba' not in sys.modules:
    numba = types.ModuleType('numba')
    numba.njit = lambda *args, **kwargs: lambda function: function
    numba.prange = range
    sys.modules['numba'] = numba


class SettingsTests(unittest.TestCase):
    def reload_settings(self, values):
        names = ['BNGLINK_HOST', 'BNGLINK_RECEIVER_PORT', 'BNGLINK_BEAMNG_PORT', 'BNGLINK_TAKES_DIR']
        with mock.patch.dict(os.environ, values, clear=False):
            for name in names:
                if name not in values:
                    os.environ.pop(name, None)
            import bnglink.settings as settings
            return importlib.reload(settings)

    def test_defaults(self):
        settings = self.reload_settings({})

        self.assertEqual('127.0.0.1', settings.HOST)
        self.assertEqual(6001, settings.RECEIVER_PORT)
        self.assertEqual(('127.0.0.1', 6000), settings.beamng_addr())
        self.assertIn('BNGLink', settings.default_takes_dir())

    def test_environment_overrides(self):
        settings = self.reload_settings({
            'BNGLINK_HOST': '0.0.0.0',
            'BNGLINK_RECEIVER_PORT': '7001',
            'BNGLINK_BEAMNG_PORT': '7000',
            'BNGLINK_TAKES_DIR': r'C:\temp\takes',
        })

        self.assertEqual('0.0.0.0', settings.HOST)
        self.assertEqual(7001, settings.RECEIVER_PORT)
        self.assertEqual(('0.0.0.0', 7000), settings.beamng_addr())
        self.assertEqual(r'C:\temp\takes', settings.default_takes_dir())

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(ValueError):
            self.reload_settings({'BNGLINK_RECEIVER_PORT': 'nope'})


if __name__ == '__main__':
    unittest.main()
