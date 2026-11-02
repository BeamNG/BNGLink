import math
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

import bnglink
import materials
import mesh
import textures
import transforms

# Max node drift in meters the body may accumulate before its mesh is re-uploaded
# 0 re-uploads every frame
RIGID_TOLERANCE = 0.0005
ROOT_ROTATION = Matrix.Rotation(math.pi / 2, 4, 'X')
IDENTITY = Matrix.Identity(4)

world_origin = None
# Mesh data per (geometry key, mesh name), copied when the same vehicle geometry is spawned again
mesh_templates = {}


def empty_vehicle_state(root):
    return {
        'root': root,
        'body': None,
        'props': [],
        'images': [],
        'materials': [],
        'material_state': bnglink.MaterialState(),
        'switch_materials': {},
        'active_switch_materials': {},
        'switch_targets': {},
        'switch_base_materials': {},
        'paint_nodes': [],
        'merged_skin': None,
        'rigid': None,
        'broken_beams': set(),
        'hidden_props': set(),
        'damage_revision': -1,
        'vehicle_matrix': Matrix.Identity(4),
        'lifecycle': 'idle',
    }


def get_vehicle_state(vehicle_states, vehicle_id):
    if vehicle_id not in vehicle_states:
        root = mesh.get_or_create_empty(f'BeamNGVehicle_{vehicle_id}')
        root.parent = mesh.get_or_create_empty('BeamNG')
        vehicle_states[vehicle_id] = empty_vehicle_state(root)
    return vehicle_states[vehicle_id]


def remove_vehicle(vehicle_states, vehicle_id):
    global world_origin
    state = vehicle_states.pop(vehicle_id, None)
    if state is None:
        return
    state['lifecycle'] = 'removed'
    mesh.clear_vehicle_meshes(state)
    bpy.data.objects.remove(state['root'], do_unlink=True)
    if not vehicle_states:
        world_origin = None


def reset_vehicles(vehicle_states):
    for vehicle_id in list(vehicle_states):
        remove_vehicle(vehicle_states, vehicle_id)


def _spawn_mesh(state, entry, bpy_materials, geometry_key):
    name = entry['name']
    template_key = (geometry_key, name)
    template = mesh_templates.get(template_key)
    if template is not None:
        mesh_data = template.copy()
        mesh_data.materials.clear()
        switch_slots = mesh.assign_face_materials(mesh_data, entry['material_ids'], entry['switch_ids'], bpy_materials)
        obj = bpy.data.objects.new(name, mesh_data)
        bpy.context.scene.collection.objects.link(obj)
        mesh.add_smooth_by_angle_modifier(obj)
    else:
        obj, switch_slots = mesh.create_mesh_object(
            name, entry['verts'], entry['faces'], normals=entry['normals'],
            uvs=entry['uvs'], uvs2=entry['uvs2'],
            material_ids=entry['material_ids'], switch_ids=entry['switch_ids'],
            materials=bpy_materials,
        )
        template_mesh = obj.data.copy()
        template_mesh.materials.clear()
        mesh_templates[template_key] = template_mesh
    obj.parent = state['root']
    for switch_id, slots in switch_slots.items():
        state['switch_targets'].setdefault(switch_id, []).append((obj, slots))
        for slot in slots:
            state['switch_base_materials'][(obj, slot)] = obj.data.materials[slot]
    return obj


def _concat_part_field(parts, field):
    return np.concatenate([part[field] for part in parts])


def merge_parts(parts):
    faces = []
    fields = {field: [] for field in bnglink.SKIN_FIELDS}
    vertices = []
    normals = []
    tangents = []
    offset = 0
    for part in parts:
        source = part['faces'].reshape(-1)
        count = len(source)
        vertices.append(part['verts'][source])
        normals.append(part['normals'][source])
        tangents.append(part['tangents'][source])
        faces.append(np.arange(offset, offset + count, dtype=np.int32).reshape(-1, 3))
        for field in fields:
            fields[field].append(part[field][source])
        offset += count

    entry = {
        'name': 'Body',
        'verts': np.concatenate(vertices),
        'faces': np.concatenate(faces),
        'normals': np.concatenate(normals),
        'tangents': np.concatenate(tangents),
        'uvs': _concat_part_field(parts, 'uvs'),
        'uvs2': _concat_part_field(parts, 'uvs2'),
        'material_ids': _concat_part_field(parts, 'material_ids'),
        'switch_ids': _concat_part_field(parts, 'switch_ids'),
    }
    return entry, {field: np.concatenate(values) for field, values in fields.items()}


