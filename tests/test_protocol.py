import json
import importlib
import struct
import sys
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if 'numba' not in sys.modules:
    numba = types.ModuleType('numba')
    numba.njit = lambda *args, **kwargs: lambda function: function
    numba.prange = range
    sys.modules['numba'] = numba

import bnglink
import bnglink.transport as transport


class MaterialStateTests(unittest.TestCase):
    def test_damage_variants_follow_switch_state(self):
        materials = [
            {'id': 0, 'transparent': True, 'layers': [{'opacityFactor': 1.0}]},
            {'id': 1, 'transparent': True, 'layers': [{'opacityFactor': 0.0}]},
            {'id': 2, 'transparent': False, 'layers': [{'opacityFactor': 0.0}]},
        ]
        material_ids = np.array([0, 1, 1, 1, 2], dtype=np.int32)
        switch_ids = np.array([4, 4, 5, -1, 4], dtype=np.int32)

        variants = bnglink.build_damage_variant_index(
            material_ids, switch_ids, materials)
        self.assertEqual({4, 5}, variants.keys())
        np.testing.assert_array_equal([1], variants[4])
        np.testing.assert_array_equal([2], variants[5])

        state = bnglink.MaterialState()
        hidden = np.zeros(len(material_ids), dtype=bool)
        self.assertTrue(state.update_hidden_variants(variants, hidden))
        np.testing.assert_array_equal(
            [False, True, True, False, False], hidden)

        changed = state.replace_damage(np.array([[4, 7]], dtype=np.uint32))
        self.assertTrue(state.update_hidden_variants(variants, hidden, changed))
        np.testing.assert_array_equal(
            [False, False, True, False, False], hidden)

        changed = state.replace_damage(np.empty((0, 2), dtype=np.uint32))
        self.assertTrue(state.update_hidden_variants(variants, hidden, changed))
        np.testing.assert_array_equal(
            [False, True, True, False, False], hidden)

    def test_damage_overrides_and_restores_normal_material(self):
        state = bnglink.MaterialState()
        self.assertEqual({3}, state.update([{'id': 3, 'materialId': 1, 'emissiveScale': 2.0}]))
        self.assertEqual((1, 2.0), state.resolve(3))

        self.assertEqual({3}, state.replace_damage(np.array([[3, 4]], dtype=np.uint32)))
        self.assertEqual((4, 0.0), state.resolve(3))
        self.assertEqual(set(), state.update([{'id': 3, 'materialId': 2, 'emissiveScale': 0.5}]))
        self.assertEqual((4, 0.0), state.resolve(3))

        self.assertEqual({3}, state.replace_damage(np.empty((0, 2), dtype=np.uint32)))
        self.assertEqual((2, 0.5), state.resolve(3))

    def test_damage_update_only_reports_changed_switches(self):
        state = bnglink.MaterialState()
        state.replace_damage(np.array([[3, 4], [5, 6]], dtype=np.uint32))

        changed = state.replace_damage(np.array([[3, 4], [5, 7], [8, 9]], dtype=np.uint32))

        self.assertEqual({5, 8}, changed)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))


def packet(kind, transfer_id, chunk, count, payload):
    return bnglink.HEADER.pack(
        bnglink.HEADER_MAGIC, kind, transfer_id, chunk, count
    ) + payload


def framed(meta, data=b''):
    encoded = json.dumps(meta).encode()
    return struct.pack('<I', len(encoded)) + encoded + data


def sync_payload(vehicle_id, sim_time=1.5):
    return (struct.pack('<2I', 1, 1) + struct.pack('<d', sim_time)
            + struct.pack('<2I', vehicle_id, 1) + np.zeros(3, dtype='<f4').tobytes()
            + struct.pack('<3f', 0.0, 0.0, 0.0)
            + struct.pack('<5I', 0, 0, 0, 0, 0))


