import bisect
import json
from collections import namedtuple
from pathlib import Path

import numpy as np
from pxr import Ar, Usd, UsdGeom

from .layout import PAINT_SCALARS, ROOT_PATH, flip_uv_v, world_to_skin

IDENTITY_M12 = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
# paints and switches are None when unchanged, textures maps changed slot index: version index
Step = namedtuple('Step', 'sync paints switches textures')


def version_at(versions, frame):
    # versions: [(first frame, texture)] sorted by frame.
    # Returns the index shown at frame, the first version also covers everything before it
    return max(bisect.bisect_right([first for first, _ in versions], frame) - 1, 0)


def latest_take(directory):
    # Take file names start with their timestamp
    takes = sorted(Path(directory).glob('*.usdz'))
    if not takes:
        raise FileNotFoundError(f'No takes found in {directory}')
    return takes[-1]


class _Track:
    # Take values are step data and USD would blend the sparse time samples, so a frame reads the last sample on or before it
    def __init__(self, attr):
        self.attr = attr
        self.times = attr.GetTimeSamples()

    def __call__(self, frame):
        return self.attr.Get(self.times[max(bisect.bisect_right(self.times, frame) - 1, 0)])


class Take:
    # Stage samples are placed by sender sim time on the capture time base.
    # Playback frames run at the chosen fps and resolve to the nearest capture frame.

    def __init__(self, path, fps=None):
        self.stage = Usd.Stage.Open(str(path))
        self.capture_fps = self.stage.GetTimeCodesPerSecond()
        self.fps = fps or self.capture_fps
        self.capture_start = int(self.stage.GetStartTimeCode())
        self.capture_end = int(self.stage.GetEndTimeCode())
        self.frame_range = (1, round((self.capture_end - self.capture_start) * self.fps / self.capture_fps) + 1)
        self.segments = [_Segment(prim) for prim in self.stage.GetPrimAtPath(ROOT_PATH).GetChildren()
                         if prim.HasAttribute('bng:vehicleId')]

    def to_capture_frame(self, frame):
        return self.capture_start + round((frame - 1) * self.capture_fps / self.fps)


class Playhead:
    # Walks a segment frame by frame, so importers only apply the state that changed

    def __init__(self, segment):
        self.segment = segment
        self.paints = None
        self.switches = None
        # Slot index: shown version index, for the texture slots that change during the take.
        # None makes the first step report every one of them.
        self.versions = {index: None for index, versions in enumerate(segment.texture_versions) if len(versions) > 1}

    def step(self, sample):
        segment = self.segment
        paints = segment.paints(sample)
        switches = segment.switches(sample)
        textures = {}
        for index, shown in self.versions.items():
            version = version_at(segment.texture_versions[index], sample)
            if version != shown:
                textures[index] = self.versions[index] = version
        step = Step(segment.sync(sample), None if paints == self.paints else paints,
                    None if switches == self.switches else switches, textures)
        self.paints, self.switches = paints, switches
        return step


class _Segment:
    # Rebuilds the decoded init of one vehicle segment and serves its state at capture frames

    def __init__(self, prim):
        if prim.GetAttribute('bng:mode').Get() != 'skinned':
            raise RuntimeError('BNGLink importers only support skinned USD takes')
        stage = prim.GetStage()
        path = prim.GetPath()
        meta = json.loads(prim.GetAttribute('bng:init').Get())
        self.vehicle_id = meta['vehicle_id']
        end_attr = prim.GetAttribute('bng:endFrame')
        self.end_frame = int(end_attr.Get()) if end_attr else None
        self.texture_versions = _read_texture_versions(prim, meta['textures'])
        body = stage.GetPrimAtPath(path.AppendChild('Body')).GetChildren()
        props = stage.GetPrimAtPath(path.AppendChild('Props')).GetChildren()
        self.init = {
            'vehicle_id': self.vehicle_id,
            'geometry_key': meta['geometry_key'],
            'node_ids': meta['node_ids'],
            'parts': [_read_part(child) for child in body],
            'props': [_read_prop(child) for child in props],
            'materials': meta['materials'],
            'textures': [versions[0][1] for versions in self.texture_versions],
            'paints': meta['paints'],
        }

        self.nodes = _Track(UsdGeom.Points(stage.GetPrimAtPath(path.AppendChild('Nodes'))).GetPointsAttr())
        self.sample_times = self.nodes.times
        self.origin = _Track(prim.GetAttribute('bng:origin'))
        self.broken_beams = _Track(prim.GetAttribute('bng:brokenBeams'))
        self.hidden_props = _Track(prim.GetAttribute('bng:hiddenProps'))
        self.damaged_switches = _Track(prim.GetAttribute('bng:damagedSwitches'))
        self.props = [_Track(child.GetAttribute('xformOp:transform')) for child in props]
        self.paint_tracks = [
            {field: _Track(prim.GetAttribute(f'bng:paint{index}:{field}')) for field in ('baseColor',) + PAINT_SCALARS}
            for index in range(len(meta['paints']))
        ]
        self.switch_tracks = [
            (int(name.split(':')[1][len('switch'):]), _Track(prim.GetAttribute(name)),
             _Track(prim.GetAttribute(name.replace(':materialId', ':emissiveScale'))))
            for name in prim.GetPropertyNames() if name.startswith('bng:switch') and name.endswith(':materialId')
        ]

    def sync(self, frame):
        return {
            'vehicle_id': self.vehicle_id,
            'node_raw': np.asarray(self.nodes(frame), dtype=np.float32),
            'origin': tuple(self.origin(frame)),
            'prop_mats': np.array([np.asarray(track(frame))[:3].ravel() for track in self.props],
                                  dtype=np.float32).reshape(-1, 12),
            'broken_beams': np.asarray(self.broken_beams(frame), dtype=np.uint32).reshape(-1, 2),
            'hidden_props': np.asarray(self.hidden_props(frame), dtype=np.uint32),
            'damaged_switches': np.asarray(self.damaged_switches(frame), dtype=np.uint32).reshape(-1, 2),
        }

    def paints(self, frame):
        paints = []
        for tracks in self.paint_tracks:
            paint = {field: track(frame) for field, track in tracks.items()}
            paint['baseColor'] = list(paint['baseColor'])
            paints.append(paint)
        return paints

    def switches(self, frame):
        return [{'id': switch_id, 'materialId': material_track(frame), 'emissiveScale': emissive_track(frame)}
                for switch_id, material_track, emissive_track in self.switch_tracks]


