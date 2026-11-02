# Whole vehicle body as one BngVehicleMeshComponent: one vertex per unique (skin vertex, UV0, UV1) and one index range per (material, switch) section.

import numpy as np
import unreal

import bnglink
import mesh
import transforms

_body_cache = {}


def _concat(parts, field):
    return np.concatenate([part[field] for part in parts])


def _merged_faces(parts):
    faces = []
    offset = 0
    for part in parts:
        faces.append(part['faces'].astype(np.int64) + offset)
        offset += len(part['verts'])
    return np.concatenate(faces)


def _sections(section_keys, triangle_counts, indices):
    sections = []
    first_triangle = 0
    for (material_id, switch_id), count in zip(section_keys, triangle_counts):
        section_indices = indices[first_triangle * 3:(first_triangle + count) * 3]
        sections.append({
            'material_id': int(material_id),
            'switch_id': int(switch_id),
            'first_index': int(first_triangle * 3),
            'num_triangles': int(count),
            'min_vertex_index': int(section_indices.min()),
            'max_vertex_index': int(section_indices.max()),
        })
        first_triangle += count
    return sections


def build_body(parts, geometry_key):
    cached = _body_cache.get(geometry_key)
    if cached is not None:
        return cached

    face_keys = np.column_stack((_concat(parts, 'material_ids'), _concat(parts, 'switch_ids')))
    section_keys, face_sections = np.unique(face_keys, axis=0, return_inverse=True)
    face_sections = face_sections.reshape(-1)
    face_order = np.argsort(face_sections, kind='stable')

    corners = _merged_faces(parts)[face_order].reshape(-1)
    corner_uvs = _concat(parts, 'uvs').reshape(-1, 3, 2)[face_order].reshape(-1, 2)
    corner_uvs2 = _concat(parts, 'uvs2').reshape(-1, 3, 2)[face_order].reshape(-1, 2)
    vertex_corners, indices = mesh.unique_corners(corners, corner_uvs, corner_uvs2)
    indices = indices.astype(np.uint32)
    source = corners[vertex_corners]

    skin, _, skin_vertex_count = bnglink.merge_part_skins(parts)
    # Arrays are passed to the component by address, so they must be C-contiguous
    face_nodes = bnglink.face_locator_nodes(parts)[face_order]
    body = {
        'positions': np.ascontiguousarray(transforms.bng_to_unreal(_concat(parts, 'verts')[source]), dtype=np.float32),
        'normals': np.ascontiguousarray(transforms.directions_bng_to_unreal(_concat(parts, 'normals')[source]), dtype=np.float32),
        'tangents': transforms.tangents_bng_to_unreal(_concat(parts, 'tangents')[source]).astype(np.float32),
        'uvs': np.column_stack((corner_uvs[vertex_corners], corner_uvs2[vertex_corners])).astype(np.float32),
        'indices': indices,
        'base_indices': indices.copy(),
        'sections': _sections(section_keys, np.bincount(face_sections, minlength=len(section_keys)), indices),
        'source': source.astype(np.int32),
        'skin': skin,
        'skin_vertex_count': skin_vertex_count,
        'face_node_index': bnglink.build_face_node_index(face_nodes),
    }
    _body_cache[geometry_key] = body
    return body


def _section_struct(section):
    struct = unreal.BngVehicleMeshSection()
    struct.first_index = section['first_index']
    struct.num_triangles = section['num_triangles']
    struct.min_vertex_index = section['min_vertex_index']
    struct.max_vertex_index = section['max_vertex_index']
    return struct


def create_component(actor, body, materials):
    component = unreal.BngVehicleMeshComponent(actor, 'Body_Mesh')
    actor.set_editor_property('root_component', component)
    if not unreal.BngLinkBridge.register_instance_component(actor, component):
        raise RuntimeError('BngVehicleMeshComponent registration failed')
    initialized = component.init_mesh(
        len(body['positions']),
        body['positions'].ctypes.data,
        body['normals'].ctypes.data,
        body['tangents'].ctypes.data,
        body['uvs'].ctypes.data,
        len(body['indices']),
        body['indices'].ctypes.data,
        [_section_struct(section) for section in body['sections']],
    )
    if not initialized:
        raise RuntimeError('BngVehicleMeshComponent.init_mesh failed')
    for section_index, section in enumerate(body['sections']):
        component.set_material(section_index, materials[section['material_id']])
    return component