def tiny_init_payload(vehicle_id=42, include_geometry=True, include_texture=True, include_props=True):
    geometry = {'key': 'geom:tiny', 'included': include_geometry}
    meta = {
        'vehicleId': vehicle_id,
        'geometry': geometry,
        'props': [],
        'materials': [{'id': 0, 'name': 'mat', 'transparent': False, 'zWrite': False,
                       'alphaTest': False, 'doubleSided': False, 'layers': []}],
        'textures': [{'key': 'tex:shared', 'name': 'shared.png', 'mime': 'image/png'}],
        'paints': [],
    }
    payload = b''
    if include_geometry:
        meta['nodeIds'] = [1]
        geometry['parts'] = [{'name': 'body', 'uniqueVertCount': 1, 'indexCount': 3}]
        geometry['props'] = [{'name': 'mirror', 'vertCount': 1, 'indexCount': 3}]
        meta['props'] = [{'pid': 7, 'bindMatrix': list(range(12)), 'liveInitMatrix': list(range(12, 24))}]
        skin = np.zeros(1, dtype=bnglink.SKIN_DTYPE)
        skin['nodes'][0] = [1, -1, -1, -1]
        payload += (
            np.zeros(3, dtype='<f4').tobytes()
            + np.zeros(3, dtype='<f4').tobytes()
            + np.zeros(4, dtype='<f4').tobytes()
            + np.zeros(3, dtype='<u4').tobytes()
            + skin.tobytes()
            + np.zeros(6, dtype='<f4').tobytes()
            + np.zeros(6, dtype='<f4').tobytes()
            + np.zeros(1, dtype='<u4').tobytes()
            + np.zeros(1, dtype='<u4').tobytes()
        )
        if include_props:
            payload += (
                np.zeros(3, dtype='<f4').tobytes()
                + np.zeros(3, dtype='<f4').tobytes()
                + np.zeros(4, dtype='<f4').tobytes()
                + np.zeros(3, dtype='<u4').tobytes()
                + np.zeros(6, dtype='<f4').tobytes()
                + np.zeros(6, dtype='<f4').tobytes()
                + np.zeros(1, dtype='<u4').tobytes()
                + np.zeros(1, dtype='<u4').tobytes()
            )
        else:
            # Lua's JSON encoder writes empty tables as objects
            geometry['props'] = {}
            meta['props'] = {}
    else:
        meta['props'] = [{'pid': 7, 'bindMatrix': list(range(12)), 'liveInitMatrix': list(range(12, 24))}]
    if include_texture:
        meta['textures'][0]['offset'] = 0
        meta['textures'][0]['size'] = 4
        payload += b'tex0'
    return framed(meta, payload)


