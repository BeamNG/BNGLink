# Endpoint that records captures instead of rendering: start `python bench/sink.py`, then run.lua in BeamNG.
# Each realtime run writes the init payloads and the synced frames after WARMUP_S, for RECORD_S.

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
for path in (ROOT_DIR, Path(__file__).resolve().parent):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import capture
import bnglink
from bnglink import settings
from bnglink.transport import KIND_BULK, KIND_CONTROL, KIND_STREAM

IDLE_INTERVAL = 0.001
STATUS_INTERVAL = 5.0


class BenchSink:
    def __init__(self):
        self.session = bnglink.LinkSession(
            settings.HOST,
            settings.RECEIVER_PORT,
            settings.beamng_addr(),
            {'init': self.on_init},
            self.log,
            on_payload=self.on_payload,
        )
        self.sync_writer = None
        self.reset_run()

    def close(self):
        if self.sync_writer is not None:
            self.sync_writer.close()
        self.session.close()

    def log(self, message):
        print(f'BNGLink bench sink: {message}', flush=True)

    def reset_run(self):
        if self.sync_writer is not None:
            self.sync_writer.close()
        self.init_started_at = None
        self.init_completed_at = None
        self.realtime_started_at = None
        self.init_bytes = 0
        self.stream_bytes = 0
        self.stream_frames = 0
        self.init_payloads = {}
        self.latest_bulk_payload = None
        self.capture_dir = None
        self.sync_writer = None

    def on_payload(self, kind, payload):
        if kind == KIND_CONTROL:
            self.on_control(bytes(payload).decode('utf-8'))
            return
        if kind == KIND_BULK:
            if self.init_started_at is not None and self.realtime_started_at is None:
                self.init_bytes += len(payload)
            self.latest_bulk_payload = bytes(payload)
            return
        if kind == KIND_STREAM and self.sync_writer is not None:
            elapsed = time.perf_counter() - self.realtime_started_at
            if capture.WARMUP_S <= elapsed < capture.WARMUP_S + capture.RECORD_S:
                recorded_at = elapsed - capture.WARMUP_S
                self.sync_writer.write(recorded_at, payload)
                self.stream_frames += 1
                self.stream_bytes += len(payload)

    def on_control(self, message):
        if message == 'session_reset':
            self.reset_run()
            self.init_started_at = time.perf_counter()
            self.log('init started')
        elif message == 'init_completed':
            if self.init_started_at is not None:
                self.init_completed_at = time.perf_counter()
            self.log('init completed')
        elif message == 'realtime_started':
            self.start_realtime()
        elif message == 'realtime_stopped':
            self.stop_realtime()

    def start_realtime(self):
        vehicle_count = len(self.init_payloads)
        if vehicle_count == 0:
            self.log('realtime ignored: no init payloads recorded')
            return
        self.capture_dir = capture.reset_capture(vehicle_count)
        for vehicle_id, payload in sorted(self.init_payloads.items()):
            capture.write_init(self.capture_dir, vehicle_id, payload)
        self.sync_writer = capture.SyncWriter(self.capture_dir)
        self.realtime_started_at = time.perf_counter()
        self.stream_bytes = 0
        self.stream_frames = 0
        self.log(f'recording n{vehicle_count:02d}')

    def stop_realtime(self):
        if self.sync_writer is None:
            return
        elapsed = time.perf_counter() - self.realtime_started_at
        self.sync_writer.close()
        self.sync_writer = None
        recorded_seconds = min(capture.RECORD_S, max(0.0, elapsed - capture.WARMUP_S))
        bytes_per_frame = self.stream_bytes / self.stream_frames if self.stream_frames else 0.0
        summary = {
            'vehicles': len(self.init_payloads),
            'warmup_s': capture.WARMUP_S,
            'record_s': capture.RECORD_S,
            'init_s': self.init_seconds(),
            'init_bytes': self.init_bytes,
            'stream_frames': self.stream_frames,
            'stream_fps': self.stream_frames / recorded_seconds if recorded_seconds > 0 else 0.0,
            'stream_bytes': self.stream_bytes,
            'mb_s': self.stream_bytes / recorded_seconds / 1000000.0 if recorded_seconds > 0 else 0.0,
            'bytes_frame': bytes_per_frame,
        }
        capture.write_json(self.capture_dir, 'summary.json', summary)
        self.log(f'wrote {self.capture_dir}')

    def init_seconds(self):
        if self.init_started_at is None or self.init_completed_at is None:
            return None
        return self.init_completed_at - self.init_started_at

    def on_init(self, session, data):
        vehicle_id = data['vehicle_id']
        if self.latest_bulk_payload is not None:
            self.init_payloads[vehicle_id] = self.latest_bulk_payload
        session.confirm_ready(vehicle_id)
        self.log(f'vehicle {vehicle_id} ready')

    def log_wait_status(self):
        if self.sync_writer is not None or self.init_started_at is None:
            return
        if self.init_completed_at is None:
            self.log(f'still receiving init, {len(self.init_payloads)} vehicles ready')
            return
        self.log('init done, waiting for realtime')

    def run(self):
        self.log(f'listening on {settings.HOST}:{settings.RECEIVER_PORT}')
        last_status = time.monotonic()
        try:
            while True:
                self.session.poll()
                now = time.monotonic()
                if now - last_status >= STATUS_INTERVAL:
                    self.log_wait_status()
                    last_status = now
                time.sleep(IDLE_INTERVAL)
        except KeyboardInterrupt:
            self.log('stopping')


def main():
    sink = BenchSink()
    try:
        sink.run()
    finally:
        sink.close()


if __name__ == '__main__':
    main()
