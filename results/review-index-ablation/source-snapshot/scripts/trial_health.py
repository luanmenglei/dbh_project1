"""Per-trial admission evidence for the shared Linux benchmark host."""
import math
from pathlib import Path
import shutil
import threading

from run_suite import ROOT, utc

POLICY = dict(version=1, max_load1=32, min_disk_free=40 * 2**30,
              sample_interval_s=1)


def sample_host(phase):
    return dict(time=utc(), phase=phase,
                load1=float(Path('/proc/loadavg').read_text().split()[0]),
                disk_free=shutil.disk_usage(ROOT).free)


def qualify(samples, correctness_only=False):
    """Recompute eligibility from saved observations; never infer missing evidence."""
    reasons = set()
    if correctness_only:
        reasons.add('correctness-only run')
    if len(samples) < 2 or samples[0].get('phase') != 'start' or samples[-1].get('phase') != 'finish':
        reasons.add('missing trial boundary samples')
    for sample in samples:
        try:
            load, free = float(sample['load1']), int(sample['disk_free'])
            if sample.get('error') or not math.isfinite(load) or load < 0 or free < 0:
                raise ValueError('invalid sample')
            if load > POLICY['max_load1']:
                reasons.add('host load exceeded 32')
            if free <= POLICY['min_disk_free']:
                reasons.add('free disk at or below 40 GiB')
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.add('host sampling failed')
    return dict(performance_eligible=not reasons, exclusion_reasons=sorted(reasons))


class TrialHealth:
    def __init__(self, correctness_only=False):
        self.correctness_only = correctness_only
        self.samples = []
        self.stop = threading.Event()

    def sample(self, phase):
        try:
            value = sample_host(phase)
        except Exception as error:
            value = dict(time=utc(), phase=phase, error=str(error))
        self.samples.append(value)

    def monitor(self):
        while not self.stop.wait(POLICY['sample_interval_s']):
            self.sample('during')

    def __enter__(self):
        self.sample('start')
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exception):
        self.stop.set()
        self.thread.join()
        self.sample('finish')

    def report(self):
        return dict(policy=POLICY, correctness_only=self.correctness_only,
                    samples=self.samples, **qualify(self.samples, self.correctness_only))
