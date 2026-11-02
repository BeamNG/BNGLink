import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HAS_PXR = importlib.util.find_spec('pxr') is not None
if HAS_PXR:
    from bngusd.layout import skin_to_world
    from bngusd.reader import IDENTITY_M12, Playhead, Take, version_at
    from bngusd.recorder import UsdRecorder
    from bngusd.writer import TakeWriter


@unittest.skipUnless(HAS_PXR, 'pxr module not installed (pip install usd-core)')
class UsdTakeTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = Path(temp_dir.name)
        self.path = self.dir / 'take.usdz'

    def write(self, frames, capture_fps=24, baked_mesh=False, init=None, updates=None):
        # frames: {frame: sync}; updates(segment) runs between the syncs
        writer = TakeWriter(capture_fps, baked_mesh)
        segment = writer.add_vehicle(init or self.init(), 1)
        for frame, sync in frames.items():
            writer.write_frame([sync], frame)
        if updates:
            updates(segment)
        writer.save(self.path)

    def test_skinned_take_roundtrip_keeps_decoded_shapes(self):
        sync = self.sync()
        # Like a single-datagram sync, decoded behind the 17-byte transport header
        sync['node_raw'] = np.frombuffer(b'\0' + sync['node_raw'].tobytes(), dtype='<f4', offset=1).reshape(-1, 3)
        self.write({1: sync})

        take = Take(self.path)
        self.assertEqual(take.fps, 24)
        segment, = take.segments
        init = segment.init
        self.assertEqual(init['vehicle_id'], 42)
        self.assertEqual(len(init['parts']), 1)
        self.assertEqual(init['textures'][0]['data'], b'png')
        part, expected = init['parts'][0], self.part()
        for key in ('verts', 'faces', 'normals', 'uvs', 'tangents', 'ref_idx', 'pz_idx', 'pz_valid', 'coords_clipped'):
            np.testing.assert_allclose(part[key], expected[key], err_msg=key)
        decoded = segment.sync(take.to_capture_frame(1))
        np.testing.assert_allclose(decoded['node_raw'], sync['node_raw'])
        np.testing.assert_allclose(decoded['prop_mats'], sync['prop_mats'])
        self.assertEqual(decoded['origin'], sync['origin'])
        self.assertEqual(decoded['broken_beams'].shape, (0, 2))
        self.assertEqual(decoded['hidden_props'].shape, (0,))
        self.assertEqual(decoded['damaged_switches'].shape, (0, 2))

    def test_legacy_take_converts_node_space_directions(self):
        from pxr import UsdGeom

        init = self.init()
        expected_normals = init['parts'][0]['normals']
        expected_tangents = np.array([[0.0, 1.0, 0.0, -1.0]] * 3, dtype=np.float32)
        init['parts'][0]['tangents'] = expected_tangents
        writer = TakeWriter(24)
        writer.add_vehicle(init, 1)
        writer.write_frame([self.sync()], 1)

        prim = writer.stage.GetPrimAtPath('/BeamNG/Vehicle_42/Body/body')
        UsdGeom.Mesh(prim).GetNormalsAttr().Set(skin_to_world(skin_to_world(expected_normals)))
        legacy_tangents = expected_tangents.copy()
        legacy_tangents[:, :3] = skin_to_world(expected_tangents[:, :3])
        UsdGeom.PrimvarsAPI(prim).GetPrimvar('bng:tangents').Set(legacy_tangents)
        writer.save(self.path)

        part = Take(self.path).segments[0].init['parts'][0]
        np.testing.assert_allclose(part['normals'], expected_normals)
        np.testing.assert_allclose(part['tangents'], expected_tangents)

    def test_texture_updates_are_recorded_as_versions(self):
        plate = {'index': 0, 'name': 'tiny.png', 'mime': 'image/png'}

        def updates(segment):
            segment.update_textures([dict(plate, data=b'png')], 3)
            segment.update_textures([dict(plate, data=b'plate2')], 5)
            segment.update_textures([dict(plate, data=b'plate3')], 8)

        self.write({frame: self.sync() for frame in range(1, 11)}, updates=updates)

        take = Take(self.path)
        versions = take.segments[0].texture_versions[0]
        self.assertEqual([frame for frame, _ in versions], [1, 5, 8])
        self.assertEqual([texture['data'] for _, texture in versions], [b'png', b'plate2', b'plate3'])
        self.assertEqual(take.segments[0].init['textures'][0]['data'], b'png')
        self.assertEqual(versions[1][1]['key'], 'tex:tiny')
        self.assertEqual([version_at(versions, frame) for frame in (0, 4, 6, 10)], [0, 0, 1, 2])
        playhead = Playhead(take.segments[0])
        steps = [(frame, playhead.step(frame).textures) for frame in range(1, 11)]
        self.assertEqual([step for step in steps if step[1]], [(1, {0: 0}), (5, {0: 1}), (8, {0: 2})])

    def test_paints_and_switches_hold_until_changed(self):
        red = self.init()['paints']
        blue = [dict(red[0], baseColor=[0.0, 0.0, 1.0, 1.0], metallic=1.0)]

        def updates(segment):
            segment.write_switches([{'id': 3, 'materialId': 0, 'emissiveScale': 2.0}], 2)
            segment.write_paints(red, 4)
            segment.write_paints(blue, 6)
            segment.write_switches([{'id': 3, 'materialId': 1, 'emissiveScale': 0.0}], 8)

        self.write({frame: self.sync() for frame in range(1, 11)}, updates=updates)

        take = Take(self.path)
        segment = take.segments[0]
        self.assertEqual(segment.paints(5), red)
        self.assertEqual(segment.paints(6), blue)
        self.assertEqual(segment.switches(7), [{'id': 3, 'materialId': 0, 'emissiveScale': 2.0}])
        self.assertEqual(segment.switches(8), [{'id': 3, 'materialId': 1, 'emissiveScale': 0.0}])
        # Unchanged values are not sampled again; a change also holds the old value on the frame before
        self.assertEqual(list(segment.paint_tracks[0]['metallic'].times), [1, 5, 6])
        playhead = Playhead(segment)
        steps = [playhead.step(frame) for frame in range(1, 11)]
        self.assertEqual([frame for frame, step in enumerate(steps, 1) if step.paints is not None], [1, 6])
        self.assertEqual([frame for frame, step in enumerate(steps, 1) if step.switches is not None], [1, 2, 8])

    def test_damage_state_holds_until_changed(self):
        damaged = self.sync()
        damaged['broken_beams'] = np.array([[0, 1]], dtype=np.uint32)
        damaged['hidden_props'] = np.array([1], dtype=np.uint32)
        damaged['damaged_switches'] = np.array([[3, 1]], dtype=np.uint32)
        self.write({1: self.sync(), 2: damaged, 3: damaged})

        take = Take(self.path)
        segment = take.segments[0]
        self.assertEqual(segment.sync(1)['broken_beams'].shape, (0, 2))
        self.assertEqual(segment.sync(2)['broken_beams'].tolist(), [[0, 1]])
        self.assertEqual(segment.sync(3)['hidden_props'].tolist(), [1])
        self.assertEqual(segment.sync(3)['damaged_switches'].tolist(), [[3, 1]])

    def test_take_resamples_to_chosen_playback_fps(self):
        first = self.sync()
        second = self.sync(sim_time=1.0)
        second['node_raw'] = np.full((4, 3), 7.0, dtype=np.float32)
        self.write({1: first, 101: second}, capture_fps=100)

        take = Take(self.path, fps=25)
        self.assertEqual(take.capture_fps, 100.0)
        self.assertEqual(take.frame_range, (1, 26))
        np.testing.assert_allclose(take.segments[0].sync(take.to_capture_frame(13))['node_raw'], first['node_raw'])
        np.testing.assert_allclose(take.segments[0].sync(take.to_capture_frame(26))['node_raw'], second['node_raw'])

    def test_baked_take_samples_points_and_normals(self):
        from pxr import Usd, UsdGeom

        init = self.init()
        init['parts'][0]['normals'] = np.array([[1.0, 0.0, 0.0]] * 3, dtype=np.float32)
        rotated = self.sync()
        # 90 deg about the node-space Z (up) axis
        rotated['node_raw'] = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
        self.write({1: self.sync(), 2: rotated}, baked_mesh=True, init=init)

        stage = Usd.Stage.Open(str(self.path))
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath('/BeamNG/Vehicle_42/Body/body'))
        normals = mesh.GetNormalsAttr()
        self.assertEqual(list(mesh.GetPointsAttr().GetTimeSamples()), [1, 2])
        self.assertEqual(list(normals.GetTimeSamples()), [1, 2])
        # Skin-axis (1, 0, 0) is node-space +X, which the yaw turns into +Y
        np.testing.assert_allclose(normals.Get(2)[0], (0.0, 1.0, 0.0), atol=1e-4)
        # Vertex 2 sits halfway along the X locator axis
        np.testing.assert_allclose(mesh.GetPointsAttr().Get(1)[2], (0.5, 0.0, 0.0), atol=1e-4)
        np.testing.assert_allclose(mesh.GetPointsAttr().Get(2)[2], (0.0, 0.5, 0.0), atol=1e-4)
        with self.assertRaises(RuntimeError):
            Take(self.path)

    def test_recorder_follows_session_messages(self):
        logs = []
        session = SimpleNamespace(handlers={})
        rec = UsdRecorder(logs.append)
        rec.attach(session)

        def send(name, data):
            session.handlers[name](session, data)

        send('init', self.init())
        blue = [dict(self.init()['paints'][0], baseColor=[0.0, 0.0, 1.0, 1.0])]
        # Updates before recording seed the vehicle when it enters the take
        send('paint_update', {'vehicle_id': 42, 'paints': blue})
        send('material_update', {'vehicle_id': 42, 'switches': [{'id': 3, 'materialId': 0, 'emissiveScale': 1.0}]})
        send('material_update', {'vehicle_id': 42, 'switches': [
            {'id': 3, 'materialId': 1, 'emissiveScale': 0.0, 'damaged': True},
            {'id': 3, 'emissiveScale': 0.0, 'damaged': False},
        ]})
        rec.start(self.dir, capture_fps=100.0)
        send('sync', [self.sync(sim_time=10.0)])
        self.assertEqual(rec.frame, 1)
        send('sync', [self.sync(sim_time=10.55)])
        self.assertEqual(rec.frame, 56)
        # A handler error stops the recording without escaping into the session
        send('sync', [{}])
        self.assertFalse(rec.active)
        self.assertIn('USD recorder error in on_sync, recording stopped:', logs)
        self.assertIsNone(rec.stop())

        rec.start(self.path, capture_fps=100.0)
        send('sync', [self.sync(sim_time=12.0)])
        self.assertEqual(rec.stop(), self.path)
        take = Take(self.path)
        self.assertEqual(len(take.segments), 1)
        self.assertEqual(take.segments[0].paints(1), blue)
        self.assertEqual(take.segments[0].switches(1), [{'id': 3, 'materialId': 0, 'emissiveScale': 1.0}])

    def test_recorder_restores_normal_material_after_damage(self):
        session = SimpleNamespace(handlers={})
        rec = UsdRecorder(lambda message: None)
        rec.attach(session)

        def send(name, data):
            session.handlers[name](session, data)

        send('init', self.init())
        send('material_update', {
            'vehicle_id': 42,
            'switches': [{'id': 3, 'materialId': 0, 'emissiveScale': 1.0}],
        })
        rec.start(self.path, capture_fps=1.0)
        send('sync', [self.sync(sim_time=0.0)])
        damaged = self.sync(sim_time=1.0)
        damaged['damaged_switches'] = np.array([[3, 1]], dtype=np.uint32)
        send('sync', [damaged])
        send('sync', [self.sync(sim_time=2.0)])
        rec.stop()

        take = Take(self.path)
        segment = take.segments[0]
        self.assertEqual([segment.switches(frame)[0]['materialId'] for frame in (1, 2, 3)], [0, 1, 0])

    def test_reinitialized_vehicle_starts_a_new_segment(self):
        session = SimpleNamespace(handlers={})
        rec = UsdRecorder(lambda message: None)
        rec.attach(session)
        session.handlers['init'](session, self.init())
        rec.start(self.path, capture_fps=100.0)
        session.handlers['sync'](session, [self.sync(sim_time=0.0)])
        session.handlers['init'](session, self.init())
        session.handlers['sync'](session, [self.sync(sim_time=0.5)])
        rec.stop()

        take = Take(self.path)
        self.assertEqual([segment.vehicle_id for segment in take.segments], [42, 42])
        self.assertEqual([segment.nodes.times[0] for segment in take.segments], [1, 51])
        self.assertEqual([segment.end_frame for segment in take.segments], [2, 52])

    def init(self):
        return {
            'vehicle_id': 42,
            'geometry_key': 'geom:tiny',
            'node_ids': [11, 12, 13, 14],
            'parts': [self.part()],
            'props': [self.prop()],
            'materials': [{'id': 0, 'name': 'mat', 'transparent': False, 'zWrite': False,
                           'alphaTest': False, 'doubleSided': False, 'layers': []}],
            'textures': [{'key': 'tex:tiny', 'name': 'tiny.png', 'mime': 'image/png', 'data': b'png'}],
            'paints': [{'baseColor': [1.0, 0.0, 0.0, 1.0], 'metallic': 0.0, 'roughness': 0.5,
                        'clearcoat': 0.0, 'clearcoatRoughness': 1.0}],
        }

    def sync(self, sim_time=0.0):
        return {
            'vehicle_id': 42,
            'sim_time': sim_time,
            'node_raw': np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32),
            'origin': (1.0, 2.0, 3.0),
            'prop_mats': np.array([[1.0, 0.0, 0.0, 4.0, 0.0, 1.0, 0.0, 5.0, 0.0, 0.0, 1.0, 6.0]], dtype=np.float32),
            'broken_beams': np.empty((0, 2), dtype=np.uint32),
            'hidden_props': np.empty(0, dtype=np.uint32),
            'damaged_switches': np.empty((0, 2), dtype=np.uint32),
        }

    def part(self):
        nodes = np.array([[0, 1, 2, 3], [0, 1, 2, -1], [0, 1, 2, 3]], dtype=np.int32)
        coords = np.array([[0.0, 0.0, 0.0], [0.25, 0.25, 0.0], [0.5, 0.0, 0.0]], dtype=np.float32)
        coords_clipped = np.clip(coords, 0.0, 1.0)
        return {
            'name': 'body',
            'verts': np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
            'faces': np.array([[0, 1, 2]], dtype=np.uint32),
            'normals': np.array([[0.0, 0.0, 1.0]] * 3, dtype=np.float32),
            'tangents': np.array([[1.0, 0.0, 0.0, 1.0]] * 3, dtype=np.float32),
            'uvs': np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 0.25]], dtype=np.float32),
            'uvs2': np.zeros((3, 2), dtype=np.float32),
            'material_ids': np.array([0], dtype=np.uint32),
            'switch_ids': np.array([-1], dtype=np.int32),
            'ref_idx': nodes[:, 0],
            'px_idx': nodes[:, 1],
            'py_idx': nodes[:, 2],
            'pz_idx': np.where(nodes[:, 3] >= 0, nodes[:, 3], nodes[:, 0]),
            'pz_valid': nodes[:, 3] >= 0,
            'coords_clipped': coords_clipped,
            'coords_residual': coords - coords_clipped,
        }

    def prop(self):
        part = self.part()
        prop = {
            key: part[key]
            for key in ('name', 'verts', 'faces', 'normals', 'tangents', 'uvs', 'uvs2', 'material_ids', 'switch_ids')
        }
        prop.update(pid=1, bind_matrix=IDENTITY_M12, live_init_matrix=IDENTITY_M12)
        return prop


if __name__ == '__main__':
    unittest.main()
