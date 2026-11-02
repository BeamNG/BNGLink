import sys
import time
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT_DIR), str(Path(__file__).resolve().parent)]

import bnglink
import capture
from bnglink.transport import KIND_BULK


def median_ms(function, setup=None, runs=9):
    times = []
    for _ in range(runs):
        if setup:
            setup()
        start = time.perf_counter()
        function()
        times.append((time.perf_counter() - start) * 1000.0)
    return np.median(times)


def sample_beams(locator_nodes, count):
    beams = []
    seen = set()
    for row in locator_nodes:
        nodes = np.unique(row[row >= 0])
        for first_index, first in enumerate(nodes):
            for second in nodes[first_index + 1:]:
                beam = (int(first), int(second))
                if beam not in seen:
                    seen.add(beam)
                    beams.append(beam)
                    if len(beams) == count:
                        return beams
    return beams


def main():
    _, payload = next(capture.read_inits(capture.capture_path(1)))
    _, init = bnglink.BngDecoder().decode(KIND_BULK, payload)
    locator_nodes = bnglink.face_locator_nodes(init['parts'])

    face_index = None

    def build_index():
        nonlocal face_index
        face_index = bnglink.build_face_node_index(locator_nodes)

    build_ms = median_ms(build_index)
    beams = sample_beams(locator_nodes, 1000)
    print(
        f'faces={len(locator_nodes)} build_ms={build_ms:.3f} '
        f'index_bytes={sum(array.nbytes for array in face_index)}'
    )
    for count in (1, 10, 100, 1000):
        broken = set(beams[:count])
        hidden = np.zeros(len(locator_nodes), dtype=bool)

        def apply_damage():
            bnglink.update_hidden_faces(face_index, set(), broken, hidden)

        print(f'beams={count} lookup_ms={median_ms(apply_damage, lambda: hidden.fill(False)):.3f}')


if __name__ == '__main__':
    main()
