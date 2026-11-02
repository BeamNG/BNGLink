import sys
import types
import unittest

if 'numba' not in sys.modules:
    numba = types.ModuleType('numba')
    numba.njit = lambda *args, **kwargs: lambda function: function
    numba.prange = range
    sys.modules['numba'] = numba

import bnglink
from test_protocol import sync_payload, tiny_init_payload


class LinkSessionTests(unittest.TestCase):
    def setUp(self):
        self.applied = []
        self.logged = []
        self.session = object.__new__(bnglink.LinkSession)
        self.session.decoder = bnglink.BngDecoder()
        self.session.handlers = {'sync': lambda session, syncs: self.applied.append(syncs)}
        self.session.log = self.logged.append
        self.session.stats = None
        self.session.on_payload = None
        self.session.latest_stream = None

    def test_stream_backlog_applies_only_latest_payload(self):
        self.session.on_transfer(bnglink.KIND_STREAM, memoryview(sync_payload(42)))
        self.session.on_transfer(bnglink.KIND_STREAM, memoryview(sync_payload(43)))

        self.assertTrue(self.session.apply_latest_stream())
        self.assertFalse(self.session.apply_latest_stream())
        self.assertEqual([[43]], [[sync['vehicle_id'] for sync in syncs] for syncs in self.applied])

    def test_control_message_is_logged(self):
        self.session.on_transfer(bnglink.KIND_CONTROL, b'init_completed')

        self.assertEqual([bnglink.CONTROL_MESSAGES['init_completed']], self.logged)
        self.assertEqual([], self.applied)

    def test_ping_replies_to_sender(self):
        sent = []
        self.session.comm = types.SimpleNamespace(send=lambda *args: sent.append(args))
        self.session.beamng_addr = ('127.0.0.1', 6000)

        self.session.on_transfer(bnglink.KIND_CONTROL, b'ping')

        self.assertEqual([('127.0.0.1', 6000, b'pong')], sent)
        self.assertEqual([], self.logged)

    def test_lifecycle_controls_clear_session_state(self):
        events = []
        self.session.handlers.update({
            'session_reset': lambda session, data: events.append(('reset', sorted(session.vehicle_states))),
            'vehicle_removed': lambda session, data: events.append(('removed', data['vehicle_id'])),
        })
        self.session.vehicle_states = {42: {}, 43: {}}
        self.session.decoder.vehicle_assets = {42: {}, 43: {}}
        self.session.decoder.assets.store_geometry('geometry', {})
        self.session.decoder.assets.store_texture('texture', {})

        self.session.on_transfer(bnglink.KIND_CONTROL, b'vehicle_removed 42')

        self.assertEqual({43: {}}, self.session.vehicle_states)
        self.assertEqual({43: {}}, self.session.decoder.vehicle_assets)
        self.assertEqual(('removed', 42), events[-1])

        self.session.on_transfer(bnglink.KIND_CONTROL, b'session_reset')

        self.assertEqual({}, self.session.vehicle_states)
        self.assertEqual({}, self.session.decoder.vehicle_assets)
        self.assertEqual({}, self.session.decoder.assets.geometries)
        self.assertEqual({}, self.session.decoder.assets.textures)
        self.assertEqual(('reset', [43]), events[-1])

    def test_failed_init_rolls_back_and_notifies_sender(self):
        sent = []
        removed = []
        self.session.comm = types.SimpleNamespace(send=lambda *args: sent.append(args))
        self.session.beamng_addr = ('127.0.0.1', 6000)
        self.session.vehicle_states = {42: {'partial': True}}
        self.session.handlers.update({
            'init': lambda session, data: (_ for _ in ()).throw(RuntimeError('boom')),
            'vehicle_removed': lambda session, data: removed.append(data['vehicle_id']),
        })

        self.session.on_transfer(bnglink.KIND_BULK, tiny_init_payload(42))

        self.assertEqual([42], removed)
        self.assertEqual({}, self.session.vehicle_states)
        self.assertEqual({}, self.session.decoder.vehicle_assets)
        self.assertEqual(('127.0.0.1', 6000, b'init vehicle 42 failed'), sent[-1])


if __name__ == '__main__':
    unittest.main()