def _read_texture_versions(prim, texture_meta):
    versions = [[] for _ in texture_meta]
    attr = prim.GetAttribute('bng:textures')
    for frame in attr.GetTimeSamples():
        for index, asset_path in enumerate(attr.Get(frame)):
            if versions[index] and versions[index][-1][1]['asset'] == asset_path.path:
                continue
            data = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(asset_path.resolvedPath)).GetBuffer()
            versions[index].append((int(frame), dict(
                texture_meta[index],
                index=index,
                mime='image/png' if asset_path.path.lower().endswith('.png') else 'image/jpeg',
                asset=asset_path.path,
                data=bytes(data),
            )))
    return versions


def _primvar(prim, name, dtype, width=None):
    value = np.asarray(UsdGeom.PrimvarsAPI(prim).GetPrimvar(name).Get(), dtype=dtype)
    return value if width is None else value.reshape(-1, width)


def _normal_alignment(vertices, faces, normals):
    triangles = vertices[faces]
    winding = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    face_normals = normals[faces].mean(axis=1)
    lengths = np.linalg.norm(winding, axis=1) * np.linalg.norm(face_normals, axis=1)
    valid = lengths > 1e-12
    if not np.any(valid):
        return 0.0
    dots = np.einsum('ij,ij->i', winding[valid], face_normals[valid])
    return np.abs(dots / lengths[valid]).mean()


def _fix_legacy_directions(vertices, faces, normals, tangents):
    # Early takes stored directions in node space while their vertices used skin axes.
    converted = world_to_skin(normals)
    if _normal_alignment(vertices, faces, converted) <= _normal_alignment(vertices, faces, normals) + 0.1:
        return normals, tangents
    tangents = tangents.copy()
    tangents[:, :3] = world_to_skin(tangents[:, :3])
    return converted, tangents


def _read_mesh(prim):
    mesh = UsdGeom.Mesh(prim)
    vertices = world_to_skin(np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float32))
    faces = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.uint32).reshape(-1, 3)
    normals = world_to_skin(np.asarray(mesh.GetNormalsAttr().Get(), dtype=np.float32))
    tangents = _primvar(prim, 'bng:tangents', np.float32, 4)
    normals, tangents = _fix_legacy_directions(vertices, faces, normals, tangents)
    return {
        'name': prim.GetAttribute('bng:name').Get(),
        'verts': vertices,
        'faces': faces,
        'normals': normals,
        'tangents': tangents,
        'uvs': flip_uv_v(_primvar(prim, 'st', np.float32)),
        'uvs2': flip_uv_v(_primvar(prim, 'st1', np.float32)),
        'material_ids': _primvar(prim, 'bng:materialIds', np.uint32),
        'switch_ids': _primvar(prim, 'bng:switchIds', np.int32),
    }


def _read_part(prim):
    part = _read_mesh(prim)
    nodes = _primvar(prim, 'bng:skinNodes', np.int32, 4)
    coords = _primvar(prim, 'bng:skinCoords', np.float32, 3)
    coords_clipped = np.clip(coords, 0.0, 1.0)
    part.update(
        ref_idx=nodes[:, 0],
        px_idx=nodes[:, 1],
        py_idx=nodes[:, 2],
        pz_idx=np.where(nodes[:, 3] >= 0, nodes[:, 3], nodes[:, 0]),
        pz_valid=nodes[:, 3] >= 0,
        coords_clipped=coords_clipped,
        coords_residual=coords - coords_clipped,
    )
    return part


def _read_prop(xform_prim):
    # The writer bakes bind and live init matrices into the sampled transform
    mesh_prim = xform_prim.GetChild('Mesh')
    return dict(_read_mesh(mesh_prim), pid=mesh_prim.GetAttribute('bng:pid').Get(),
                bind_matrix=IDENTITY_M12, live_init_matrix=IDENTITY_M12)