class UdpCommTests(unittest.TestCase):
    def setUp(self):
        self.comm = object.__new__(bnglink.UdpComm)
        self.comm.sock = FakeSocket()
        self.comm.peer_addr = None
        self.comm.chunk_states = {}
        self.comm.buffered_bytes = 0
        self.comm.last_completed_transfer_ids = {}
        self.comm.highest_transfer_ids = {}
        self.received = []
        self.address = ('127.0.0.1', 6000)

    def handle(self, kind, transfer_id, chunk, count, payload):
        self.comm.handle(
            packet(kind, transfer_id, chunk, count, payload),
            self.address,
            lambda received_kind, data: self.received.append((received_kind, data)),
        )

    def test_interleaved_channels_are_assembled_independently(self):
        self.handle(bnglink.KIND_BULK, 10, 0, 2, b'bulk-')
        self.handle(bnglink.KIND_UPDATE, 11, 0, 2, b'update-')
        self.handle(bnglink.KIND_BULK, 10, 1, 2, b'data')
        self.handle(bnglink.KIND_UPDATE, 11, 1, 2, b'data')

        self.assertEqual([
            (bnglink.KIND_BULK, b'bulk-data'),
            (bnglink.KIND_UPDATE, b'update-data'),
        ], self.received)
        self.assertEqual(2, len(self.comm.sock.sent))

    def test_older_chunks_do_not_replace_newer_transfer(self):
        self.handle(bnglink.KIND_STREAM, 11, 0, 2, b'new-')
        self.handle(bnglink.KIND_STREAM, 10, 0, 2, b'old-')
        self.handle(bnglink.KIND_STREAM, 11, 1, 2, b'data')

        self.assertEqual([(bnglink.KIND_STREAM, b'new-data')], self.received)

    def test_sender_restart_clears_completed_ids(self):
        self.handle(bnglink.KIND_UPDATE, 5, 0, 1, b'old-session')
        self.handle(bnglink.KIND_UPDATE, 1, 0, 1, b'new-session')

        self.assertEqual([
            (bnglink.KIND_UPDATE, b'old-session'),
            (bnglink.KIND_UPDATE, b'new-session'),
        ], self.received)

    def test_stream_single_chunk_is_delivered_as_buffer_view(self):
        self.handle(bnglink.KIND_STREAM, 7, 0, 1, b'sync-payload')

        kind, data = self.received[0]
        self.assertEqual(bnglink.KIND_STREAM, kind)
        self.assertEqual(b'sync-payload', bytes(data))

    def test_malformed_datagrams_are_ignored(self):
        self.comm.handle(b'BNG1', self.address, lambda kind, data: self.received.append((kind, data)))
        self.handle(9, 1, 0, 1, b'unknown-kind')
        self.handle(bnglink.KIND_BULK, 2, 1, 1, b'invalid-chunk')
        self.handle(bnglink.KIND_BULK, 3, 0, 0, b'invalid-count')
        self.handle(bnglink.KIND_BULK, 4, 0, 1, b'x' * (transport.MAX_CHUNK_SIZE + 1))

        self.assertEqual([], self.received)
        self.assertEqual([], self.comm.sock.sent)

    def test_stale_partial_transfer_is_discarded(self):
        self.handle(bnglink.KIND_BULK, 10, 0, 2, b'partial')
        channel = (self.address, bnglink.KIND_BULK)
        self.comm.chunk_states[channel]['updated_at'] = 0.0

        self.comm._expire_stale(transport.PARTIAL_TRANSFER_TIMEOUT)

        self.assertEqual({}, self.comm.chunk_states)
        self.assertEqual(0, self.comm.buffered_bytes)

    def test_bind_error_names_endpoint(self):
        first = bnglink.UdpComm('127.0.0.1', 0)
        host, port = first.sock.getsockname()
        try:
            with self.assertRaises(OSError) as caught:
                bnglink.UdpComm(host, port)
        finally:
            first.close()

        self.assertIn(f'{host}:{port}', str(caught.exception))


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.decoder = bnglink.BngDecoder()

    def test_material_update(self):
        self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload())
        payload = framed({
            'type': 'material_update',
            'vehicleId': 42,
            'switches': [{'id': 3, 'materialId': 0, 'emissiveScale': 0.25}],
        })

        self.assertEqual(
            ('material_update', {
                'vehicle_id': 42,
                'switches': [{'id': 3, 'materialId': 0, 'emissiveScale': 0.25}],
            }),
            self.decoder.decode(bnglink.KIND_UPDATE, payload),
        )
        reset = framed({
            'type': 'material_update',
            'vehicleId': 42,
            'switches': [{'id': 3, 'emissiveScale': 0.0}],
        })
        self.assertEqual(
            [{'id': 3, 'emissiveScale': 0.0}],
            self.decoder.decode(bnglink.KIND_UPDATE, reset)[1]['switches'],
        )

    def test_init_without_props(self):
        kind, data = self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload(include_props=False))

        self.assertEqual('init', kind)
        self.assertEqual(1, len(data['parts']))
        self.assertEqual([], data['props'])

    def test_texture_update_slices_binary_payload(self):
        self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload())
        payload = framed({
            'type': 'texture_update',
            'vehicleId': 42,
            'textures': [
                {'index': 0, 'offset': 0, 'size': 3, 'name': 'display.png', 'mime': 'image/png'},
            ],
        }, b'bcd')

        kind, data = self.decoder.decode(bnglink.KIND_BULK, payload)

        self.assertEqual('texture_update', kind)
        self.assertEqual(42, data['vehicle_id'])
        self.assertEqual(b'bcd', data['textures'][0]['data'])

    def test_framed_payload_validates_metadata_length(self):
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_BULK, b'\x01\x00')
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_BULK, struct.pack('<I', 10) + b'{}')

    def test_texture_update_rejects_invalid_range_and_index(self):
        self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload())
        bad_range = framed({
            'type': 'texture_update',
            'vehicleId': 42,
            'textures': [
                {'index': 0, 'offset': 1, 'size': 3, 'name': 'display.png', 'mime': 'image/png'},
            ],
        }, b'abcd')
        bad_index = framed({
            'type': 'texture_update',
            'vehicleId': 42,
            'textures': [
                {'index': 1, 'offset': 0, 'size': 4, 'name': 'display.png', 'mime': 'image/png'},
            ],
        }, b'abcd')

        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_BULK, bad_range)
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_BULK, bad_index)

    def test_decode_sync_reads_buffer_protocol_payload(self):
        nodes = np.arange(6, dtype='<f4')
        payload = (struct.pack('<2I', 3, 1) + struct.pack('<d', 2.5)
                   + struct.pack('<2I', 42, 2) + nodes.tobytes()
                   + struct.pack('<3f', 1.0, 2.0, 3.0)
                   + struct.pack('<I', 0)
                   + struct.pack('<4I', 4, 1, 0, 1)
                   + struct.pack('<2I', 1, 7)
                   + struct.pack('<3I', 1, 99, 0))

        kind, vehicles = self.decoder.decode(bnglink.KIND_STREAM, memoryview(payload))

        self.assertEqual('sync', kind)
        self.assertEqual(42, vehicles[0]['vehicle_id'])
        self.assertEqual(2.5, vehicles[0]['sim_time'])
        self.assertEqual([0, 1, 2], vehicles[0]['node_raw'][0].tolist())
        self.assertEqual([3, 4, 5], vehicles[0]['node_raw'][1].tolist())
        self.assertEqual(4, vehicles[0]['damage_revision'])
        self.assertEqual([[0, 1]], vehicles[0]['broken_beams'].tolist())
        self.assertEqual([7], vehicles[0]['hidden_props'].tolist())
        self.assertEqual([[99, 0]], vehicles[0]['damaged_switches'].tolist())

    def test_decode_sync_rejects_truncated_and_trailing_data(self):
        payload = sync_payload(42)
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_STREAM, payload[:-1])
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_STREAM, payload + b'\0')

    def test_mesh_block_drops_degenerate_faces_and_decodes_switch_ids(self):
        values = (
            np.zeros(18, dtype='<f4').tobytes()
            + np.zeros(18, dtype='<f4').tobytes()
            + np.zeros(24, dtype='<f4').tobytes()
            + np.array([0, 1, 2, 3, 3, 4], dtype='<u4').tobytes()
            + np.arange(12, dtype='<f4').tobytes()
            + np.zeros(12, dtype='<f4').tobytes()
            + np.array([2, 5], dtype='<u4').tobytes()
            + np.array([4, 0], dtype='<u4').tobytes()
        )
        reader = bnglink.decoder._Reader(values)

        mesh, skin = bnglink.decoder._read_mesh(reader, 6, 6, False)

        self.assertIsNone(skin)
        self.assertEqual([[0, 1, 2]], mesh['faces'].tolist())
        self.assertEqual([[0, 1], [2, 3], [4, 5]], mesh['uvs'].tolist())
        self.assertEqual([2], mesh['material_ids'].tolist())
        self.assertEqual([3], mesh['switch_ids'].tolist())
        self.assertEqual(len(values), reader.offset)

    def test_mesh_block_moves_normals_and_tangents_to_vertex_axes(self):
        values = (
            np.array([0.0, 1.0, 0.0], dtype='<f4').tobytes()
            + np.array([0.0, 0.0, 1.0], dtype='<f4').tobytes()
            + np.array([0.0, 1.0, 0.0, -1.0], dtype='<f4').tobytes()
        )

        mesh, _ = bnglink.decoder._read_mesh(bnglink.decoder._Reader(values), 1, 0, False)

        self.assertEqual([[0.0, 1.0, 0.0]], mesh['verts'].tolist())
        self.assertEqual([[0.0, 1.0, 0.0]], mesh['normals'].tolist())
        self.assertEqual([[0.0, 0.0, -1.0, -1.0]], mesh['tangents'].tolist())

    def test_cached_init_resolves_from_asset_cache(self):
        self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload())

        kind, data = self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload(43, False, False))

        self.assertEqual('init', kind)
        self.assertEqual(43, data['vehicle_id'])
        self.assertEqual([1], data['node_ids'])
        self.assertEqual(b'tex0', data['textures'][0]['data'])
        self.assertEqual(7, data['props'][0]['pid'])
        self.assertEqual(list(range(12)), data['props'][0]['bind_matrix'])

    def test_cached_init_returns_fresh_part_dicts(self):
        _, first = self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload())
        first['parts'][0].pop('verts')

        _, second = self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload(43, False, False))

        self.assertIn('verts', second['parts'][0])

    def test_missing_cached_asset_raises(self):
        with self.assertRaises(bnglink.MissingAsset):
            self.decoder.decode(bnglink.KIND_BULK, tiny_init_payload(43, False, False))

    def test_init_rejects_invalid_material_and_skin_indices(self):
        invalid_material = bytearray(tiny_init_payload())
        meta_len = struct.unpack_from('<I', invalid_material, 0)[0]
        meta = json.loads(invalid_material[4:4 + meta_len])
        meta['materials'][0]['baseColorTexture'] = 1
        with self.assertRaises(ValueError):
            self.decoder.decode(
                bnglink.KIND_BULK,
                framed(meta, invalid_material[4 + meta_len:]),
            )

        meta, data = self.decoder.decode_framed(tiny_init_payload())
        meta['nodeIds'] = [2]
        with self.assertRaises(ValueError):
            self.decoder.decode(bnglink.KIND_BULK, framed(meta, data))


