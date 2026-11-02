import time
import traceback
from pathlib import Path

import bnglink


def _material_updates(state, switch_ids):
    updates = []
    for switch_id in switch_ids:
        material_id, emissive_scale = state.resolve(switch_id)
        update = {'id': switch_id, 'emissiveScale': emissive_scale}
        if material_id is not None:
            update['materialId'] = material_id
        updates.append(update)
    return updates


class UsdRecorder:
    # Records on the sender's clock: each sync's sim time is quantized to the take's capture time base,
    # so the take is independent of receive jitter and playback fps is chosen when the take is read.
    # Vehicles enter the take on their first sync, with the latest init, paints, switches and textures seen even while not recording.

    def __init__(self, log):
        self.log = log
        self.writer = None
        self.path = None
        self.t0 = None
        self.frame = 1
        self.inits = {}
        self.material_states = {}

    @property
    def active(self):
        return self.writer is not None

    def attach(self, session):
        # Runs after the session's own handlers for the same messages
        for name, hook in (('session_reset', self.on_session_reset),
                           ('vehicle_removed', self.on_vehicle_removed),
                           ('init', self.on_init), ('paint_update', self.on_paint_update),
                           ('texture_update', self.on_texture_update),
                           ('material_update', self.on_material_update), ('sync', self.on_sync)):
            self._chain(session, name, hook)

    def start(self, out_dir='.', capture_fps=1000.0, baked_mesh=False):
        # pxr is only needed while recording, so endpoints without it keep their live link
        from .writer import TakeWriter
        if self.active:
            self.log('USD recording already running')
            return None
        path = Path(out_dir)
        self.path = path if path.suffix.lower() == '.usdz' else path / time.strftime('bnglink_take_%Y%m%d_%H%M%S.usdz')
        self.writer = TakeWriter(capture_fps, baked_mesh)
        self.t0 = None
        self.log(f'USD recording, capture {capture_fps:g} fps to {self.path}')
        return self.path

    def stop(self):
        if not self.active:
            self.log('no USD recording running')
            return None
        writer, self.writer = self.writer, None
        writer.save(self.path)
        self.log(f'USD take stored: {self.path}')
        return self.path

    def on_init(self, init):
        vehicle_id = init['vehicle_id']
        self.inits[vehicle_id] = init
        self.material_states[vehicle_id] = bnglink.MaterialState()
        # A reinitialized vehicle starts a new segment on its next sync
        if self.active:
            self.writer.close_vehicle(vehicle_id, self.frame + 1)

    def on_session_reset(self, _):
        self.inits.clear()
        self.material_states.clear()
        if self.active:
            for vehicle_id in list(self.writer.current):
                self.writer.close_vehicle(vehicle_id, self.frame + 1)

    def on_vehicle_removed(self, data):
        vehicle_id = data['vehicle_id']
        self.inits.pop(vehicle_id, None)
        self.material_states.pop(vehicle_id, None)
        if self.active:
            self.writer.close_vehicle(vehicle_id, self.frame + 1)

    def on_sync(self, syncs):
        if not self.active:
            return
        sim_time = syncs[0]['sim_time']
        if self.t0 is None:
            self.t0 = sim_time
        # Two syncs in one capture slot keep the newest, matching the latest-wins stream.
        # The clamp covers the sender's clock restarting when its extension reloads.
        self.frame = max(1, round((sim_time - self.t0) * self.writer.capture_fps) + 1)
        for sync in syncs:
            vehicle_id = sync['vehicle_id']
            material_state = self.material_states[vehicle_id]
            changed = material_state.replace_damage(sync['damaged_switches'])
            if vehicle_id not in self.writer.current:
                segment = self.writer.add_vehicle(self.inits[vehicle_id], self.frame)
                changed = material_state.normal.keys() | material_state.damaged.keys()
            else:
                segment = self.writer.current[vehicle_id]
            segment.write_switches(_material_updates(material_state, changed), self.frame)
        self.writer.write_frame(syncs, self.frame)

    def on_paint_update(self, data):
        self.inits[data['vehicle_id']]['paints'] = data['paints']
        segment = self._segment(data['vehicle_id'])
        if segment is not None:
            segment.write_paints(data['paints'], self.frame)

    def on_texture_update(self, data):
        textures = self.inits[data['vehicle_id']]['textures']
        for update in data['textures']:
            textures[update['index']].update(name=update['name'], mime=update['mime'], data=update['data'])
        segment = self._segment(data['vehicle_id'])
        if segment is not None:
            segment.update_textures(data['textures'], self.frame)

    def on_material_update(self, data):
        switches = [switch for switch in data['switches'] if switch.get('damaged') is None]
        if not switches:
            return
        vehicle_id = data['vehicle_id']
        material_state = self.material_states[vehicle_id]
        updates = _material_updates(material_state, material_state.update(switches))
        segment = self._segment(vehicle_id)
        if segment is not None:
            segment.write_switches(updates, self.frame)

    def _segment(self, vehicle_id):
        return self.writer.current.get(vehicle_id) if self.active else None

    def _chain(self, session, name, hook):
        previous = session.handlers.get(name)

        def handler(link_session, data):
            if previous is not None:
                previous(link_session, data)
            try:
                hook(data)
            except Exception:
                # Escaping into the endpoint's poll loop would stop the live link
                self.writer = None
                self.log(f'USD recorder error in {hook.__name__}, recording stopped:')
                traceback.print_exc()

        session.handlers[name] = handler
