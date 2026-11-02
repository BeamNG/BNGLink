# USD take record, save and read cost on a capture: python bench/usd_bench.py [capture name]

import cProfile
import pstats
import struct
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parent.parent
for path in (ROOT_DIR, Path(__file__).resolve().parent):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import capture
import bnglink
from bnglink.transport import KIND_BULK, KIND_STREAM
from bngusd.recorder import UsdRecorder
from bngusd.reader import Playhead, Take


def load(path):
    decoder = bnglink.BngDecoder()
    inits = [decoder.decode(KIND_BULK, payload)[1] for _, payload in capture.read_inits(path)]
    syncs = []
    t0 = None
    for timestamp, payload in capture.read_sync(path):
        t0 = timestamp if t0 is None else t0
        # The captures predate the sync sim time field: stamp them with the capture clock
        payload = payload[:8] + struct.pack('<d', timestamp - t0) + payload[8:]
        syncs.append(decoder.decode(KIND_STREAM, payload)[1])
    return inits, syncs


def bench(path, baked):
    inits, syncs = load(path)
    if baked:
        syncs = syncs[:60]
    session = SimpleNamespace(handlers={})
    recorder = UsdRecorder(lambda message: None)
    recorder.attach(session)
    for init in inits:
        session.handlers['init'](session, init)
    with tempfile.TemporaryDirectory() as temp_dir:
        recorder.start(temp_dir, 1000.0, baked)
        t0 = time.perf_counter()
        for frame_syncs in syncs:
            session.handlers['sync'](session, frame_syncs)
        record_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        take_path = recorder.stop()
        save_s = time.perf_counter() - t0
        size_mb = Path(take_path).stat().st_size / 1e6

        read_s = frame_s = 0.0
        frames = 0
        if not baked:
            t0 = time.perf_counter()
            take = Take(take_path, fps=60)
            read_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            playheads = [Playhead(segment) for segment in take.segments]
            for frame in range(take.frame_range[0], take.frame_range[1] + 1):
                sample = take.to_capture_frame(frame)
                for playhead in playheads:
                    playhead.step(sample)
            frames = take.frame_range[1] - take.frame_range[0] + 1
            frame_s = time.perf_counter() - t0
            del take, playheads

    print(
        f'{path.name}{" baked" if baked else ""}: syncs={len(syncs)} '
        f'record_ms/sync={record_s / len(syncs) * 1000.0:.3f} save_s={save_s:.3f} size_mb={size_mb:.2f} '
        f'open_s={read_s:.3f} read_ms/frame={frame_s / max(frames, 1) * 1000.0:.3f}'
    )


def main():
    args = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
    paths = [capture.CAPTURE_DIR / name for name in args] or capture.existing_captures()[:1]
    profile = cProfile.Profile() if '--profile' in sys.argv else None
    if profile:
        profile.enable()
    for path in paths:
        bench(path, '--baked' in sys.argv)
    if profile:
        profile.disable()
        pstats.Stats(profile).sort_stats('tottime').print_stats(18)


if __name__ == '__main__':
    main()
