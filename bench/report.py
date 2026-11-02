# Summary table of every capture (summary.json + game.json): python bench/report.py

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
for path in (ROOT_DIR, Path(__file__).resolve().parent):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import capture


def read_optional(path, name):
    file_path = path / name
    if not file_path.exists():
        return {}
    return capture.read_json(path, name)


def fmt(value, digits=2):
    if value is None:
        return 'n/a'
    return f'{float(value):.{digits}f}'


def print_row(path):
    summary = read_optional(path, 'summary.json')
    game = read_optional(path, 'game.json')
    vehicles = summary.get('vehicles') or game.get('vehicles') or int(path.name[1:])
    init_mb = summary.get('init_bytes', 0) / 1000000.0
    print(
        f'{vehicles:2d} | '
        f'{fmt(summary.get("init_s")):>8} | '
        f'{fmt(init_mb):>8} | '
        f'{fmt(game.get("game_fps")):>8} | '
        f'{fmt(summary.get("stream_fps")):>10} | '
        f'{fmt(summary.get("mb_s")):>8} | '
        f'{fmt(summary.get("bytes_frame"), 0):>11} | '
        f'{fmt(game.get("send_us_frame")):>8}'
    )


def main():
    captures = capture.existing_captures()
    if not captures:
        print(f'No captures found in {capture.CAPTURE_DIR}')
        return
    print(' n | init s  | init MB | game FPS | stream FPS | MB/s    | bytes/frame | send us')
    print('---+---------+---------+----------+------------+---------+-------------+--------')
    for path in captures:
        print_row(path)


if __name__ == '__main__':
    main()
