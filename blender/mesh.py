import ctypes
import math

import bpy
import numpy as np

SMOOTH_BY_ANGLE = math.radians(30)
_smooth_by_angle_group = None


def _angle_socket_id(node_group):
    for item in node_group.interface.items_tree:
        if getattr(item, 'in_out', None) == 'INPUT' and item.name == 'Angle':
            return item.identifier
    raise RuntimeError('Smooth by Angle group has no Angle input')


def _set_smooth_angle(mod):
    # 5.2 stores modifier inputs as RNA properties. Earlier versions use ID properties.
    socket_id = _angle_socket_id(mod.node_group)
    if bpy.app.version >= (5, 2, 0):
        getattr(mod.properties.inputs, socket_id).value = SMOOTH_BY_ANGLE
    else:
        mod[socket_id] = SMOOTH_BY_ANGLE


def add_smooth_by_angle_modifier(obj):
    global _smooth_by_angle_group

    if _smooth_by_angle_group is None:
        # Only the operator needs the selection state
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.shade_auto_smooth(angle=SMOOTH_BY_ANGLE)
        mod = obj.modifiers['Smooth by Angle']
        _set_smooth_angle(mod)
        _smooth_by_angle_group = mod.node_group
        return

    polygons = obj.data.polygons
    polygons.foreach_set('use_smooth', np.ones(len(polygons), dtype=bool))
    mod = obj.modifiers.new('Smooth by Angle', 'NODES')
    mod.node_group = _smooth_by_angle_group
    _set_smooth_angle(mod)


def position_view(mesh_data):
    count = len(mesh_data.vertices)
    ptr = mesh_data.attributes['position'].data[0].as_pointer()
    buf = (ctypes.c_float * (count * 3)).from_address(ptr)
    return np.ctypeslib.as_array(buf).reshape(count, 3)


def refresh_mesh(mesh_data, obj):
    mesh_data.update()
    obj.update_tag(refresh={'DATA'})


def get_or_create_empty(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        bpy.context.scene.collection.objects.link(obj)
    return obj


def clear_child_meshes(root):
    for obj in [child for child in root.children if child.type == 'MESH']:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)


def clear_vehicle_meshes(state):
    clear_child_meshes(state['root'])
    state['body'] = None
    state['props'] = []


def assign_face_materials(mesh, material_indices, switch_ids, materials):
    # One slot per (material, switch) pair.
    # The pair is packed into one key to keep the unique 1-D array (faster than np.unique(axis=0) over all faces).
    # Slot order is arbitrary as long as the material list and the per-face indices agree.
    keys = (np.asarray(material_indices, dtype=np.int64) << 32) | (np.asarray(switch_ids, dtype=np.int64) + 1)
    pairs, slots = np.unique(keys, return_inverse=True)
    mesh.polygons.foreach_set('material_index', slots.astype(np.int32))

    switch_slots = {}
    for slot, pair in enumerate(pairs):
        mesh.materials.append(materials[pair >> 32])
        switch_id = int(pair & 0xFFFFFFFF) - 1
        if switch_id >= 0:
            switch_slots.setdefault(switch_id, []).append(slot)
    return switch_slots


def add_uv_layer(mesh, name, uvs):
    # np.array always copies: the decoded arrays are read-only and V is flipped in place.
    uvs = np.array(uvs, dtype=np.float32).reshape(-1, 2)
    uvs[:, 1] = 1.0 - uvs[:, 1]
    mesh.uv_layers.new(name=name).data.foreach_set('uv', uvs.reshape(-1))


def create_mesh_object(
    name, verts, faces, normals=None, uvs=None, uvs2=None,
    material_ids=None, switch_ids=None, materials=None
):
    mesh = bpy.data.meshes.new(name)
    verts = np.ascontiguousarray(verts, dtype=np.float32)
    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set('co', verts.reshape(-1))
    mesh.loops.add(len(faces) * 3)
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set('loop_total', np.full(len(faces), 3, dtype=np.int32))
    mesh.polygons.foreach_set('loop_start', np.arange(0, len(faces) * 3, 3, dtype=np.int32))
    mesh.polygons.foreach_set('vertices', np.ascontiguousarray(faces, dtype=np.int32).reshape(-1))

    if uvs is not None:
        add_uv_layer(mesh, 'uv1', uvs)
    if uvs2 is not None:
        add_uv_layer(mesh, 'uv2', uvs2)

    mesh.validate()

    switch_slots = {}
    if material_ids is not None and materials is not None:
        switch_slots = assign_face_materials(mesh, material_ids, switch_ids, materials)

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    add_smooth_by_angle_modifier(obj)
    if normals is not None:
        mesh.normals_split_custom_set_from_vertices(
            np.ascontiguousarray(normals, dtype=np.float32))
    return obj, switch_slots
