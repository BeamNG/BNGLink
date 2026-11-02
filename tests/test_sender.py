# End to end: the real Lua sender (LuaJIT through lupa) against engine_mock.lua, decoded by bnglink.

import unittest

import lua_sender

VEHICLE_EXTENSION_PATH = lua_sender.ROOT / 'beamng' / 'vlua' / 'BNGLink.lua'


@unittest.skipIf(lua_sender.LuaRuntime is None, 'needs lupa (pip install lupa)')
class SenderTests(unittest.TestCase):
    def start(self, vehicle_order=None):
        sender = lua_sender.LuaSender()
        if vehicle_order:
            sender.execute(f'harness.vehicleOrder = {vehicle_order}')
        sender.lua.globals().extensions.load('BNGLink_BNGLink')
        bng = sender.ext('BNGLink_BNGLink')
        bng.initContent()
        sender.run(60)
        return sender, bng

    def test_scenario_delivers_every_message_kind(self):
        sender = lua_sender.LuaSender()
        lua_sender.run_scenario(sender)

        self.assertEqual([], sender.errors())
        counts = {}
        for name, _ in sender.receiver.messages:
            counts[name] = counts.get(name, 0) + 1
        for name in ('init', 'sync', 'paint_update', 'texture_update', 'material_update', 'control'):
            self.assertGreater(counts.get(name, 0), 0, name)
        self.assertTrue(sender.receiver.dropped)

    def test_repeated_vehicle_reuses_cached_geometry_and_textures(self):
        sender, _ = self.start()

        first, second = sender.messages('init')[:2]
        self.assertEqual(first['geometry_key'], second['geometry_key'])
        self.assertIs(first['parts'][0]['verts'], second['parts'][0]['verts'])
        self.assertEqual(len(first['textures']), len(second['textures']))

    def test_session_reset_and_vehicle_removal_controls(self):
        sender, bng = self.start()
        self.assertIn('session_reset', sender.messages('control'))

        bng.startRealtime()
        bng.onVehicleDestroyed(10)
        sender.run(2)

        self.assertIn('vehicle_removed 10', sender.messages('control'))
        self.assertTrue(all(
            vehicle['vehicle_id'] != 10
            for syncs in sender.messages('sync')[-2:]
            for vehicle in syncs
        ))

    def test_sync_matches_init_layout(self):
        sender, bng = self.start()
        bng.startRealtime()
        sender.run(5)

        inits = {init['vehicle_id']: init for init in sender.messages('init')}
        syncs = sender.messages('sync')
        self.assertTrue(syncs)
        for vehicles in syncs:
            for vehicle in vehicles:
                init = inits[vehicle['vehicle_id']]
                self.assertEqual(len(init['node_ids']), len(vehicle['node_raw']))
                self.assertEqual(len(init['props']), len(vehicle['prop_mats']))
                self.assertGreater(vehicle['sim_time'], 0.0)

    def test_damage_state_is_included_in_sync(self):
        sender, bng = self.start('{10}')
        bng.startRealtime()
        sender.execute(
            "extensions.BNGLink_BNGLink.damageCallback(10, {"
            "brokenBeams={{100, 101}}, "
            "switches={{id=99, material='headlight_on'}}, "
            "hiddenProps={1}})"
        )
        sender.run(2)

        sync = sender.messages('sync')[-1][0]
        self.assertEqual([[0, 1]], sync['broken_beams'].tolist())
        self.assertEqual([1], sync['hidden_props'].tolist())
        self.assertEqual([[99, 7]], sync['damaged_switches'].tolist())
        update = sender.messages('material_update')[-1]
        self.assertEqual(0, update['switches'][0]['emissiveScale'])
        self.assertTrue(update['switches'][0]['damaged'])

        update_count = len(sender.messages('material_update'))
        sender.execute(
            "extensions.BNGLink_BNGLink.damageCallback(10, {"
            "brokenBeams={{100, 101}}, "
            "switches={{id=99, material='headlight_on'}}, "
            "hiddenProps={1}})"
        )
        sender.run(2)
        self.assertEqual(update_count, len(sender.messages('material_update')))

        sender.execute(
            "extensions.BNGLink_BNGLink.damageCallback(10, {"
            "brokenBeams={}, switches={}, hiddenProps={}})"
        )
        sender.run(2)
        sync = sender.messages('sync')[-1][0]
        self.assertEqual([], sync['broken_beams'].tolist())
        self.assertEqual([], sync['damaged_switches'].tolist())
        self.assertNotIn('materialId', sender.messages('material_update')[-1]['switches'][0])

    def test_vehicle_reset_clears_damage_before_beamstate_reset(self):
        lua = lua_sender.LuaRuntime(encoding='latin-1')
        lua.execute("""
            queued = {}
            objectId = 10
            obj = {
              broken = true,
              beamIsBroken = function(self) return self.broken end,
              queueGameEngineLua = function(self, command) queued[#queued + 1] = command end,
            }
            v = {data = {
              beams = {
                [1] = {
                  cid = 1, id1 = 100, id2 = 101,
                  deformSwitches = {[7] = {dmgMat = 'glass_dmg'}},
                },
              },
              props = {},
            }}
            beamstate = {deformGroupsTriggerBeam = {glass = 1}}
            tableKeysSorted = function(values)
              local keys = {}
              for key in pairs(values) do keys[#keys + 1] = key end
              table.sort(keys)
              return keys
            end
            serialize = function(data)
              return tostring(#data.brokenBeams) .. ':' .. tostring(#data.switches)
            end
        """)
        extension = lua.execute(f'return dofile("{VEHICLE_EXTENSION_PATH.as_posix()}")')

        extension.onExtensionLoaded()
        extension.updateGFX(0)
        self.assertTrue(lua.globals().queued[1].endswith('1:1)'))

        lua.globals().obj.broken = False
        extension.onReset()
        extension.updateGFX(0)
        self.assertTrue(lua.globals().queued[2].endswith('0:0)'))

    def test_ui_state_and_vehicle_sync_toggle(self):
        sender, bng = self.start()

        state = bng.getUiState()
        self.assertFalse(state['connected'])
        self.assertEqual('127.0.0.1:6001', state['connectedTo'])
        self.assertEqual('Mock Vehicle', state['vehicles'][1]['name'])
        self.assertTrue(state['vehicles'][1]['enabled'])
        self.assertTrue(state['vehicles'][1]['driven'])

        self.assertFalse(bng.setConnectionSettings(sender.lua.table_from({'beamngPort': 0, 'receiverHost': ''})))
        self.assertTrue(bng.setConnectionSettings(sender.lua.table_from({
            'beamngPort': 6000,
            'receiverHost': '127.0.0.3',
            'receiverPort': 6101,
        })))
        self.assertEqual('127.0.0.3:6101', bng.getUiState()['connectedTo'])

        controls = len(sender.messages('control'))
        bng.setVehicleSyncEnabled(10, False)
        sender.run(5)
        self.assertFalse(bng.getUiState()['vehicles'][1]['enabled'])
        self.assertEqual(controls, len(sender.messages('control')))

        bng.startRealtime()
        sender.run(5)
        self.assertTrue(bng.getUiState()['connected'])

        resync_controls = len(sender.messages('control'))
        bng.resync()
        sync_start = len(sender.messages('sync'))
        sender.run(60)

        controls_after_resync = sender.messages('control')[resync_controls:]
        self.assertTrue(bng.getUiState()['liveSync'])
        self.assertIn('realtime_stopped', controls_after_resync)
        self.assertIn('realtime_started', controls_after_resync)
        self.assertGreater(len(sender.messages('sync')), sync_start)
        self.assertTrue(all(
            vehicle['vehicle_id'] != 10
            for syncs in sender.messages('sync')[sync_start:]
            for vehicle in syncs
        ))

    def test_failed_init_does_not_block_next_vehicle(self):
        sender = lua_sender.LuaSender()
        sender.receiver.failed_inits.add(10)
        sender.lua.globals().extensions.load('BNGLink_BNGLink')
        bng = sender.ext('BNGLink_BNGLink')

        bng.initContent()
        sender.run(60)

        ready_ids = [init['vehicle_id'] for init in sender.messages('init')]
        self.assertIn(11, ready_ids)
        bng.startRealtime()
        sender.run(2)
        self.assertTrue(all(
            vehicle['vehicle_id'] != 10
            for syncs in sender.messages('sync')
            for vehicle in syncs
        ))

    def test_realtime_bench_counts_sent_frames(self):
        sender, bng = self.start()
        realtime = sender.ext('BNGLink_Realtime')
        bng.startRealtime()
        realtime.resetBench()
        sender.run(10)

        bench = realtime.readBench()
        self.assertEqual(10, bench.frames)
        self.assertGreater(bench.bytesFrame, 0)
        self.assertEqual(10, bench.updateFrames)
        realtime.disableBench()

    def test_short_ack_is_ignored(self):
        sender, _ = self.start()
        sender.harness.push('BNGA')
        sender.run(1)

        self.assertEqual([], sender.errors())

    def cef_updates_over_two_seconds(self, smooth_dashboards):
        sender, bng = self.start('{10}')
        bng.startRealtime()
        bng.setSmoothDashboards(smooth_dashboards)
        self.assertEqual(smooth_dashboards, bng.getUiState()['smoothDashboards'])

        start = len(sender.messages('texture_update'))
        for frame in range(120):
            sender.execute(f'harness.versions.cef = {frame + 2}')
            sender.run(1)

        updates = sender.messages('texture_update')[start:]
        self.assertTrue(all(update['vehicle_id'] == 10 for update in updates))
        self.assertEqual([], sender.errors())
        return len(updates)

    def test_smooth_cef_dashboard_updates_at_24_hz(self):
        self.assertAlmostEqual(48, self.cef_updates_over_two_seconds(True), delta=1)

    def test_cef_dashboard_updates_at_plate_rate_by_default(self):
        self.assertAlmostEqual(8, self.cef_updates_over_two_seconds(False), delta=1)

    def test_unchanged_cef_dashboard_is_not_resent(self):
        sender, bng = self.start('{10}')
        bng.startRealtime()

        sender.execute('harness.versions.cef = 2')
        sender.run(20)
        start = len(sender.messages('texture_update'))
        sender.run(120)

        self.assertEqual(0, len(sender.messages('texture_update')) - start)
        self.assertEqual([], sender.errors())


if __name__ == '__main__':
    unittest.main()
