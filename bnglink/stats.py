# Opt-in debug timing: per-second throughput reporting and bench lap timers.

import time


def lap(stats, key, start):
    # stats is None outside benches; returns the start of the next lap
    now = time.perf_counter()
    if stats is not None:
        stats[key] += now - start
    return now


class RateStats:
    def __init__(self, log_fn):
        self.log_fn = log_fn
        self.received = 0
        self.applied = 0
        self.apply_time = 0.0
        self.last = time.monotonic()

    def note_applied(self, seconds):
        self.applied += 1
        self.apply_time += seconds

    def tick(self):
        now = time.monotonic()
        if now - self.last < 1.0:
            return
        avg = self.apply_time / self.applied * 1000.0 if self.applied else 0.0
        self.log_fn(
            f'Live sync received/s: {self.received}, updated/s: {self.applied}, '
            f'avg update ms: {avg:.2f}'
        )
        self.received = 0
        self.applied = 0
        self.apply_time = 0.0
        self.last = now
