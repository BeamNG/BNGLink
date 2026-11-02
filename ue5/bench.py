"""Replays bench/captures through the Unreal endpoint code without BeamNG.
Run from Unreal's Python editor tab, results go to captures/ue5_bench.csv.

Frame split (ms per frame):
  apply            sync applied from Python: vehicle_frame + upload + props_xform
  game/render/gpu  engine thread and GPU frame times (stat unit), one frame behind
Uploads counts the vehicles per frame whose nodes were sent to the GPU.
Each capture replays once un-timed first so first-draw shader and texture costs stay out of the measured pass.
"""

import os
import sys
import time
import traceback

import unreal

pkg_dir = os.path.dirname(__file__)
root_dir = os.path.dirname(pkg_dir)
bench_dir = os.path.join(root_dir, 'bench')
for path in (root_dir, pkg_dir, bench_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

import capture
import bnglink
import mesh
import vehicle
from bnglink.transport import KIND_BULK, KIND_STREAM

TEX_DIR = os.path.join(pkg_dir, 'bng_textures')
SPLIT_CSV = capture.CAPTURE_DIR / 'ue5_bench.csv'
TIMER_KEYS = ('vehicle_frame', 'upload', 'props_xform', 'apply')
THREAD_KEYS = ('game', 'render', 'gpu')
COUNT_KEYS = ('uploads',)
STAT_KEYS = TIMER_KEYS + THREAD_KEYS + COUNT_KEYS


def log_event(message):
    print(f'BNGLink Unreal bench: {message}')


class UnrealReplayBench:
    def __init__(self):
        self.captures = capture.existing_captures()
        self.capture_index = 0
        self.decoder = bnglink.BngDecoder()
        self.vehicle_states = {}
        self.syncs = []
        self.frame_index = 0
        self.warming = False
        self.tick_s = 0.0
        self.samples = 0
        self.stats = dict.fromkeys(STAT_KEYS, 0.0)
        self.rows = []
        self.tick_handle = None
        self.busy = False

    def cleanup(self):
        subsystem = mesh.actor_subsystem()
        for state in self.vehicle_states.values():
            if state['prop_handle'] is not None:
                unreal.BngLinkBridge.unregister_prop_group(state['prop_handle'])
            mesh.clear_vehicle_meshes(state)
            subsystem.destroy_actor(state['root'])
        self.vehicle_states = {}
        vehicle.world_origin = None

    def start_capture(self, path):
        self.cleanup()
        log_event(f'loading {path.name}')
        for _, payload in capture.read_inits(path):
            name, data = self.decoder.decode(KIND_BULK, payload)
            if name != 'init':
                continue
            log_event(f'building vehicle {data["vehicle_id"]}')
            vehicle.build_meshes(data, self.vehicle_states, TEX_DIR, refresh_textures=False)

        self.syncs = [self.decoder.decode(KIND_STREAM, payload)[1] for _, payload in capture.read_sync(path)]
        self.frame_index = 0
        self.warming = bool(self.syncs)
        self.tick_s = 0.0
        self.samples = 0
        self.stats = dict.fromkeys(STAT_KEYS, 0.0)
        log_event(f'replaying {path.name}')

    def apply_syncs(self, syncs, stats=None):
        start = time.perf_counter()
        for sync in syncs:
            state = self.vehicle_states[sync['vehicle_id']]
            vehicle.apply_damage(state, sync)
            vehicle.apply_body(state, sync, self.decoder, stats)
            lap = time.perf_counter()
            vehicle.apply_transforms(state, sync)
            if stats is not None:
                stats['props_xform'] += time.perf_counter() - lap
        if stats is not None:
            stats['apply'] += time.perf_counter() - start

    def finish_capture(self, path):
        stats = self.stats
        per_frame = 1.0 / self.samples if self.samples else 0.0
        row = {
            'vehicles': len(self.vehicle_states),
            'frames': self.samples,
            'view_fps': self.samples / self.tick_s if self.tick_s > 0 else 0.0,
        }
        for key in TIMER_KEYS:
            row[f'{key}_ms'] = stats[key] * per_frame * 1000.0
        for key in THREAD_KEYS:
            row[f'{key}_ms'] = stats[key] * per_frame
        for key in COUNT_KEYS:
            row[key] = stats[key] * per_frame
        self.rows.append(row)
        log_event(f'{path.name}: ' + ' '.join(f'{key}={value}' for key, value in capture.format_row(row).items()))

    def stop(self):
        if self.tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self.tick_handle)
            self.tick_handle = None
        self.cleanup()
        unreal._bng_link_bench = None

    def tick(self, delta_time):
        if self.busy:
            return
        if self.capture_index >= len(self.captures):
            if self.rows:
                capture.write_rows(SPLIT_CSV, self.rows)
                log_event(f'wrote {SPLIT_CSV}')
            log_event('complete')
            self.stop()
            return

        path = self.captures[self.capture_index]
        if not self.syncs:
            self.busy = True
            try:
                self.start_capture(path)
            except Exception:
                traceback.print_exc()
                self.busy = False
                self.stop()
                return
            self.busy = False
            if not self.syncs:
                self.finish_capture(path)
                self.capture_index += 1
            return

        if self.frame_index >= len(self.syncs):
            if self.warming:
                self.frame_index = 0
                self.warming = False
                return
            self.finish_capture(path)
            self.capture_index += 1
            self.syncs = []
            return

        syncs = self.syncs[self.frame_index]
        self.frame_index += 1
        if self.warming:
            self.apply_syncs(syncs)
            return
        self.apply_syncs(syncs, self.stats)
        threads = unreal.BngLinkBridge.get_thread_times()
        self.stats['game'] += threads.x
        self.stats['render'] += threads.y
        self.stats['gpu'] += threads.z
        self.samples += 1
        self.tick_s += float(delta_time)


previous = getattr(unreal, '_bng_link_bench', None)
if previous is not None:
    previous.stop()

bench = UnrealReplayBench()
unreal._bng_link_bench = bench
if bench.captures:
    bench.busy = True
    try:
        bench.start_capture(bench.captures[0])
    except Exception:
        traceback.print_exc()
        bench.cleanup()
        unreal._bng_link_bench = None
        raise
    finally:
        bench.busy = False
bench.tick_handle = unreal.register_slate_post_tick_callback(bench.tick)
log_event('started')
