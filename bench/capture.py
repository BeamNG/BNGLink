# Capture files, one folder per vehicle count (captures/nXX):
#   init_<vehicle id>.bin  raw init bulk payload
#   sync.bin               (timestamp, size) header + raw stream payload per frame
#   summary.json           transfer stats from sink.py, game.json game-side stats from run.lua

import csv
import json
import shutil
import struct
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CAPTURE_DIR = ROOT_DIR / 'bench' / 'captures'
WARMUP_S = 2.0
RECORD_S = 5.0
SYNC_HEADER = struct.Struct('<dI')


def capture_path(vehicle_count):
    return CAPTURE_DIR / f'n{int(vehicle_count):02d}'


def reset_capture(vehicle_count):
    path = capture_path(vehicle_count)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def existing_captures():
    if not CAPTURE_DIR.exists():
        return []
    return sorted(path for path in CAPTURE_DIR.iterdir() if path.is_dir() and path.name.startswith('n'))


def write_json(path, name, data):
    path.mkdir(parents=True, exist_ok=True)
    with (path / name).open('w', encoding='utf-8') as outfile:
        json.dump(data, outfile, indent=2, sort_keys=True)
        outfile.write('\n')


def read_json(path, name):
    with (path / name).open('r', encoding='utf-8') as infile:
        return json.load(infile)


def write_init(path, vehicle_id, payload):
    with (path / f'init_{int(vehicle_id)}.bin').open('wb') as outfile:
        outfile.write(payload)


def read_inits(path):
    files = sorted(path.glob('init_*.bin'), key=lambda item: int(item.stem.split('_', 1)[1]))
    for file_path in files:
        vehicle_id = int(file_path.stem.split('_', 1)[1])
        yield vehicle_id, file_path.read_bytes()


class SyncWriter:
    def __init__(self, path):
        self.file = (path / 'sync.bin').open('wb')

    def write(self, timestamp, payload):
        self.file.write(SYNC_HEADER.pack(float(timestamp), len(payload)))
        self.file.write(payload)

    def close(self):
        self.file.close()


def read_sync(path):
    with (path / 'sync.bin').open('rb') as infile:
        while True:
            header = infile.read(SYNC_HEADER.size)
            if not header:
                break
            timestamp, size = SYNC_HEADER.unpack(header)
            payload = infile.read(size)
            if len(payload) != size:
                raise ValueError(f'truncated sync payload in {path}')
            yield timestamp, payload


def format_row(row):
    return {key: f'{value:.3f}' if isinstance(value, float) else value for key, value in row.items()}


def write_rows(path, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(format_row(row) for row in rows)