def spawn_body(state, entry, material_defs, bpy_materials, geometry_key, face_node_index):
    obj = _spawn_mesh(state, entry, bpy_materials, geometry_key)
    variant_faces = bnglink.build_damage_variant_index(
        entry['material_ids'], entry['switch_ids'], material_defs)
    variant_hidden_faces = np.zeros(len(entry['faces']), dtype=bool)
    state['material_state'].update_hidden_variants(
        variant_faces, variant_hidden_faces)
    positions = mesh.position_view(obj.data)
    triangles = positions.reshape(-1, 3, 3)
    triangles[variant_hidden_faces, 1:] = triangles[variant_hidden_faces, :1]
    mesh.refresh_mesh(obj.data, obj)
    return {
        'obj': obj,
        'mesh': obj.data,
        'positions': positions,
        'moved': False,
        'face_node_index': face_node_index,
        'hidden_faces': np.zeros(len(entry['faces']), dtype=bool),
        'variant_faces': variant_faces,
        'variant_hidden_faces': variant_hidden_faces,
        'damage_dirty': False,
    }


def build_rigid_state(skin, vertex_count, node_count):
    # The body is classified as one unit: it is a single mesh, so re-uploading any of it costs all of it
    node_idx, node_start = bnglink.part_node_sets(skin, [(0, vertex_count)])
    return {
        'ready': False,
        'nodes': np.empty((node_count, 3)),
        'vehicle_ref': np.empty((node_count, 3)),
        'vehicle_q': np.array((1.0, 0.0, 0.0, 0.0)),
        'vehicle_t': np.zeros(3),
        'node_idx': node_idx,
        'node_start': node_start,
        'ref_nodes': np.empty((len(node_idx), 3)),
        'body_q': np.zeros((1, 4)),
        'body_t': np.zeros((1, 3)),
        'mode': np.empty(1, dtype=np.int8),
        'xform': np.empty((3, 4)),
    }


def reset_rigid(rigid, node_raw):
    # The current frame becomes the vehicle-space reference, the body is re-uploaded
    bnglink.nodes_to_skin_axes(node_raw, rigid['vehicle_ref'])
    rigid['ref_nodes'][:] = rigid['vehicle_ref'][rigid['node_idx']]
    rigid['vehicle_q'][:] = (1.0, 0.0, 0.0, 0.0)
    rigid['vehicle_t'][:] = 0.0
    rigid['body_q'][:] = (1.0, 0.0, 0.0, 0.0)
    rigid['body_t'][:] = 0.0
    rigid['mode'][:] = bnglink.RIGID_RESKIN
    rigid['ready'] = bool(np.any(node_raw))


