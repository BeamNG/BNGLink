import numpy as np
import unreal


def actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def spawn_actor(name):
    actor = actor_subsystem().spawn_actor_from_class(
        unreal.Actor,
        unreal.Vector(0.0, 0.0, 0.0),
        unreal.Rotator(0.0, 0.0, 0.0),
    )
    actor.set_actor_label(name)
    return actor


def attach_to_parent(actor, parent):
    actor.attach_to_actor(
        parent,
        '',
        unreal.AttachmentRule.KEEP_RELATIVE,
        unreal.AttachmentRule.KEEP_RELATIVE,
        unreal.AttachmentRule.KEEP_RELATIVE,
        False,
    )


def clear_vehicle_meshes(state):
    subsystem = actor_subsystem()
    for actor in state['actors']:
        subsystem.destroy_actor(actor)
    state['actors'] = []
    state['props'] = []


def create_mesh_actor(name, parent):
    actor = spawn_actor(name)
    actor.set_actor_enable_collision(False)
    component = unreal.ProceduralMeshComponent(actor, f'{name}_Mesh')
    actor.set_editor_property('root_component', component)
    if not unreal.BngLinkBridge.register_instance_component(actor, component):
        raise RuntimeError(f'{name} component registration failed')
    attach_to_parent(actor, parent)
    return actor


def _uv_list(uvs):
    return [unreal.Vector2D(float(uv[0]), float(uv[1])) for uv in uvs]


def _vector_list(vectors):
    return [unreal.Vector(float(value[0]), float(value[1]), float(value[2])) for value in vectors]


def _tangent_list(tangents):
    return [
        unreal.ProcMeshTangent(
            tangent_x=unreal.Vector(float(value[0]), float(value[1]), float(value[2])),
            flip_tangent_y=bool(value[3] < 0.0),
        )
        for value in tangents
    ]


def add_mesh_section(component, section_index, verts, normals, tangents, faces, uvs, uvs2):
    component.create_mesh_section_linear_color(
        section_index,
        np.asarray(verts, dtype=np.float64).tolist(),
        np.asarray(faces, dtype=np.int32).reshape(-1).tolist(),
        _vector_list(normals),
        _uv_list(uvs),
        _uv_list(uvs2),
        [],
        [],
        [],
        _tangent_list(tangents),
        False,
    )


def _float_bits(values):
    return np.ascontiguousarray(values, dtype=np.float32).view(np.uint32)


def unique_corners(corners, uvs, uvs2):
    # One vertex per unique (source vertex, UV0, UV1), numbered by first use so sections get compact vertex ranges.
    # Returns the first corner of every vertex and the vertex index of every corner.
    keys = np.column_stack((corners.astype(np.uint32), _float_bits(uvs), _float_bits(uvs2)))
    _, first_corner, corner_vertex = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first_corner)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    return first_corner[order], rank[corner_vertex.reshape(-1)]


def split_sections(verts, normals, tangents, faces, uvs, uvs2, material_ids, switch_ids):
    # Yields material id, switch id and the section buffers for every (material, switch) group of faces
    face_keys = np.column_stack((material_ids, switch_ids))
    for material_id, switch_id in np.unique(face_keys, axis=0):
        face_mask = (face_keys[:, 0] == material_id) & (face_keys[:, 1] == switch_id)
        corner_mask = np.repeat(face_mask, 3)
        corners = faces[face_mask].reshape(-1)
        vertex_corners, indices = unique_corners(corners, uvs[corner_mask], uvs2[corner_mask])
        source = corners[vertex_corners]
        yield int(material_id), int(switch_id), (
            verts[source], normals[source], tangents[source], indices.reshape(-1, 3),
            uvs[corner_mask][vertex_corners], uvs2[corner_mask][vertex_corners],
        )


def add_material_sections(component, verts, normals, tangents, faces, uvs, uvs2, material_ids, switch_ids, materials):
    section_keys = []
    sections = split_sections(verts, normals, tangents, faces, uvs, uvs2, material_ids, switch_ids)
    for section_index, (material_id, switch_id, buffers) in enumerate(sections):
        add_mesh_section(component, section_index, *buffers)
        component.set_material(section_index, materials[material_id])
        section_keys.append((material_id, switch_id))
    return section_keys