class SkinningBatchTests(unittest.TestCase):
    def setUp(self):
        self.decoder = bnglink.BngDecoder()

    def build_part(self, name, nodes, coords):
        skin = np.zeros(len(nodes), dtype=bnglink.SKIN_DTYPE)
        skin['nodes'] = nodes
        skin['coords'] = coords
        return self.decoder.build_skinning(
            name, np.zeros((len(nodes), 3), np.float32),
            np.zeros((1, 3), np.uint32), skin,
            {10: 0, 11: 1, 12: 2, 13: 3})

    def test_merge_empty(self):
        self.assertEqual((None, [], 0), bnglink.merge_part_skins([]))

    def test_merged_deform_matches_per_part_deform(self):
        parts = [
            self.build_part('a',
                            [(10, 11, 12, 13), (11, 10, 13, 12), (12, 13, 10, 11), (13, 12, 11, 10)],
                            [(0.1, 0.2, 0.3), (1.1, 0.2, 0.3), (0.1, 1.2, 0.3), (0.1, 0.2, 1.3)]),
            self.build_part('b',
                            [(10, -1, -1, -1), (11, -1, -1, -1)],
                            [(0.5, 0.5, 0.5), (0.25, 0.75, 0.25)]),
        ]

        merged, ranges, total = bnglink.merge_part_skins(parts)

        self.assertEqual([(0, 4), (4, 6)], ranges)
        self.assertEqual(6, total)

        node_pos = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], np.float32)
        per_part = []
        for part in parts:
            out = np.empty((part['ref_idx'].shape[0], 3), np.float32)
            self.decoder.deform_body(node_pos, part, out)
            per_part.append(out)
        batched = np.empty((total, 3), np.float32)
        self.decoder.deform_body(node_pos, merged, batched)

        np.testing.assert_array_equal(np.concatenate(per_part), batched)

    def test_broken_beam_hides_faces_using_both_locator_nodes(self):
        part = {
            'faces': np.array([[0, 1, 2], [1, 2, 3]], dtype=np.uint32),
            'ref_idx': np.array([0, 4, 8, 12], dtype=np.int32),
            'px_idx': np.array([1, 5, 9, 13], dtype=np.int32),
            'py_idx': np.array([2, 6, 10, 14], dtype=np.int32),
            'pz_idx': np.array([3, 7, 11, 15], dtype=np.int32),
            'pz_valid': np.ones(4, dtype=bool),
        }
        locator_nodes = bnglink.face_locator_nodes([part])
        face_node_index = bnglink.build_face_node_index(locator_nodes)
        hidden = np.zeros(2, dtype=bool)
        broken = {(0, 4)}

        self.assertTrue(bnglink.update_hidden_faces(face_node_index, set(), broken, hidden))
        self.assertEqual([True, False], hidden.tolist())
        self.assertFalse(bnglink.update_hidden_faces(face_node_index, broken, broken, hidden))
        self.assertTrue(bnglink.update_hidden_faces(face_node_index, broken, set(), hidden))
        self.assertEqual([False, False], hidden.tolist())