def build_meshes(init, vehicle_states, tex_dir):
    state = get_vehicle_state(vehicle_states, init['vehicle_id'])
    state['lifecycle'] = 'preparing'
    mesh.clear_vehicle_meshes(state)

    images = textures.save_textures(init['textures'], tex_dir)
    state['images'] = images
    material_results = [materials.create_bpy_material(m, images, init['paints']) for m in init['materials']]
    bpy_materials = [result[0] for result in material_results]
    state['materials'] = bpy_materials
    state['material_state'] = bnglink.MaterialState()
    state['switch_materials'] = {}
    state['active_switch_materials'] = {}
    state['switch_targets'] = {}
    state['switch_base_materials'] = {}
    state['paint_nodes'] = [entry for _, entries in material_results for entry in entries]

    parts = init['parts']
    geometry_key = init['geometry_key']
    body_entry, merged_skin = merge_parts(parts) if parts else (None, None)
    vertex_count = len(body_entry['verts']) if body_entry else 0
    state['merged_skin'] = merged_skin
    state['body'] = spawn_body(
        state, body_entry, init['materials'], bpy_materials, geometry_key,
        bnglink.build_face_node_index(bnglink.face_locator_nodes(parts))) if parts else None
    state['rigid'] = build_rigid_state(merged_skin, vertex_count, len(init['node_ids'])) if parts else None
    state['broken_beams'] = set()
    state['hidden_props'] = set()
    state['damage_revision'] = -1
    state['vehicle_matrix'] = Matrix.Identity(4)

    for prop in init['props']:
        state['props'].append({
            'obj': _spawn_mesh(state, prop, bpy_materials, geometry_key),
            'pid': prop['pid'],
            'offset': transforms.prop_offset(prop['bind_matrix'], prop['live_init_matrix']),
        })

    state['root'].rotation_euler = (math.pi / 2, 0, 0)
    state['root'].update_tag(refresh={'OBJECT'})
    state['lifecycle'] = 'ready'

    print(
        f'BNGLink Blender: Vehicle content built: {len(parts)} parts in one body mesh, '
        f'{len(state["props"])} props, {len(bpy_materials)} materials'
    )


def update_paint(state, paints):
    materials.apply_paints(state['paint_nodes'], paints)
    node_trees = {entry['node'].id_data for entry in state['paint_nodes']}
    for node_tree in node_trees:
        node_tree.update_tag()
    return bool(node_trees)


def _apply_material_switch(state, switch_id):
    changed = False
    material_id, emissive_scale = state['material_state'].resolve(switch_id)
    if material_id is None or material_id < 0:
        state['active_switch_materials'].pop(switch_id, None)
        for obj, slots in state['switch_targets'].get(switch_id, []):
            for slot in slots:
                obj.data.materials[slot] = state['switch_base_materials'][(obj, slot)]
                changed = True
        return changed

    key = (switch_id, material_id)
    cached = state['switch_materials'].get(key)
    if cached is None:
        material = state['materials'][material_id].copy()
        material.name = f'{material.name}_Switch_{switch_id}'
        strengths = [
            node.inputs['Emission Strength']
            for node in material.node_tree.nodes
            if node.type == 'BSDF_PRINCIPLED' and node.inputs.get('Emission Strength')
        ]
        cached = (material, strengths)
        state['switch_materials'][key] = cached
    material, strengths = cached
    if strengths:
        for strength in strengths:
            strength.default_value = emissive_scale
        material.node_tree.update_tag()
        changed = True
    if state['active_switch_materials'].get(switch_id) != material_id:
        state['active_switch_materials'][switch_id] = material_id
        for obj, slots in state['switch_targets'].get(switch_id, []):
            for slot in slots:
                obj.data.materials[slot] = material
                changed = True
    return changed


def update_materials(state, switches):
    changed = False
    changed_switches = state['material_state'].update(switches)
    for switch_id in changed_switches:
        if _apply_material_switch(state, switch_id):
            changed = True
    body = state['body']
    if body is not None and state['material_state'].update_hidden_variants(
            body['variant_faces'], body['variant_hidden_faces'], changed_switches):
        body['damage_dirty'] = True
        changed = True
    return changed


def apply_damage(state, sync):
    revision = sync.get('damage_revision')
    if revision is not None and revision == state['damage_revision']:
        return
    if revision is not None:
        state['damage_revision'] = revision
    broken = {tuple(map(int, beam)) for beam in sync['broken_beams']}
    changed_switches = state['material_state'].replace_damage(sync['damaged_switches'])
    for switch_id in changed_switches:
        _apply_material_switch(state, switch_id)
    body = state['body']
    if body is not None:
        if state['material_state'].update_hidden_variants(
                body['variant_faces'], body['variant_hidden_faces'], changed_switches):
            body['damage_dirty'] = True
    if broken != state['broken_beams'] and body is not None:
        if bnglink.update_hidden_faces(
                body['face_node_index'], state['broken_beams'], broken, body['hidden_faces']):
            body['damage_dirty'] = True
    state['broken_beams'] = broken

    hidden_props = {int(pid) for pid in sync['hidden_props']}
    if hidden_props != state['hidden_props']:
        for prop in state['props']:
            hidden = prop['pid'] in hidden_props
            prop['obj'].hide_viewport = hidden
            prop['obj'].hide_render = hidden
        state['hidden_props'] = hidden_props


