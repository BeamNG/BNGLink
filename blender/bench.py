"""Replays bench/captures through the Blender endpoint code without BeamNG.
Run from Blender's Text Editor with this file open; results go to captures/blender_bench_split.csv.

Frame split (ms per frame, frame = tick to tick):
  apply      sync applied from Python: rigid + skin + mesh + body_xform + props_xform
  eval       depsgraph evaluation, forced right after apply
  pre_draw   eval end -> first 3D view PRE_VIEW callback (draw cache rebuild, other regions)
  draw       PRE_VIEW -> last 3D view POST_PIXEL (engine draw submission)
  post_draw  last POST_PIXEL -> next tick (buffer swap, GPU wait, event loop)
Counts are per frame; undrawn counts frames that saw no 3D view draw (their time goes to post_draw).
"""

import os
import sys
import time

import bpy
import numpy as np

pkg_dir = os.path.dirname(bpy.context.space_data.text.filepath)
root_dir = os.path.dirname(pkg_dir)
bench_dir = os.path.join(root_dir, 'bench')
for path in (root_dir, pkg_dir, bench_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

import capture
import bnglink
import vehicle
from bnglink.transport import KIND_BULK, KIND_STREAM

TEX_DIR = os.path.join(pkg_dir, 'bng_textures')
SPLIT_CSV = capture.CAPTURE_DIR / 'blender_bench_split.csv'
TIMER_KEYS = ('rigid', 'skin', 'mesh', 'body_xform', 'props_xform', 'apply',
              'eval', 'pre_draw', 'draw', 'post_draw', 'frame')
COUNT_KEYS = ('bodies', 'moved', 'reskinned', 'undrawn')


def log_event(message):
    print(f'BNGLink Blender bench: {message}')


def redraw():
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def collect_objects(obj, found):
    for child in list(obj.children):
        collect_objects(child, found)
    found.append(obj)


class BlenderReplayBench:
    def __init__(self):
        self.captures = capture.existing_captures()
        self.capture_index = 0
        self.decoder = bnglink.BngDecoder()
        self.vehicle_states = {}
        self.syncs = []
        self.frame_index = 0
        self.samples = 0
        self.stats = None
        self.rows = []
        self.frame_open = False
        self.frame_start = 0.0
        self.eval_end = 0.0
        self.pre_view = None
        self.post_pixel = None
        self.draw_handles = [
            bpy.types.SpaceView3D.draw_handler_add(self.on_pre_view, (), 'WINDOW', 'PRE_VIEW'),
            bpy.types.SpaceView3D.draw_handler_add(self.on_post_pixel, (), 'WINDOW', 'POST_PIXEL'),
        ]

    def on_pre_view(self):
        if self.frame_open and self.pre_view is None:
            self.pre_view = time.perf_counter()

    def on_post_pixel(self):
        if self.frame_open:
            self.post_pixel = time.perf_counter()

    def remove_draw_handlers(self):
        for handle in self.draw_handles:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        self.draw_handles = []

    def close_frame(self, now):
        stats = self.stats
        stats['frame'] += now - self.frame_start
        if self.pre_view is not None and self.post_pixel is not None:
            stats['pre_draw'] += self.pre_view - self.eval_end
            stats['draw'] += self.post_pixel - self.pre_view
            stats['post_draw'] += now - self.post_pixel
        else:
            stats['undrawn'] += 1
            stats['post_draw'] += now - self.eval_end
        self.samples += 1
        self.frame_open = False

    def cleanup(self):
        objects = []
        for state in self.vehicle_states.values():
            # the positions view points into mesh memory that batch_remove frees
            if state['body'] is not None:
                state['body']['positions'] = None
            try:
                collect_objects(state['root'], objects)
            except ReferenceError:
                pass
        self.vehicle_states = {}
        vehicle.world_origin = None
        meshes = [obj.data for obj in objects if obj.data is not None]
        if objects:
            bpy.data.batch_remove(objects)
        orphan_meshes = [mesh_data for mesh_data in meshes if mesh_data.users == 0]
        if orphan_meshes:
            bpy.data.batch_remove(orphan_meshes)
        return None

    def start_capture(self, path):
        self.cleanup()
        for _, payload in capture.read_inits(path):
            name, data = self.decoder.decode(KIND_BULK, payload)
            if name != 'init':
                continue
            vehicle.build_meshes(data, self.vehicle_states, TEX_DIR)
            state = self.vehicle_states[data['vehicle_id']]
            zero_sync = {'node_raw': np.zeros((len(data['node_ids']), 3), np.float32)}
            vehicle.apply_body(state, zero_sync, self.decoder)

        self.syncs = [self.decoder.decode(KIND_STREAM, payload)[1] for _, payload in capture.read_sync(path)]
        self.frame_index = 0
        self.samples = 0
        self.stats = dict.fromkeys(TIMER_KEYS + COUNT_KEYS, 0.0)
        log_event(f'replaying {path.name} (rigid tolerance {vehicle.RIGID_TOLERANCE * 1000.0:g} mm)')

    def finish_capture(self, path):
        frames = self.samples
        stats = self.stats
        per_frame = 1.0 / frames if frames else 0.0
        row = {
            'vehicles': len(self.vehicle_states),
            'frames': frames,
            'tolerance_mm': vehicle.RIGID_TOLERANCE * 1000.0,
            'view_fps': frames / stats['frame'] if stats['frame'] > 0 else 0.0,
        }
        for key in TIMER_KEYS:
            row[f'{key}_ms'] = stats[key] * per_frame * 1000.0
        for key in COUNT_KEYS:
            row[key] = stats[key] * per_frame
        row['kept'] = row['bodies'] - row['moved'] - row['reskinned']
        self.rows.append(row)
        log_event(f'{path.name}: ' + ' '.join(f'{key}={value}' for key, value in capture.format_row(row).items()))

    def finish(self):
        if self.rows:
            capture.write_rows(SPLIT_CSV, self.rows)
            log_event(f'wrote {SPLIT_CSV}')
        self.remove_draw_handlers()
        log_event('complete')
        bpy.app.driver_namespace['bng_link_bench'] = None
        bpy.app.timers.register(self.cleanup, first_interval=0.5)
        return None

    def tick(self):
        now = time.perf_counter()
        if self.frame_open:
            self.close_frame(now)

        if self.capture_index >= len(self.captures):
            return self.finish()

        path = self.captures[self.capture_index]
        if not self.syncs:
            self.start_capture(path)
            if not self.syncs:
                self.finish_capture(path)
                self.capture_index += 1
                return 0.0
            now = time.perf_counter()

        if self.frame_index >= len(self.syncs):
            self.finish_capture(path)
            self.capture_index += 1
            self.syncs = []
            return 0.0

        stats = self.stats
        self.frame_start = now
        for sync in self.syncs[self.frame_index]:
            state = self.vehicle_states[sync['vehicle_id']]
            vehicle.apply_damage(state, sync)
            vehicle.apply_body(state, sync, self.decoder, stats)
            t0 = time.perf_counter()
            vehicle.apply_transforms(state, sync)
            stats['props_xform'] += time.perf_counter() - t0
        t0 = time.perf_counter()
        stats['apply'] += t0 - now
        bpy.context.evaluated_depsgraph_get()
        self.eval_end = time.perf_counter()
        stats['eval'] += self.eval_end - t0
        self.frame_index += 1
        self.pre_view = None
        self.post_pixel = None
        self.frame_open = True
        redraw()
        return 0.0


previous = bpy.app.driver_namespace.get('bng_link_bench')
if previous is not None:
    for callback in (previous.tick, previous.cleanup):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    previous.remove_draw_handlers()
    previous.cleanup()

bench = BlenderReplayBench()
bpy.app.driver_namespace['bng_link_bench'] = bench
bpy.app.timers.register(bench.tick)
log_event('started')