def quat_matrix(q):
    w, x, y, z = q
    return np.array((
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    ))


def axis_rotation(axis, angle):
    axis = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    k = np.array(((0, -axis[2], axis[1]), (axis[2], 0, -axis[0]), (-axis[1], axis[0], 0)))
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * k @ k


class RigidifyTests(unittest.TestCase):
    TOLERANCE = 1e-3

    def setUp(self):
        self.decoder = bnglink.BngDecoder()
        rng = np.random.default_rng(7)
        self.rest = rng.uniform(-2.0, 2.0, (12, 3)).astype(np.float32)
        # Part 0 depends on nodes 0..5, part 1 on nodes 6..11
        self.skin = {
            'ref_idx': np.array([0, 1, 6, 7], np.int32),
            'px_idx': np.array([2, 3, 8, 9], np.int32),
            'py_idx': np.array([4, 5, 10, 11], np.int32),
            'pz_idx': np.array([1, 0, 7, 6], np.int32),
            'pz_valid': np.array([True, False, True, False]),
            'coords_clipped': rng.uniform(0.0, 1.0, (4, 3)).astype(np.float32),
            'coords_residual': np.zeros((4, 3), np.float32),
        }
        self.ranges = [(0, 2), (2, 4)]
        node_idx, node_start = bnglink.part_node_sets(self.skin, self.ranges)
        count = len(self.rest)
        self.state = {
            'vehicle_ref': np.empty((count, 3)),
            'vehicle_q': np.array((1.0, 0.0, 0.0, 0.0)),
            'vehicle_t': np.zeros(3),
            'nodes': np.empty((count, 3)),
            'node_idx': node_idx,
            'node_start': node_start,
            'ref_nodes': np.empty((len(node_idx), 3)),
            'part_q': np.tile((1.0, 0.0, 0.0, 0.0), (2, 1)),
            'part_t': np.zeros((2, 3)),
            'modes': np.empty(2, np.int8),
        }
        bnglink.nodes_to_skin_axes(self.rest, self.state['vehicle_ref'])
        self.state['ref_nodes'][:] = self.state['vehicle_ref'][node_idx]

    def moved_rest(self):
        rotation = axis_rotation((0.3, 0.5, 0.8), 0.7)
        return (self.rest @ rotation.T + (10.0, -4.0, 2.0)).astype(np.float32)

    def rigidify(self, node_pos):
        s = self.state
        bnglink.rigidify(node_pos, s['vehicle_ref'], s['vehicle_q'], s['vehicle_t'], s['nodes'],
                         s['node_idx'], s['node_start'], s['ref_nodes'], s['part_q'], s['part_t'],
                         self.TOLERANCE, s['modes'])
        return list(s['modes'])

    def assert_parts_match_nodes(self):
        s = self.state
        for p in range(len(self.ranges)):
            begin, end = s['node_start'][p], s['node_start'][p + 1]
            shown = s['ref_nodes'][begin:end] @ quat_matrix(s['part_q'][p]).T + s['part_t'][p]
            error = np.linalg.norm(shown - s['nodes'][s['node_idx'][begin:end]], axis=1).max()
            self.assertLess(error, self.TOLERANCE)

    def test_part_node_sets(self):
        np.testing.assert_array_equal(np.arange(12), self.state['node_idx'])
        np.testing.assert_array_equal([0, 6, 12], self.state['node_start'])

    def test_rigid_vehicle_motion_keeps_all_parts(self):
        moved = self.moved_rest()

        self.assertEqual([bnglink.RIGID_KEEP, bnglink.RIGID_KEEP], self.rigidify(moved))
        np.testing.assert_allclose(self.state['nodes'], self.state['vehicle_ref'], atol=1e-4)

        rest_out = np.empty((4, 3), np.float32)
        moved_out = np.empty((4, 3), np.float32)
        self.decoder.deform_body(self.rest, self.skin, rest_out)
        self.decoder.deform_body(moved, self.skin, moved_out)
        bnglink.to_vehicle_space(moved_out, self.state['vehicle_q'], self.state['vehicle_t'], moved_out)
        np.testing.assert_allclose(moved_out, rest_out, atol=1e-4)

    def test_deform_xform_matches_separate_to_vehicle_space(self):
        moved = self.moved_rest()
        self.rigidify(moved)
        q, t = self.state['vehicle_q'], self.state['vehicle_t']

        two_pass = np.empty((4, 3), np.float32)
        self.decoder.deform_body(moved, self.skin, two_pass)
        bnglink.to_vehicle_space(two_pass, q, t, two_pass)

        xform = np.empty((3, 4))
        bnglink.vehicle_space_xform(q, t, xform)
        fused = np.empty((4, 3), np.float32)
        self.decoder.deform_body(moved, self.skin, fused, xform)

        np.testing.assert_allclose(fused, two_pass, rtol=0, atol=1e-6)

    def test_deformed_part_is_reskinned_and_rigid_part_follows(self):
        moved = self.moved_rest()
        moved[9] += (0.05, 0.0, 0.0)

        modes = self.rigidify(moved)

        self.assertIn(modes[0], (bnglink.RIGID_KEEP, bnglink.RIGID_MOVED))
        self.assertEqual(bnglink.RIGID_RESKIN, modes[1])
        self.assert_parts_match_nodes()


class UePropTransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'unreal' not in sys.modules:
            sys.modules['unreal'] = types.ModuleType('unreal')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ue5'))
        cls.transforms = importlib.import_module('transforms')

    def test_prop_matrices_batch_matches_per_prop_loop(self):
        rng = np.random.default_rng(3)
        rows = rng.standard_normal((5, 12)).astype(np.float32)
        offsets = rng.standard_normal((5, 4, 4))
        offsets[:, 3, :] = (0.0, 0.0, 0.0, 1.0)

        batched = self.transforms.prop_matrices_batch(rows, offsets)

        for i in range(len(rows)):
            expected = offsets[i] @ self.transforms.mat12_to_matrix(rows[i])
            np.testing.assert_allclose(batched[i], expected, rtol=1e-12, atol=1e-12)

    def test_ue_prop_matrices_matches_per_prop_conversion(self):
        rng = np.random.default_rng(5)
        rows = rng.standard_normal((5, 12)).astype(np.float32)
        offsets = rng.standard_normal((5, 4, 4))
        offsets[:, 3, :] = (0.0, 0.0, 0.0, 1.0)

        batched = self.transforms.ue_prop_matrices(rows, offsets)

        t = self.transforms
        for i in range(len(rows)):
            expected = t.BNG_TO_UE_4D @ (offsets[i] @ t.mat12_to_matrix(rows[i])) @ t.BNG_TO_UE_4D
            expected[:3, :3] = expected[:3, :3] @ t.PROP_AXIS_CORRECTION
            expected[:3, 3] *= t.BNG_SCALE
            np.testing.assert_allclose(batched[i], expected, rtol=1e-12, atol=1e-12)


class UeMeshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'unreal' not in sys.modules:
            sys.modules['unreal'] = types.ModuleType('unreal')
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ue5'))
        cls.mesh = importlib.import_module('mesh')

    def test_unique_corners_splits_vertices_on_uv_seams_in_first_use_order(self):
        corners = np.array((5, 6, 7, 7, 6, 5))
        uvs = np.zeros((6, 2), dtype=np.float32)
        uvs[5] = (0.5, 0.5)

        vertex_corners, indices = self.mesh.unique_corners(corners, uvs, np.zeros_like(uvs))

        self.assertEqual([0, 1, 2, 5], vertex_corners.tolist())
        self.assertEqual([0, 1, 2, 2, 1, 3], indices.tolist())

    def test_split_sections_rebuilds_every_triangle(self):
        verts = np.arange(12, dtype=np.float64).reshape(4, 3)
        faces = np.array(((0, 1, 2), (2, 1, 3), (0, 2, 3)))
        uvs = np.arange(18, dtype=np.float32).reshape(9, 2)
        material_ids = np.array((1, 0, 1))
        switch_ids = np.array((-1, -1, 4))

        sections = list(self.mesh.split_sections(
            verts, verts, np.zeros((4, 4)), faces, uvs, uvs, material_ids, switch_ids))

        self.assertEqual([(0, -1), (1, -1), (1, 4)], [(m, s) for m, s, _ in sections])
        for material_id, switch_id, (section_verts, _, _, indices, section_uvs, _) in sections:
            mask = (material_ids == material_id) & (switch_ids == switch_id)
            np.testing.assert_array_equal(verts[faces[mask]], section_verts[indices])
            np.testing.assert_array_equal(uvs.reshape(-1, 3, 2)[mask], section_uvs[indices])


class GpuSkinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ue5'))
        cls.gpu_skin = importlib.import_module('gpu_skin')

    def setUp(self):
        self.bng_nodes = np.array((
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 0.0, 0.5),
        ), dtype=np.float32)
        self.ue_nodes = np.array((
            (0.0, 0.0, 0.0),
            (100.0, 0.0, 0.0),
            (0.0, 100.0, 0.0),
            (0.0, 0.0, 50.0),
        ), dtype=np.float32)
        self.skin = {
            'ref_idx': np.array((0, 0), dtype=np.int32),
            'px_idx': np.array((1, 1), dtype=np.int32),
            'py_idx': np.array((2, 2), dtype=np.int32),
            'pz_idx': np.array((3, 0), dtype=np.int32),
            'pz_valid': np.array((True, False)),
            'coords_clipped': np.array(((0.25, 0.5, 0.4), (0.25, 0.5, 0.2)), dtype=np.float32),
            'coords_residual': np.array(((0.0, 0.1, 0.0), (0.0, 0.0, 0.05)), dtype=np.float32),
        }
        self.source = np.array((0, 1), dtype=np.int32)
        self.normals = np.array(((0.36, -0.48, -0.8), (0.36, -0.48, -0.8)), dtype=np.float32)
        self.tangents = np.array(((0.8, 0.6, 0.0, 1.0), (0.8, 0.6, 0.0, 1.0)), dtype=np.float32)
        self.records = self.gpu_skin.build_skin_vertices(
            self.skin, self.source, self.normals, self.tangents, self.ue_nodes)

    @staticmethod
    def _normalize(v):
        length = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(length, 1e-12)

    def shader_reference(self, nodes):
        positions = np.empty((len(self.records), 3), dtype=np.float32)
        normals = np.empty_like(positions)
        tangents = np.empty_like(positions)
        for i, rec in enumerate(self.records):
            r = nodes[rec['nodes'][0]]
            a = nodes[rec['nodes'][1]] - r
            b = nodes[rec['nodes'][2]] - r
            n = np.cross(b, a)
            n = n / max(np.linalg.norm(n), 1e-12)
            if rec['nodes'][3] == self.gpu_skin.INVALID_NODE:
                c = n
            else:
                c = nodes[rec['nodes'][3]] - r
            fx = rec['coords'][0] + rec['residual'][0] / (np.linalg.norm(a) + 1e-6)
            fy = rec['coords'][1] + rec['residual'][1] / (np.linalg.norm(b) + 1e-6)
            fz = rec['coords'][2] + rec['residual'][2] / (np.linalg.norm(c) + 1e-6)
            positions[i] = r + a * fx + b * fy + c * fz
            normals[i] = a * rec['normal'][0] + b * rec['normal'][1] + n * rec['normal'][2]
            tangents[i] = a * rec['tangent'][0] + b * rec['tangent'][1] + n * rec['tangent'][2]
        normals = self._normalize(normals)
        tangents = self._normalize(tangents - normals * np.sum(tangents * normals, axis=1, keepdims=True))
        return positions, normals, tangents

    def test_skin_vertex_dtype_matches_cpp_layout(self):
        self.assertEqual(68, self.gpu_skin.SKIN_VERTEX_DTYPE.itemsize)

    def test_shader_positions_match_cpu_skinning(self):
        expected = np.empty((2, 3), dtype=np.float32)
        bnglink.deform_skin(
            self.bng_nodes,
            self.skin['ref_idx'], self.skin['px_idx'], self.skin['py_idx'], self.skin['pz_idx'],
            self.skin['pz_valid'], self.skin['coords_clipped'], self.skin['coords_residual'],
            bnglink.IDENTITY_XFORM, expected,
        )
        expected_ue = np.column_stack((-expected[:, 2], expected[:, 0], expected[:, 1])).astype(np.float32) * 100.0

        positions, _, _ = self.shader_reference(self.ue_nodes)

        np.testing.assert_allclose(positions, expected_ue, rtol=1e-5, atol=1e-4)

    def test_bind_pose_keeps_normals_and_tangents(self):
        _, normals, tangents = self.shader_reference(self.ue_nodes)

        np.testing.assert_allclose(normals, self.normals, rtol=1e-6, atol=1e-6)
        np.testing.assert_allclose(tangents, self.tangents[:, :3], rtol=1e-6, atol=1e-6)

    def test_rigid_rotation_rotates_normals_and_tangents(self):
        rotation = axis_rotation((0.2, 0.4, 0.7), 0.6)
        rotated_nodes = (self.ue_nodes @ rotation.T).astype(np.float32)
        _, normals, tangents = self.shader_reference(rotated_nodes)

        np.testing.assert_allclose(normals, self.normals @ rotation.T, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(tangents, self.tangents[:, :3] @ rotation.T, rtol=1e-5, atol=1e-5)


if __name__ == '__main__':
    unittest.main()
