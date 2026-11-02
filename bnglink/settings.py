import os
from pathlib import Path

HOST = os.environ.get('BNGLINK_HOST', '127.0.0.1')


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer, got {raw!r}') from exc
    if not 0 < value <= 65535:
        raise ValueError(f'{name} must be between 1 and 65535, got {value}')
    return value


BEAMNG_PORT = _env_int('BNGLINK_BEAMNG_PORT', 6000)
RECEIVER_PORT = _env_int('BNGLINK_RECEIVER_PORT', 6001)
TAKES_DIR = os.environ.get('BNGLINK_TAKES_DIR') or str(Path.home() / 'BNGLink' / 'takes')


def beamng_addr():
    return HOST, BEAMNG_PORT


def default_takes_dir():
    return TAKES_DIR