def _rigid_matrix(q, t):
    matrix = Quaternion(q).to_matrix().to_4x4()
    matrix.translation = t
    return matrix


def apply_body(state, sync, decoder, stats=None):
    # stats: optional dict of bench accumulators (seconds for timings, totals for body counts)
    if state['merged_skin'] is None:
        return
    rigid = state['rigid']
    body = state['body']
    start = time.perf_counter()
    if RIGID_TOLERANCE <= 0.0:
        rigid['mode'][0] = bnglink.RIGID_RESKIN
    elif rigid['ready']:
        bnglink.rigidify(
            sync['node_raw'], rigid['vehicle_ref'], rigid['vehicle_q'], rigid['vehicle_t'],
            rigid['nodes'], rigid['node_idx'], rigid['node_start'], rigid['ref_nodes'],
            rigid['body_q'], rigid['body_t'], RIGID_TOLERANCE, rigid['mode'],
        )
    else:
        reset_rigid(rigid, sync['node_raw'])
    start = bnglink.lap(stats, 'rigid', start)

    mode = rigid['mode'][0]
    if body['damage_dirty']:
        mode = rigid['mode'][0] = bnglink.RIGID_RESKIN
    if mode == bnglink.RIGID_RESKIN:
        # The mesh buffer has the merged skin layout, so it doubles as the deform target,
        # and the deform writes straight into vehicle space
        bnglink.vehicle_space_xform(rigid['vehicle_q'], rigid['vehicle_t'], rigid['xform'])
        decoder.deform_body(sync['node_raw'], state['merged_skin'], body['positions'], rigid['xform'])
        triangles = body['positions'].reshape(-1, 3, 3)
        triangles[body['hidden_faces'], 1:] = triangles[body['hidden_faces'], :1]
        triangles[body['variant_hidden_faces'], 1:] = triangles[body['variant_hidden_faces'], :1]
        body['damage_dirty'] = False
        start = bnglink.lap(stats, 'skin', start)
        if body['moved']:
            body['obj'].matrix_local = IDENTITY
            body['moved'] = False
        mesh.refresh_mesh(body['mesh'], body['obj'])
        start = bnglink.lap(stats, 'mesh', start)
    elif mode == bnglink.RIGID_MOVED:
        body['obj'].matrix_local = _rigid_matrix(rigid['body_q'][0], rigid['body_t'][0])
        body['moved'] = True

    state['vehicle_matrix'] = _rigid_matrix(rigid['vehicle_q'], rigid['vehicle_t'])
    bnglink.lap(stats, 'body_xform', start)

    if stats is not None:
        stats['bodies'] += 1
        stats['moved'] += int(mode == bnglink.RIGID_MOVED)
        stats['reskinned'] += int(mode == bnglink.RIGID_RESKIN)


def apply_transforms(state, sync):
    global world_origin
    if world_origin is None:
        world_origin = sync['origin']
    location = [pos - ref for pos, ref in zip(sync['origin'], world_origin)]
    vehicle_matrix = state['vehicle_matrix']
    state['root'].matrix_local = Matrix.Translation(location) @ ROOT_ROTATION @ vehicle_matrix
    state['root'].update_tag(refresh={'OBJECT'})
    vehicle_inverse = vehicle_matrix.inverted()
    for prop_state, mat12 in zip(state['props'], sync['prop_mats']):
        live_bng = prop_state['offset'] @ transforms.mat12_to_matrix(mat12)
        prop_obj = prop_state['obj']
        prop_obj.matrix_local = vehicle_inverse @ transforms.prop_root_local(live_bng)
        prop_obj.update_tag(refresh={'OBJECT'})
