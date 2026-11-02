import os
import sys

import unreal

pkg_dir = os.path.dirname(__file__)
root_dir = os.path.dirname(pkg_dir)
for p in (root_dir, pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import bnglink
from bnglink import settings
import textures
import vehicle
import bngusd.recorder as usd_recorder

TEX_DIR = os.path.join(pkg_dir, 'bng_textures')

DEBUG = False

session = None
live_preview = True


def log_event(message):
    print(f'BNGLink Unreal: {message}')


def link():
    return getattr(unreal, '_bng_link', None)


def stop():
    previous = link()
    if previous is None:
        return
    del unreal._bng_link
    unreal.unregister_slate_post_tick_callback(previous['tick_handle'])
    previous['session'].close()
    if previous['usd'].active:
        previous['usd'].stop()
    log_event('BNGLink stopped')


def clear_textures_cache():
    keep = {
        texture
        for state in (session.vehicle_states.values() if session is not None else ())
        for texture in state['texture_assets']
    }
    textures.clear_cache(keep, TEX_DIR)
    log_event(f'Textures cache cleared: {TEX_DIR}')


def on_init(session, data):
    vehicle_id = data['vehicle_id']
    log_event(f'Loading vehicle {vehicle_id}')
    vehicle.build_meshes(data, session.vehicle_states, TEX_DIR)
    session.confirm_ready(vehicle_id)
    log_event(f'Vehicle {vehicle_id} ready')


def on_session_reset(session, _data):
    vehicle.reset_vehicles(session.vehicle_states)


def on_vehicle_removed(session, data):
    vehicle.remove_vehicle(session.vehicle_states, data['vehicle_id'])


def on_paint_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is not None:
        vehicle.update_paint(state, data['paints'])


def on_texture_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is not None:
        textures.update_textures(state['texture_assets'], data['textures'])


def on_material_update(session, data):
    state = session.vehicle_states.get(data['vehicle_id'])
    if state is not None:
        vehicle.update_materials(state, data['switches'])


def on_sync(session, syncs):
    current = link()
    if current and current['usd'].active and not live_preview:
        return
    for sync in syncs:
        state = session.vehicle_states.get(sync['vehicle_id'])
        if state is None or state['lifecycle'] != 'ready':
            continue
        vehicle.apply_damage(state, sync)
        vehicle.apply_body(state, sync, session.decoder)
        vehicle.apply_transforms(state, sync)


def tick(_delta_time):
    current = link()
    if current and current['session'] is session:
        session.poll()


def start(host=settings.HOST, port=settings.RECEIVER_PORT, beamng_addr=None):
    global session
    log_event('Starting BNGLink')
    stop()
    if beamng_addr is None:
        beamng_addr = settings.beamng_addr()
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
    unreal._bng_link = {
        'session': session,
        'tick_handle': unreal.register_slate_post_tick_callback(tick),
        'usd': usd,
    }
    log_event(f'BNGLink ready, listening on {host}:{port}')


if __name__ == '__main__':
    start()
