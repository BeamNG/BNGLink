# Platform-neutral message model: turns transfer payloads into plain Python
# objects in BeamNG display space. Each endpoint converts to its own axes.

import json
import math
import re
import struct

import numpy as np

from .assets import AssetCache, material_key
from .skinning import IDENTITY_XFORM, SKIN_DTYPE, deform_normals, deform_skin, nodes_to_skin_axes
from .transport import KIND_CONTROL, KIND_STREAM

MAX_METADATA_SIZE = 16 * 1024 * 1024
MAX_VEHICLES = 1024
MAX_NODES = 100000
MAX_PROPS = 100000
MAX_MESHES = 10000
MAX_VERTICES = 10000000
MAX_INDICES = 30000000
MAX_MATERIALS = 16384
MAX_TEXTURES = 16384
MESSAGE_TYPES = {'paint_update', 'texture_update', 'material_update'}


def _bounded_int(value, name, maximum):
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f'invalid {name}: {value}')
    return value


def _bounded_list(value, name, maximum):
    # Lua's JSON encoder writes empty tables as objects
    if value == {}:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'invalid {name}')
    return value


def _matrix12(value, name):
    if not isinstance(value, list) or len(value) != 12:
        raise ValueError(f'invalid {name}')
    if any(type(item) not in (int, float) or not math.isfinite(item) for item in value):
        raise ValueError(f'invalid {name}')


def _validate_texture_refs(value, texture_count):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('Texture') and item is not None:
                index = _bounded_int(item, key, MAX_TEXTURES)
                if index >= texture_count:
                    raise ValueError(f'{key} index out of range: {index}')
            else:
                _validate_texture_refs(item, texture_count)
    elif isinstance(value, list):
        for item in value:
            _validate_texture_refs(item, texture_count)


class _Reader:
    def __init__(self, payload, offset=0):
        self.payload = payload
        self.offset = offset

    def array(self, dtype, count, width=None):
        count = _bounded_int(count, 'array count', MAX_INDICES)
        item_count = count * (width or 1)
        byte_count = item_count * np.dtype(dtype).itemsize
        if self.offset + byte_count > len(self.payload):
            raise ValueError('payload ended before array data')
        values = np.frombuffer(self.payload, dtype=dtype, count=item_count, offset=self.offset)
        self.offset += values.nbytes
        return values if width is None else values.reshape(count, width)

    def unpack(self, format, context):
        size = struct.calcsize(format)
        if self.offset + size > len(self.payload):
            raise ValueError(f'payload ended before {context}')
        values = struct.unpack_from(format, self.payload, self.offset)
        self.offset += size
        return values


def _skin_axes(directions):
    # GPUMesh swaps vertex positions to (x, z, -y) but leaves normals and tangents in node space.
    # The copy keeps the tangent handedness in w.
    out = directions.copy()
    nodes_to_skin_axes(directions, out)
    return out


