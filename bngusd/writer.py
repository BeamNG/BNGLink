import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

import bnglink
from .layout import PAINT_SCALARS, ROOT_PATH, flip_uv_v, skin_to_world

MATERIALS_PATH = f'{ROOT_PATH}/Materials'
# deform_skin() writes xform @ (x, z, -y): this maps it back to node space
SKIN_TO_WORLD_XFORM = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
T = Sdf.ValueTypeNames


def _safe_name(name):
    text = re.sub(r'[^A-Za-z0-9_]+', '_', name).strip('_') or 'unnamed'
    return f'_{text}' if text[0].isdigit() else text


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _set(prim, name, type_name, value):
    prim.CreateAttribute(name, type_name).Set(value)


def _matrices(m12s):
    m12s = np.reshape(m12s, (-1, 3, 4))
    matrices = np.zeros((len(m12s), 4, 4))
    matrices[:, :3] = m12s
    matrices[:, 3, 3] = 1.0
    return matrices


class TakeWriter:
    # Writes the take stage on a fixed capture time base: frames are quantized sender sim time, playback fps is a reader concern.

    def __init__(self, capture_fps=1000.0, baked_mesh=False):
        self.capture_fps = capture_fps
        self.baked_mesh = baked_mesh
        self.stage = Usd.Stage.CreateInMemory('bnglink_take.usda')
        self.stage.SetFramesPerSecond(capture_fps)
        self.stage.SetTimeCodesPerSecond(capture_fps)
        self.stage.SetStartTimeCode(1)
        UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(self.stage, 1.0)
        self.stage.SetDefaultPrim(UsdGeom.Xform.Define(self.stage, ROOT_PATH).GetPrim())
        UsdGeom.Xform.Define(self.stage, MATERIALS_PATH)
        self.segments = []
        # Vehicle id: its open segment, the one syncs and updates go to
        self.current = {}
        self.end_frame = 1

    def add_vehicle(self, init, frame):
        vehicle_id = init['vehicle_id']
        index = sum(segment.vehicle_id == vehicle_id for segment in self.segments)
        segment = _Segment(self.stage, init, index, frame, self.baked_mesh)
        self.segments.append(segment)
        self.current[vehicle_id] = segment
        return segment

    def write_frame(self, syncs, frame):
        for sync in syncs:
            self.current[sync['vehicle_id']].write_sync(sync, frame)
        self.end_frame = max(self.end_frame, frame)

    def close_vehicle(self, vehicle_id, end_frame):
        segment = self.current.pop(vehicle_id, None)
        if segment is not None:
            segment.close(end_frame)

    def save(self, usdz_path):
        path = Path(usdz_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stage.SetEndTimeCode(self.end_frame)
        for segment in self.current.values():
            segment.close(self.end_frame + 1)
        temp_dir = Path(tempfile.mkdtemp(prefix='bnglink_usd_'))
        try:
            for segment in self.segments:
                segment.write_textures(temp_dir)
            usd_path = str(temp_dir / 'take.usdc')
            self.stage.GetRootLayer().Export(usd_path)
            if not UsdUtils.CreateNewUsdzPackage(usd_path, str(path)):
                raise RuntimeError(f'could not write {path}')
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class _Segment:
    # One vehicle from its init until it is reinitialized or the take ends

    def __init__(self, stage, init, index, start_frame, baked_mesh):
        self.stage = stage
        self.vehicle_id = init['vehicle_id']
        self.start_frame = start_frame
        self.name = f'Vehicle_{self.vehicle_id}' + (f'_{index}' if index else '')
        self.path = f'{ROOT_PATH}/{self.name}'
        self.prim = UsdGeom.Xform.Define(self.stage, self.path).GetPrim()
        self.origin = self.prim.CreateAttribute('bng:origin', T.Float3)
        # Attribute name: last value written by _write_step
        self.step_values = {}
        # Per texture slot: [(first frame, texture)], one entry per distinct image recorded
        self.texture_versions = [[(start_frame, dict(texture))] for texture in init['textures']]

        _set(self.prim, 'bng:vehicleId', T.Int, self.vehicle_id)
        _set(self.prim, 'bng:geometryKey', T.String, init['geometry_key'])
        _set(self.prim, 'bng:mode', T.String, 'baked' if baked_mesh else 'skinned')
        meta = {key: init[key] for key in ('vehicle_id', 'geometry_key', 'node_ids', 'materials', 'paints')}
        meta['textures'] = [{key: value for key, value in texture.items() if key != 'data'} for texture in init['textures']]
        _set(self.prim, 'bng:init', T.String, _json(meta))

        material_dir = f'{MATERIALS_PATH}/Vehicle_{self.vehicle_id}'
        UsdGeom.Xform.Define(self.stage, material_dir)
        self.materials = []
        for material in init['materials']:
            usd_material = UsdShade.Material.Define(self.stage, f'{material_dir}/{_safe_name(material["name"])}')
            _set(usd_material.GetPrim(), 'bng:id', T.Int, material['id'])
            _set(usd_material.GetPrim(), 'bng:definition', T.String, _json(material))
            self.materials.append(usd_material)

        parts = init['parts']
        UsdGeom.Xform.Define(self.stage, f'{self.path}/Body')
        self.part_meshes = [self._write_mesh(f'{self.path}/Body/{_safe_name(part["name"])}', part) for part in parts]
        self.nodes = None
        self.skin = None
        if not baked_mesh:
            self._write_skin(init['node_ids'], parts)
        elif parts:
            self.skin, self.part_ranges, count = bnglink.merge_part_skins(parts)
            self.rest_normals = skin_to_world(np.concatenate([part['normals'] for part in parts]))
            self.positions = np.empty((count, 3), dtype=np.float32)
            self.normals = np.empty((count, 3), dtype=np.float32)
            self.rest_nodes = None
        self._write_props(init['props'])
        switch_ids = sorted({
            int(switch_id)
            for entry in parts + init['props']
            for switch_id in entry['switch_ids']
            if switch_id >= 0
        })
        self.write_switches(
            [{'id': switch_id, 'emissiveScale': 0.0} for switch_id in switch_ids],
            start_frame,
        )
        self.write_paints(init['paints'], start_frame)

    def write_sync(self, sync, frame):
        node_raw = sync['node_raw']
        self.origin.Set(sync['origin'], frame)
        broken_beams = Vt.IntArray(
            [int(value) for beam in sync['broken_beams'] for value in beam])
        hidden_props = Vt.IntArray([int(value) for value in sync['hidden_props']])
        damaged_switches = Vt.IntArray(
            [int(value) for switch in sync['damaged_switches'] for value in switch])
        self._write_step({
            'bng:brokenBeams': (T.IntArray, broken_beams),
            'bng:hiddenProps': (T.IntArray, hidden_props),
            'bng:damagedSwitches': (T.IntArray, damaged_switches),
        }, frame)
        if self.nodes is not None:
            # Single-datagram syncs decode to views at an odd offset, and USD rejects unaligned buffers
            self.nodes.Set(np.require(node_raw, requirements='A'), frame)
        elif self.skin is not None:
            # Normals rotate from the first sampled frame's locator pose,
            # first normal sample is the rest normal even when capture starts mid-deformation
            if self.rest_nodes is None:
                self.rest_nodes = np.array(node_raw)
            bnglink.BngDecoder.deform_body(node_raw, self.skin, self.positions, SKIN_TO_WORLD_XFORM)
            bnglink.BngDecoder.deform_body_normals(node_raw, self.rest_nodes, self.skin, self.rest_normals, self.normals)
            for mesh, (start, stop) in zip(self.part_meshes, self.part_ranges):
                mesh.GetPointsAttr().Set(self.positions[start:stop], frame)
                mesh.GetNormalsAttr().Set(self.normals[start:stop], frame)
        for op, matrix in zip(self.prop_ops, self.prop_offsets @ _matrices(sync['prop_mats'])):
            op.Set(Gf.Matrix4d(matrix), frame)

    def write_paints(self, paints, frame):
        values = {}
        for index, paint in enumerate(paints):
            values[f'bng:paint{index}:baseColor'] = (T.Float4, tuple(paint['baseColor']))
            for key in PAINT_SCALARS:
                values[f'bng:paint{index}:{key}'] = (T.Float, paint[key])
        self._write_step(values, frame)

    def write_switches(self, switches, frame):
        values = {}
        for switch in switches:
            prefix = f'bng:switch{switch["id"]}'
            material_name = f'{prefix}:materialId'
            if material_name not in self.step_values and frame > self.start_frame:
                self._write_step({
                    material_name: (T.Int, -1),
                    f'{prefix}:emissiveScale': (T.Float, 0.0),
                }, self.start_frame)
            values[material_name] = (T.Int, switch.get('materialId', -1))
            values[f'{prefix}:emissiveScale'] = (T.Float, switch['emissiveScale'])
        self._write_step(values, frame)

    def update_textures(self, updates, frame):
        for update in updates:
            versions = self.texture_versions[update['index']]
            last_frame, last = versions[-1]
            if update['data'] == last['data']:
                continue
            if last_frame == frame:
                versions.pop()
            versions.append((frame, dict(last, name=update['name'], mime=update['mime'], data=update['data'])))

    def close(self, end_frame):
        _set(self.prim, 'bng:endFrame', T.Int, max(self.start_frame + 1, end_frame))

    def write_textures(self, out_dir):
        changes = {}
        for index, versions in enumerate(self.texture_versions):
            for version, (frame, texture) in enumerate(versions):
                # The leading './' anchors the path to the layer; without it USD treats it as a search path and usdz packaging renames it
                name = _safe_name(Path(texture['name']).stem or f'texture_{index}')
                ext = '.png' if texture['mime'] == 'image/png' else '.jpg'
                asset_path = f'./textures/{self.name}/{index:04d}_{name}_v{version}{ext}'
                target = out_dir / asset_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(texture['data'])
                changes.setdefault(frame, {})[index] = asset_path
        # Asset arrays are not interpolated, so each sample holds until the next change
        attr = self.prim.CreateAttribute('bng:textures', T.AssetArray)
        current = [''] * len(self.texture_versions)
        for frame in sorted(changes):
            for index, asset_path in changes[frame].items():
                current[index] = asset_path
            attr.Set(Sdf.AssetPathArray(current), frame)

    def _write_step(self, values, frame):
        # values: {attribute name: (type, value)}.
        # Only changes are sampled, together with the old value one frame earlier, so USD's linear interpolation does not fade discrete state
        for name, (type_name, value) in values.items():
            previous = self.step_values.get(name)
            if previous == value:
                continue
            attr = self.prim.CreateAttribute(name, type_name)
            if previous is not None and frame > self.start_frame:
                attr.Set(previous, frame - 1)
            attr.Set(value, frame)
            self.step_values[name] = value

    def _write_skin(self, node_ids, parts):
        nodes = UsdGeom.Points.Define(self.stage, f'{self.path}/Nodes')
        _set(nodes.GetPrim(), 'bng:nodeIds', T.IntArray, node_ids)
        self.nodes = nodes.GetPointsAttr()
        for mesh, part in zip(self.part_meshes, parts):
            primvars = UsdGeom.PrimvarsAPI(mesh)
            skin_nodes = np.column_stack((part['ref_idx'], part['px_idx'], part['py_idx'],
                                          np.where(part['pz_valid'], part['pz_idx'], -1)))
            primvars.CreatePrimvar('bng:skinNodes', T.IntArray, UsdGeom.Tokens.vertex, 4).Set(skin_nodes.ravel())
            primvars.CreatePrimvar('bng:skinCoords', T.Float3Array, UsdGeom.Tokens.vertex).Set(
                part['coords_clipped'] + part['coords_residual'])

    def _write_props(self, props):
        UsdGeom.Xform.Define(self.stage, f'{self.path}/Props')
        self.prop_ops = []
        for index, prop in enumerate(props):
            xform = UsdGeom.Xform.Define(self.stage, f'{self.path}/Props/{_safe_name(prop["name"])}_{index}')
            mesh = self._write_mesh(f'{xform.GetPath()}/Mesh', prop)
            _set(mesh.GetPrim(), 'bng:pid', T.Int, prop['pid'])
            self.prop_ops.append(xform.MakeMatrixXform())
        # The sampled transform is bindMatrix * inverse(liveInitMatrix) * liveMatrix
        self.prop_offsets = (_matrices([prop['bind_matrix'] for prop in props])
                             @ np.linalg.inv(_matrices([prop['live_init_matrix'] for prop in props])))

    def _write_mesh(self, path, entry):
        mesh = UsdGeom.Mesh.Define(self.stage, path)
        prim = mesh.GetPrim()
        _set(prim, 'bng:name', T.String, entry['name'])
        mesh.GetPointsAttr().Set(skin_to_world(entry['verts']))
        mesh.GetFaceVertexCountsAttr().Set(np.full(len(entry['faces']), 3, dtype=np.int32))
        mesh.GetFaceVertexIndicesAttr().Set(entry['faces'].ravel())
        mesh.GetNormalsAttr().Set(skin_to_world(entry['normals']))
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        primvars = UsdGeom.PrimvarsAPI(prim)
        for name, type_name, interpolation, value in (
            ('st', T.TexCoord2fArray, UsdGeom.Tokens.faceVarying, flip_uv_v(entry['uvs'])),
            ('st1', T.TexCoord2fArray, UsdGeom.Tokens.faceVarying, flip_uv_v(entry['uvs2'])),
            ('bng:tangents', T.Float4Array, UsdGeom.Tokens.vertex, entry['tangents']),
            ('bng:materialIds', T.IntArray, UsdGeom.Tokens.uniform, entry['material_ids']),
            ('bng:switchIds', T.IntArray, UsdGeom.Tokens.uniform, entry['switch_ids']),
        ):
            primvars.CreatePrimvar(name, type_name, interpolation).Set(value)
        # Generic USD viewers get the lowest material id as a preview binding
        if len(entry['material_ids']):
            UsdShade.MaterialBindingAPI(prim).Bind(self.materials[int(entry['material_ids'].min())])
        return mesh
