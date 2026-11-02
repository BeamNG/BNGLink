import time

import numpy as np
import unreal

import bnglink
import body_mesh
import gpu_skin
import materials
import mesh
import textures
import transforms

# Vehicle-space vertex moves below this, in cm, are not uploaded
MOVEMENT_EPSILON = 0.01

world_origin = None


def empty_vehicle_state(root):
    return {
        'root': root,
        'actors': [],
        'props': [],
        'body': None,
        'prop_handle': None,
        'prop_offsets': np.empty((0, 4, 4), dtype=np.float64),
        'texture_assets': [],
        'material_assets': [],
        'base_materials': [],
        'paint_params': [],
        'material_state': bnglink.MaterialState(),
        'switch_materials': {},
        'active_switch_materials': {},
        'switch_targets': {},
        'material_sections': {},
        'broken_beams': set(),
        'hidden_props': set(),
        'damage_revision': -1,
        'lifecycle': 'idle',
    }


def register_material_sections(state, component, section_keys):
    state['material_sections'][component] = section_keys
    for section_index, (_, switch_id) in enumerate(section_keys):
        if switch_id >= 0:
            state['switch_targets'].setdefault(switch_id, []).append((component, section_index))


def get_world_empty():
    root = getattr(unreal, '_bng_link_world_empty', None)
    if root is None or not unreal.SystemLibrary.is_valid(root):
        root = mesh.spawn_actor('BeamNG')
        unreal._bng_link_world_empty = root
    return root


def get_vehicle_state(vehicle_states, vehicle_id):
    if vehicle_id not in vehicle_states:
        root = mesh.spawn_actor(f'BeamNGVehicle_{vehicle_id}')
        mesh.attach_to_parent(root, get_world_empty())
        vehicle_states[vehicle_id] = empty_vehicle_state(root)
    return vehicle_states[vehicle_id]


def remove_vehicle(vehicle_states, vehicle_id):
    global world_origin
    state = vehicle_states.pop(vehicle_id, None)
    if state is None:
        return
    state['lifecycle'] = 'removed'
    if state['prop_handle'] is not None:
        unreal.BngLinkBridge.unregister_prop_group(state['prop_handle'])
    mesh.clear_vehicle_meshes(state)
    mesh.actor_subsystem().destroy_actor(state['root'])
    if not vehicle_states:
        world_origin = None


def reset_vehicles(vehicle_states):
    for vehicle_id in list(vehicle_states):
        remove_vehicle(vehicle_states, vehicle_id)


