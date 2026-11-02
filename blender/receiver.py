import bpy
import numpy as np

import bnglink
import materials
import textures
import vehicle
import bngusd.recorder as usd_recorder

IDLE_INTERVAL = 0.005

DEBUG = False

redraw_areas = []
session = None
usd = None
# Off while recording leaves the scene untouched, the recorder still gets every sync
live_preview = True


def log_event(message):
    print(f'BNGLink Blender: {message}')


def link():
    # The running link {'session', 'poll', 'usd'}, or None
    return bpy.app.driver_namespace.get('bng_link')


def stop():
    previous = bpy.app.driver_namespace.pop('bng_link', None)
    if previous is None:
        return
    if bpy.app.timers.is_registered(previous['poll']):
        bpy.app.timers.unregister(previous['poll'])
    previous['session'].close()
    if previous['usd'].active:
        previous['usd'].stop()
    log_event('BNGLink stopped')


def clear_textures_cache():
    # Vehicles still in the scene, e.g. imported takes, keep their textures
    textures.clear_cache(materials.prune_cache())
    log_event(f'Textures cache cleared: {textures.TEX_DIR}')


def redraw():
    for area in redraw_areas:
        area.tag_redraw()


def on_init(session, data):
    vehicle_id = data['vehicle_id']
    log_event(f'Loading vehicle {vehicle_id}')
    vehicle.build_meshes(data, session.vehicle_states, textures.TEX_DIR)
    state = session.vehicle_states[vehicle_id]
    zero_sync = {'node_raw': np.zeros((len(data['node_ids']), 3), np.float32)}
    vehicle.apply_body(state, zero_sync, session.decoder)
    session.confirm_ready(vehicle_id)
    log_event(f'Vehicle {vehicle_id} ready')


def on_session_reset(session, data):
    vehicle.reset_vehicles(session.vehicle_states)
    redraw()


def on_vehicle_removed(session, data):
    vehicle.remove_vehicle(session.vehicle_states, data['vehicle_id'])
    redraw()


def on_paint_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is None:
        log_event(f'Paint update ignored for unknown vehicle {data["vehicle_id"]}')
    elif vehicle.update_paint(state, data['paints']):
        redraw()
        log_event(f'Vehicle {data["vehicle_id"]} paint updated')
    else:
        log_event(f'Paint update had no tracked nodes for vehicle {data["vehicle_id"]}')


def on_texture_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is not None:
        textures.update_textures(state['images'], data['textures'])
        redraw()


def on_material_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is not None and vehicle.update_materials(state, data['switches']):
        redraw()


def on_sync(session, syncs):
    if usd.active and not live_preview:
        return
    for sync in syncs:
        state = session.vehicle_states.get(sync['vehicle_id'])
        if state is None or state['lifecycle'] != 'ready':
            continue
        vehicle.apply_damage(state, sync)
        vehicle.apply_body(state, sync, session.decoder)
        vehicle.apply_transforms(state, sync)

    redraw()


def poll():
    if bpy.app.driver_namespace.get('bng_link', {}).get('session') is not session:
        return None
    return 0.0 if session.poll() else IDLE_INTERVAL


def start(host, port, beamng_addr):
    global session, usd, redraw_areas
    log_event('Starting BNGLink')
    stop()
    redraw_areas = [area for area in bpy.context.screen.areas if area.type == 'VIEW_3D']
    session = bnglink.LinkSession(
        host, port, beamng_addr,
        {
            'init': on_init,
            'session_reset': on_session_reset,
            'vehicle_removed': on_vehicle_removed,
            'paint_update': on_paint_update,
            'texture_update': on_texture_update,
            'material_update': on_material_update,
            'sync': on_sync,
        },
        log_event,
        bnglink.RateStats(log_event) if DEBUG else None,
    )
    usd = usd_recorder.UsdRecorder(log_event)
    usd.attach(session)
    bpy.app.driver_namespace['bng_link'] = {'session': session, 'poll': poll, 'usd': usd}
    bpy.app.timers.register(poll)
    log_event(f'BNGLink ready, listening on {host}:{port}')
