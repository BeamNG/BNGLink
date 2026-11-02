# Headless recorder: python bngusd/record.py --capture-fps 1000 --out takes/
# Recording starts with the first sync and is saved on Ctrl+C.

import argparse
import functools
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bnglink
from bnglink import settings
from bngusd.recorder import UsdRecorder

IDLE_INTERVAL = 0.001


def log(message):
    print(f'BNGLink USD recorder: {message}', flush=True)


def on_init(session, init):
    session.confirm_ready(init['vehicle_id'])
    log(f'vehicle {init["vehicle_id"]} ready')


def on_sync(recorder, out_dir, capture_fps, baked_mesh, session, syncs):
    if not recorder.active:
        recorder.start(out_dir, capture_fps, baked_mesh)


def main():
    parser = argparse.ArgumentParser(description='Record a BNGLink stream to USDZ.')
    parser.add_argument('--out', default='.', help='Output directory or .usdz file')
    parser.add_argument('--capture-fps', type=float, default=1000.0,
                        help='Time base the take is sampled on; playback fps is chosen at import')
    parser.add_argument('--baked-mesh', action='store_true', help='Store baked mesh vertices per frame')
    args = parser.parse_args()
    recorder = UsdRecorder(log)
    sync_handler = functools.partial(
        on_sync, recorder, args.out, args.capture_fps, args.baked_mesh)

    session = bnglink.LinkSession(
        settings.HOST, settings.RECEIVER_PORT, settings.beamng_addr(),
        {'init': on_init, 'sync': sync_handler}, log)
    recorder.attach(session)
    log(f'listening on {settings.HOST}:{settings.RECEIVER_PORT}')
    try:
        while True:
            session.poll()
            time.sleep(IDLE_INTERVAL)
    except KeyboardInterrupt:
        log('stopping')
    finally:
        if recorder.active:
            recorder.stop()
        session.close()


if __name__ == '__main__':
    main()