def _read_mesh(reader, vert_count, index_count, skinned, material_count=None):
    # Returns the mesh arrays and the skin entries (None when not skinned).
    # Degenerate triangles are dropped together with their corner data.
    mesh = {
        'verts': reader.array('<f4', vert_count, 3),
        'normals': _skin_axes(reader.array('<f4', vert_count, 3)),
        'tangents': _skin_axes(reader.array('<f4', vert_count, 4)),
    }
    tris = reader.array('<u4', index_count).reshape(-1, 3)
    skin = reader.array(SKIN_DTYPE, vert_count) if skinned else None
    uvs = reader.array('<f4', index_count, 2)
    uvs2 = reader.array('<f4', index_count, 2)
    material_ids = reader.array('<u4', index_count // 3)
    switch_ids = reader.array('<u4', index_count // 3).astype(np.int32) - 1
    if len(tris) and int(tris.max()) >= vert_count:
        raise ValueError('mesh vertex index out of range')
    if material_count is not None and len(material_ids) and int(material_ids.max()) >= material_count:
        raise ValueError('mesh material index out of range')

    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    corner_keep = np.repeat(keep, 3)
    mesh.update(
        faces=tris[keep],
        uvs=uvs[corner_keep],
        uvs2=uvs2[corner_keep],
        material_ids=material_ids[keep],
        switch_ids=switch_ids[keep],
    )
    return mesh, skin


def _base_name(name):
    return re.sub(r'\.\d{3}$', '', name)


def _mesh_counts(info, vertex_field):
    if not isinstance(info, dict):
        raise ValueError('invalid mesh metadata')
    vert_count = _bounded_int(info.get(vertex_field), vertex_field, MAX_VERTICES)
    index_count = _bounded_int(info.get('indexCount'), 'indexCount', MAX_INDICES)
    if index_count % 3:
        raise ValueError('mesh index count is not divisible by three')
    return vert_count, index_count


def _validate_mesh_materials(meshes, material_count):
    for mesh in meshes:
        material_ids = mesh['material_ids']
        if len(material_ids) and int(material_ids.max()) >= material_count:
            raise ValueError('mesh material index out of range')


class BngDecoder:
    def __init__(self, assets=None):
        self.assets = assets or AssetCache()
        self.vehicle_assets = {}

    def reset(self):
        self.assets.geometries.clear()
        self.assets.textures.clear()
        self.vehicle_assets.clear()

    def remove_vehicle(self, vehicle_id):
        self.vehicle_assets.pop(vehicle_id, None)

    def decode(self, kind, payload):
        if kind == KIND_STREAM:
            return 'sync', self.decode_sync(payload)
        if kind == KIND_CONTROL:
            if len(payload) > 1024:
                raise ValueError('control payload too large')
            return 'control', payload.decode('utf-8')

        meta, data = self.decode_framed(payload)
        message_type = meta.get('type', 'init')
        if message_type == 'init':
            return 'init', self.decode_init(data, meta)
        if message_type not in MESSAGE_TYPES:
            raise ValueError(f'unknown message type: {message_type}')

        vehicle_id = _bounded_int(meta.get('vehicleId'), 'vehicle id', 0xffffffff)
        assets = self.vehicle_assets.get(vehicle_id)
        if assets is None:
            raise ValueError(f'update for unknown vehicle {vehicle_id}')

        if message_type == 'texture_update':
            textures = self.decode_texture_update(data, meta, assets['textures'])
            return 'texture_update', {'vehicle_id': vehicle_id, 'textures': textures}
        if data:
            raise ValueError(f'{message_type} payload has unexpected binary data')
        if message_type == 'paint_update':
            paints = _bounded_list(meta.get('paints'), 'paints', 3)
            return 'paint_update', {'vehicle_id': vehicle_id, 'paints': paints}

        switches = _bounded_list(meta.get('switches'), 'switches', 65536)
        for switch in switches:
            if not isinstance(switch, dict):
                raise ValueError('invalid material switch')
            _bounded_int(switch.get('id'), 'switch id', 0xffffffff)
            material_id = switch.get('materialId')
            if material_id is not None:
                material_id = _bounded_int(material_id, 'material id', MAX_MATERIALS)
                if material_id >= assets['materials']:
                    raise ValueError(f'material index out of range: {material_id}')
            emissive_scale = switch.get('emissiveScale')
            if type(emissive_scale) not in (int, float) or not math.isfinite(emissive_scale):
                raise ValueError('invalid emissive scale')
            damaged = switch.get('damaged')
            if damaged is not None and type(damaged) is not bool:
                raise ValueError('invalid damaged material state')
            if damaged and material_id is None:
                raise ValueError('damaged material is missing its material id')
        return 'material_update', {'vehicle_id': vehicle_id, 'switches': switches}

    @staticmethod
    def decode_framed(payload):
        if len(payload) < 4:
            raise ValueError('framed payload is missing its metadata length')
        meta_len = struct.unpack_from('<I', payload, 0)[0]
        if meta_len > MAX_METADATA_SIZE or meta_len > len(payload) - 4:
            raise ValueError(f'invalid metadata length: {meta_len}')
        try:
            meta = json.loads(payload[4:4 + meta_len])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'invalid metadata: {exc}') from exc
        if not isinstance(meta, dict):
            raise ValueError('metadata must be an object')
        return meta, payload[4 + meta_len:]

    @staticmethod
    def decode_texture_update(payload, meta, texture_count):
        entries = _bounded_list(meta.get('textures'), 'textures', MAX_TEXTURES)
        textures = []
        expected_offset = 0
        seen_indices = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('invalid texture update')
            index = _bounded_int(entry.get('index'), 'texture index', MAX_TEXTURES)
            if index >= texture_count or index in seen_indices:
                raise ValueError(f'invalid texture index: {index}')
            offset = _bounded_int(entry.get('offset'), 'texture offset', len(payload))
            size = _bounded_int(entry.get('size'), 'texture size', len(payload))
            if offset != expected_offset or offset + size > len(payload):
                raise ValueError('invalid texture data range')
            if not isinstance(entry.get('name'), str) or not isinstance(entry.get('mime'), str):
                raise ValueError('invalid texture metadata')
            textures.append({
                'index': index,
                'name': entry['name'],
                'mime': entry['mime'],
                'data': payload[offset:offset + size],
            })
            seen_indices.add(index)
            expected_offset += size
        if expected_offset != len(payload):
            raise ValueError('texture update payload size mismatch')
        return textures

    def decode_init(self, payload, meta):
        vehicle_id = _bounded_int(meta.get('vehicleId'), 'vehicle id', 0xffffffff)
        geometry = meta.get('geometry')
        if not isinstance(geometry, dict) or not isinstance(geometry.get('key'), str):
            raise ValueError('invalid geometry metadata')
        if type(geometry.get('included')) is not bool:
            raise ValueError('invalid geometry included flag')

        materials = _bounded_list(meta.get('materials'), 'materials', MAX_MATERIALS)
        textures_meta = _bounded_list(meta.get('textures'), 'textures', MAX_TEXTURES)
        paints = _bounded_list(meta.get('paints'), 'paints', 3)
        for index, material in enumerate(materials):
            if not isinstance(material, dict):
                raise ValueError(f'invalid material at index {index}')
            material_id = _bounded_int(material.get('id'), 'material id', MAX_MATERIALS)
            if material_id != index:
                raise ValueError(f'invalid material id at index {index}')
            _validate_texture_refs(material, len(textures_meta))

        reader = _Reader(payload)
        if geometry['included']:
            node_ids = _bounded_list(meta.get('nodeIds'), 'nodeIds', MAX_NODES)
            for node_id in node_ids:
                _bounded_int(node_id, 'node id', 0x7fffffff)
            if len(set(node_ids)) != len(node_ids):
                raise ValueError('duplicate node id')
            node_index = {node_id: i for i, node_id in enumerate(node_ids)}

            parts_meta = _bounded_list(geometry.get('parts'), 'geometry parts', MAX_MESHES)
            props_meta = _bounded_list(geometry.get('props'), 'geometry props', MAX_MESHES)
            parts = []
            for info in parts_meta:
                vert_count, index_count = _mesh_counts(info, 'uniqueVertCount')
                if not isinstance(info.get('name'), str):
                    raise ValueError('invalid part name')
                mesh, skin = _read_mesh(reader, vert_count, index_count, True, len(materials))
                part = self.build_skinning(
                    _base_name(info['name']), mesh['verts'], mesh['faces'], skin, node_index)
                parts.append(dict(mesh, **part))

            prop_templates = []
            for info in props_meta:
                vert_count, index_count = _mesh_counts(info, 'vertCount')
                if not isinstance(info.get('name'), str):
                    raise ValueError('invalid prop name')
                mesh, _ = _read_mesh(reader, vert_count, index_count, False, len(materials))
                prop_templates.append(dict(mesh, name=_base_name(info['name'])))
            template = {'node_ids': node_ids, 'parts': parts, 'props': prop_templates}
        else:
            template = self.assets.geometry(vehicle_id, geometry['key'])

        _validate_mesh_materials(template['parts'], len(materials))
        _validate_mesh_materials(template['props'], len(materials))

        prop_instances = _bounded_list(meta.get('props'), 'props', MAX_PROPS)
        if len(prop_instances) != len(template['props']):
            raise ValueError('prop instance count does not match geometry')
        props = []
        for prop, instance in zip(template['props'], prop_instances):
            if not isinstance(instance, dict):
                raise ValueError('invalid prop instance')
            pid = _bounded_int(instance.get('pid'), 'prop id', 0xffffffff)
            _matrix12(instance.get('bindMatrix'), 'prop bind matrix')
            _matrix12(instance.get('liveInitMatrix'), 'prop live init matrix')
            props.append(dict(
                prop,
                pid=pid,
                bind_matrix=instance['bindMatrix'],
                live_init_matrix=instance['liveInitMatrix'],
            ))

        textures = []
        pending_textures = []
        expected_offset = 0
        for info in textures_meta:
            if not isinstance(info, dict):
                raise ValueError('invalid texture metadata')
            if not all(isinstance(info.get(field), str) for field in ('key', 'name', 'mime')):
                raise ValueError('invalid texture metadata')
            has_data = 'offset' in info or 'size' in info
            if has_data:
                if 'offset' not in info or 'size' not in info:
                    raise ValueError('incomplete texture data range')
                offset = _bounded_int(info['offset'], 'texture offset', len(payload))
                size = _bounded_int(info['size'], 'texture size', len(payload))
                if offset != expected_offset or reader.offset + offset + size > len(payload):
                    raise ValueError('invalid texture data range')
                start = reader.offset + offset
                texture = {'key': info['key'], 'name': info['name'], 'mime': info['mime'],
                           'data': payload[start:start + size]}
                pending_textures.append(texture)
                expected_offset += size
            else:
                texture = self.assets.texture(vehicle_id, info['key'])
            textures.append(dict(texture))
        read_size = reader.offset + expected_offset
        if read_size != len(payload):
            raise ValueError(f'init payload size mismatch: read {read_size}, have {len(payload)}')

        texture_keys = [texture['key'] for texture in textures]
        for material in materials:
            material['cache_key'] = material_key(material, texture_keys)

        if geometry['included']:
            self.assets.store_geometry(geometry['key'], template)
        for texture in pending_textures:
            self.assets.store_texture(texture['key'], texture)
        self.vehicle_assets[vehicle_id] = {
            'materials': len(materials),
            'textures': len(textures),
        }

        return {
            'vehicle_id': vehicle_id,
            'geometry_key': geometry['key'],
            'node_ids': template['node_ids'],
            # Fresh dicts so endpoints can attach their own data to a cached template
            'parts': [dict(part) for part in template['parts']],
            'props': props,
            'materials': materials,
            'textures': textures,
            'paints': paints,
        }

    @staticmethod
    def build_skinning(name, verts, faces, skin, node_index):
        # Node ids -> sync node indices; missing axis nodes fall back to the reference node
        nodes = skin['nodes']
        mapped = np.array([node_index.get(int(v), -1) for v in nodes.reshape(-1)],
                          dtype=np.int32).reshape(nodes.shape)
        if np.any((nodes >= 0) & (mapped < 0)) or np.any(mapped[:, 0] < 0):
            raise ValueError('skin references an unknown node')
        ref_idx = mapped[:, 0]
        coords = skin['coords']
        coords_clipped = np.clip(coords, 0.0, 1.0)
        return {
            'name': name,
            'verts': verts,
            'faces': faces,
            'ref_idx': ref_idx,
            'px_idx': np.where(mapped[:, 1] >= 0, mapped[:, 1], ref_idx),
            'py_idx': np.where(mapped[:, 2] >= 0, mapped[:, 2], ref_idx),
            'pz_idx': np.where(mapped[:, 3] >= 0, mapped[:, 3], ref_idx),
            'pz_valid': mapped[:, 3] >= 0,
            'coords_clipped': coords_clipped,
            'coords_residual': coords - coords_clipped,
        }

    @staticmethod
    def decode_sync(payload):
        reader = _Reader(payload)
        _, vehicle_count, sim_time = reader.unpack('<2Id', 'sync header')
        _bounded_int(vehicle_count, 'vehicle count', MAX_VEHICLES)
        if not math.isfinite(sim_time):
            raise ValueError('invalid simulation time')
        vehicles = []
        vehicle_ids = set()
        for _ in range(vehicle_count):
            vehicle_id, node_count = reader.unpack('<2I', 'vehicle header')
            if vehicle_id in vehicle_ids:
                raise ValueError(f'duplicate vehicle id in sync: {vehicle_id}')
            vehicle_ids.add(vehicle_id)
            _bounded_int(node_count, 'node count', MAX_NODES)
            node_raw = reader.array('<f4', node_count, 3)
            origin = reader.unpack('<3f', 'vehicle origin')
            prop_count, = reader.unpack('<I', 'prop count')
            _bounded_int(prop_count, 'prop count', MAX_PROPS)
            prop_mats = reader.array('<f4', prop_count, 12)
            damage_revision, broken_count = reader.unpack('<2I', 'damage header')
            _bounded_int(broken_count, 'broken beam count', MAX_INDICES)
            broken_beams = reader.array('<u4', broken_count, 2)
            if len(broken_beams) and int(broken_beams.max()) >= node_count:
                raise ValueError('broken beam node index out of range')
            hidden_count, = reader.unpack('<I', 'hidden prop count')
            _bounded_int(hidden_count, 'hidden prop count', MAX_PROPS)
            hidden_props = reader.array('<u4', hidden_count)
            damaged_count, = reader.unpack('<I', 'damaged material count')
            _bounded_int(damaged_count, 'damaged material count', MAX_MATERIALS)
            damaged_switches = reader.array('<u4', damaged_count, 2)
            if len(damaged_switches) and int(damaged_switches[:, 1].max()) >= MAX_MATERIALS:
                raise ValueError('damaged material index out of range')
            vehicles.append({
                'vehicle_id': vehicle_id,
                'sim_time': sim_time,
                'node_raw': node_raw,
                'origin': origin,
                'prop_mats': prop_mats,
                'damage_revision': damage_revision,
                'broken_beams': broken_beams,
                'hidden_props': hidden_props,
                'damaged_switches': damaged_switches,
            })
        if reader.offset != len(payload):
            raise ValueError('sync payload has trailing data')
        return vehicles

    @staticmethod
    def deform_body(node_pos, skin, out, xform=IDENTITY_XFORM):
        deform_skin(node_pos, skin['ref_idx'], skin['px_idx'], skin['py_idx'], skin['pz_idx'],
                    skin['pz_valid'], skin['coords_clipped'], skin['coords_residual'], xform, out)

    @staticmethod
    def deform_body_normals(node_pos, ref_nodes, skin, normals, out):
        deform_normals(node_pos, ref_nodes, skin['ref_idx'], skin['px_idx'], skin['py_idx'],
                       skin['pz_idx'], skin['pz_valid'], normals, out)