def spawn_body(state, parts, node_count, material_defs, unreal_materials, geometry_key):
    actor = mesh.spawn_actor('Body')
    actor.set_actor_enable_collision(False)
    template = body_mesh.build_body(parts, geometry_key)
    section_counts = [section['num_triangles'] for section in template['sections']]
    material_ids = np.repeat(
        [section['material_id'] for section in template['sections']], section_counts)
    switch_ids = np.repeat(
        [section['switch_id'] for section in template['sections']], section_counts)
    variant_faces = bnglink.build_damage_variant_index(
        material_ids, switch_ids, material_defs)
    variant_hidden_faces = np.zeros(len(material_ids), dtype=bool)
    state['material_state'].update_hidden_variants(
        variant_faces, variant_hidden_faces)
    body = dict(
        template,
        indices=template['base_indices'].copy(),
        hidden_faces=np.zeros(len(template['base_indices']) // 3, dtype=bool),
        variant_faces=variant_faces,
        variant_hidden_faces=variant_hidden_faces,
    )
    triangles = body['indices'].reshape(-1, 3)
    source = body['base_indices'].reshape(-1, 3)
    triangles[variant_hidden_faces] = source[variant_hidden_faces, :1]
    component = body_mesh.create_component(actor, body, unreal_materials)
    mesh.attach_to_parent(actor, state['root'])
    section_keys = [(section['material_id'], section['switch_id']) for section in body['sections']]
    register_material_sections(state, component, section_keys)
    state['actors'].append(actor)
    state['body'] = {
        'component': component,
        'skin': body['skin'],
        'source': body['source'],
        'rest_positions': body['positions'],
        'normals': body['normals'],
        'tangents': body['tangents'],
        'bind_deform': np.empty((body['skin_vertex_count'], 3), dtype=np.float32),
        'bind_positions': np.empty_like(body['positions']),
        'nodes': np.empty((node_count, 3), dtype=np.float32),
        'vehicle_nodes': np.empty((node_count, 3), dtype=np.float32),
        'uploaded_nodes': np.full((node_count, 3), np.inf, dtype=np.float32),
        'vehicle_ref': None,
        'vehicle_q': np.array((1.0, 0.0, 0.0, 0.0)),
        'vehicle_t': np.zeros(3),
        'indices': body['indices'],
        'base_indices': body['base_indices'],
        'face_node_index': body['face_node_index'],
        'hidden_faces': body['hidden_faces'],
        'variant_faces': body['variant_faces'],
        'variant_hidden_faces': body['variant_hidden_faces'],
    }


def setup_props(state):
    props = state['props']
    if state['prop_handle'] is not None:
        unreal.BngLinkBridge.unregister_prop_group(state['prop_handle'])
        state['prop_handle'] = None
    if not props:
        state['prop_offsets'] = np.empty((0, 4, 4), dtype=np.float64)
        return
    state['prop_offsets'] = np.stack([prop['offset'] for prop in props])
    state['prop_handle'] = unreal.BngLinkBridge.register_prop_group(
        [prop['actor'].root_component for prop in props])


def spawn_prop(state, prop, unreal_materials):
    actor = mesh.create_mesh_actor(prop['name'], state['root'])
    section_keys = mesh.add_material_sections(
        actor.root_component,
        transforms.bng_to_unreal(prop['verts']),
        transforms.directions_bng_to_unreal(prop['normals']),
        transforms.tangents_bng_to_unreal(prop['tangents']),
        prop['faces'], prop['uvs'], prop['uvs2'], prop['material_ids'], prop['switch_ids'],
        unreal_materials,
    )
    register_material_sections(state, actor.root_component, section_keys)
    state['actors'].append(actor)
    state['props'].append({
        'actor': actor,
        'pid': prop['pid'],
        'offset': transforms.prop_offset(prop['bind_matrix'], prop['live_init_matrix']),
    })


def build_meshes(init, vehicle_states, tex_dir, refresh_textures=True, persistent=False):
    state = get_vehicle_state(vehicle_states, init['vehicle_id'])
    state['lifecycle'] = 'preparing'
    mesh.clear_vehicle_meshes(state)

    texture_assets = textures.import_textures(
        init['vehicle_id'], init['textures'], init['materials'], tex_dir,
        refresh=refresh_textures, save=persistent)
    state['texture_assets'] = texture_assets
    material_results = [
        materials.create_material(
            material_def, init['paints'], texture_assets, persistent=persistent)
        for material_def in init['materials']
    ]
    unreal_materials = [material for material, _, _ in material_results]
    state['material_assets'] = [parent for _, _, parent in material_results]
    state['base_materials'] = unreal_materials
    state['paint_params'] = [] if persistent else [(instance, params) for instance, params, _ in material_results]
    state['material_state'] = bnglink.MaterialState()
    state['switch_materials'] = {}
    state['active_switch_materials'] = {}
    state['switch_targets'] = {}
    state['material_sections'] = {}
    state['broken_beams'] = set()
    state['hidden_props'] = set()
    state['damage_revision'] = -1

    spawn_body(
        state, init['parts'], len(init['node_ids']), init['materials'],
        unreal_materials, init['geometry_key'])
    for prop in init['props']:
        spawn_prop(state, prop, unreal_materials)
    setup_props(state)
    state['lifecycle'] = 'ready'

    print(
        f'BNGLink Unreal: Vehicle content built: {len(init["parts"])} parts, '
        f'{len(state["props"])} props, {len(unreal_materials)} materials'
    )


def update_paint(state, paints):
    for material, params in state['paint_params']:
        materials.apply_paints(material, params, paints)


def _update_body_indices(body, update_component=True):
    triangles = body['indices'].reshape(-1, 3)
    source = body['base_indices'].reshape(-1, 3)
    hidden = body['hidden_faces'] | body['variant_hidden_faces']
    triangles[:] = source
    triangles[hidden] = source[hidden, :1]
    if update_component and not body['component'].update_indices(
            len(body['indices']), body['indices'].ctypes.data):
        raise RuntimeError('BngVehicleMeshComponent.update_indices failed')


def _apply_material_switch(state, switch_id):
    material_id, emissive_scale = state['material_state'].resolve(switch_id)
    if material_id is None or material_id < 0:
        state['active_switch_materials'].pop(switch_id, None)
        for component, section_index in state['switch_targets'].get(switch_id, []):
            base_material_id = state['material_sections'][component][section_index][0]
            component.set_material(section_index, state['base_materials'][base_material_id])
        return

    key = (switch_id, material_id)
    material = state['switch_materials'].get(key)
    if material is None:
        material = materials.create_dynamic_instance(state['material_assets'][material_id])
        state['switch_materials'][key] = material
    material.set_scalar_parameter_value(materials.EMISSIVE_SCALE_PARAMETER, emissive_scale)
    if state['active_switch_materials'].get(switch_id) == material_id:
        return
    state['active_switch_materials'][switch_id] = material_id
    for component, section_index in state['switch_targets'].get(switch_id, []):
        component.set_material(section_index, material)


def update_materials(state, switches):
    changed = state['material_state'].update(switches)
    for switch_id in changed:
        _apply_material_switch(state, switch_id)
    body = state['body']
    if state['material_state'].update_hidden_variants(
            body['variant_faces'], body['variant_hidden_faces'], changed):
        _update_body_indices(body)


def apply_damage(state, sync, update_component=True):
    revision = sync.get('damage_revision')
    if revision is not None and revision == state['damage_revision']:
        return False
    if revision is not None:
        state['damage_revision'] = revision
    broken = {tuple(map(int, beam)) for beam in sync['broken_beams']}
    body = state['body']
    changed_switches = state['material_state'].replace_damage(sync['damaged_switches'])
    for switch_id in changed_switches:
        _apply_material_switch(state, switch_id)
    variants_changed = state['material_state'].update_hidden_variants(
        body['variant_faces'], body['variant_hidden_faces'], changed_switches)
    faces_changed = False
    if broken != state['broken_beams']:
        faces_changed = bnglink.update_hidden_faces(
            body['face_node_index'], state['broken_beams'], broken, body['hidden_faces'])
    if faces_changed or variants_changed:
        _update_body_indices(body, update_component)
    state['broken_beams'] = broken

    hidden_props = {int(pid) for pid in sync['hidden_props']}
    if hidden_props != state['hidden_props']:
        if update_component:
            for prop in state['props']:
                hidden = prop['pid'] in hidden_props
                prop['actor'].set_actor_hidden_in_game(hidden)
                prop['actor'].set_is_temporarily_hidden_in_editor(hidden)
        state['hidden_props'] = hidden_props
    return faces_changed or variants_changed


def _bind_body(body, sync, decoder, q, t):
    decoder.deform_body(sync['node_raw'], body['skin'], body['bind_deform'])
    transforms.skin_to_vehicle_space(body['bind_deform'], body['source'], q, t, body['bind_positions'])
    bnglink.fit_vehicle_frame(body['rest_positions'], body['bind_positions'], q, t)
    body['vehicle_ref'] = body['nodes'].copy()
    bnglink.to_vehicle_space(body['vehicle_ref'], q, t, body['vehicle_ref'])
    skin_vertices = gpu_skin.build_skin_vertices(
        body['skin'], body['source'], body['normals'], body['tangents'], body['vehicle_ref'])
    if not body['component'].bind_skin(len(body['vehicle_ref']), skin_vertices.ctypes.data, len(skin_vertices)):
        raise RuntimeError('BngVehicleMeshComponent.bind_skin failed')


def apply_body(state, sync, decoder, stats=None, update_component=True):
    body = state['body']
    q = body['vehicle_q']
    t = body['vehicle_t']
    start = time.perf_counter()
    nodes = body['nodes']
    transforms.nodes_to_unreal(sync['node_raw'], nodes)
    if body['vehicle_ref'] is None:
        _bind_body(body, sync, decoder, q, t)
    else:
        bnglink.fit_vehicle_frame(body['vehicle_ref'], nodes, q, t)
    start = bnglink.lap(stats, 'vehicle_frame', start)

    vehicle_nodes = body['vehicle_nodes']
    bnglink.to_vehicle_space(nodes, q, t, vehicle_nodes)
    if not update_component:
        return

    changed = bnglink.positions_changed(vehicle_nodes, body['uploaded_nodes'], MOVEMENT_EPSILON)
    body['component'].update_mesh(
        unreal.Quat(q[1], q[2], q[3], q[0]),
        unreal.Vector(t[0], t[1], t[2]),
        vehicle_nodes.ctypes.data if changed else 0,
        len(vehicle_nodes),
    )
    bnglink.lap(stats, 'upload', start)
    if stats is not None:
        stats['uploads'] += changed


def apply_transforms(state, sync, reference_origin=None):
    global world_origin
    if reference_origin is None:
        if world_origin is None:
            world_origin = sync['origin']
        reference_origin = world_origin

    state['root'].set_actor_relative_location(
        transforms.relative_location_to_unreal(sync['origin'], reference_origin),
        False,
        False,
    )

    if state['prop_handle'] is not None:
        ue_mats = transforms.ue_prop_matrices(sync['prop_mats'], state['prop_offsets'])
        unreal.BngLinkBridge.apply_prop_transforms(
            state['prop_handle'], ue_mats.ctypes.data, ue_mats.shape[0])
