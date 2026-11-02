# Decode and skinning cost per capture, without any endpoint: python bench/replay.py

import sys
import time
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
for path in (ROOT_DIR, Path(__file__).resolve().parent):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import capture
import bnglink
from bnglink.transport import KIND_BULK, KIND_STREAM


def build_states(decoder, path):
    states = {}
    for vehicle_id, payload in capture.read_inits(path):
        name, data = decoder.decode(KIND_BULK, payload)
        if name != 'init':
            continue
        merged, _, total = bnglink.merge_part_skins(data['parts'])
        states[vehicle_id] = {
            'skin': merged,
            'out': None if merged is None else np.empty((total, 3), dtype=np.float32),
            'verts': total,
        }
    return states


def warmup(decoder, states, payloads):
    if not payloads:
        return
    _, syncs = decoder.decode(KIND_STREAM, payloads[0])
    for sync in syncs:
        state = states.get(sync['vehicle_id'])
        if state and state['skin'] is not None:
            decoder.deform_body(sync['node_raw'], state['skin'], state['out'])


def bench_capture(decoder, path):
    states = build_states(decoder, path)
    payloads = [payload for _, payload in capture.read_sync(path)]
    warmup(decoder, states, payloads)

    decode_s = 0.0
    skin_s = 0.0
    frames = 0
    for payload in payloads:
        t0 = time.perf_counter()
        _, syncs = decoder.decode(KIND_STREAM, payload)
        decode_s += time.perf_counter() - t0

        t0 = time.perf_counter()
        for sync in syncs:
            state = states.get(sync['vehicle_id'])
            if state and state['skin'] is not None:
                decoder.deform_body(sync['node_raw'], state['skin'], state['out'])
        skin_s += time.perf_counter() - t0
        frames += 1

    verts = sum(state['verts'] for state in states.values())
    total_s = decode_s + skin_s
    return {
        'name': path.name,
        'vehicles': len(states),
        'frames': frames,
        'verts': verts,
        'decode_ms': decode_s / frames * 1000.0 if frames else 0.0,
        'skin_ms': skin_s / frames * 1000.0 if frames else 0.0,
        'fps': frames / total_s if total_s > 0 else 0.0,
    }


def print_result(result):
    print(
        f'{result["name"]}: '
        f'vehicles={result["vehicles"]} '
        f'frames={result["frames"]} '
        f'verts={result["verts"]} '
        f'decode_ms={result["decode_ms"]:.3f} '
        f'skin_ms={result["skin_ms"]:.3f} '
        f'fps={result["fps"]:.1f}'
    )


def main():
    captures = capture.existing_captures()
    if not captures:
        print(f'No captures found in {capture.CAPTURE_DIR}')
        return
    decoder = bnglink.BngDecoder()
    for path in captures:
        print_result(bench_capture(decoder, path))


if __name__ == '__main__':
    main()
