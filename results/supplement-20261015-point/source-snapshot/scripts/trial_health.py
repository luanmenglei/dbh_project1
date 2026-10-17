"""Per-trial admission evidence for the shared Linux benchmark host."""
import math
from pathlib import Path
import shutil
import threading

from run_suite import ROOT, utc

POLICY = dict(version=1, max_load1=32, min_disk_free=40 * 2**30,
              sample_interval_s=1)


def shared_policy(reason, cpus):
    if not reason.strip() or not cpus or len(set(cpus)) != len(cpus) or min(cpus) < 0:
        raise ValueError('Shared-host runs require a reason and distinct monitored CPU IDs')
    return dict(POLICY, version=2, max_load1=None, shared_host_reason=reason,
                monitored_cpus=sorted(cpus), min_available_memory=16 * 2**30)


def policy_for(args):
    return shared_policy(args.shared_host_reason, args.monitor_cpus) if args.shared_host_reason else POLICY


def add_policy_args(parser):
    parser.add_argument('--shared-host-reason', default='',
                        help='Explicit shared-host protocol: record load without the load<=32 gate')
    parser.add_argument('--monitor-cpus', type=int, nargs='+', default=[],
                        help='Experiment CPU IDs and their SMT siblings, recorded each sample')


def cpu_sample(cpus):
    counters = {}
    for line in Path('/proc/stat').read_text().splitlines():
        fields = line.split()
        if fields[0].startswith('cpu') and fields[0][3:].isdigit():
            number = int(fields[0][3:])
            if number in cpus:
                counters[str(number)] = list(map(int, fields[1:9]))
    if len(counters) != len(cpus):
        raise ValueError('Missing monitored CPU counters')
    memory = dict((f[0].rstrip(':'), int(f[1])) for f in
                  (line.split() for line in Path('/proc/meminfo').read_text().splitlines()))
    return dict(cpu_ticks=counters, available_memory=memory['MemAvailable'] * 1024)


def sample_host(phase):
    return dict(time=utc(), phase=phase,
                load1=float(Path('/proc/loadavg').read_text().split()[0]),
                disk_free=shutil.disk_usage(ROOT).free)


def qualify(samples, correctness_only=False, policy=None):
    """Recompute eligibility from saved observations; never infer missing evidence."""
    policy = POLICY if policy is None else policy
    if policy != POLICY:
        expected = shared_policy(policy.get('shared_host_reason', ''), policy.get('monitored_cpus', []))
        if policy != expected:
            raise ValueError('Unknown health policy')
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
            if policy['max_load1'] is not None and load > policy['max_load1']:
                reasons.add('host load exceeded 32')
            if free <= policy['min_disk_free']:
                reasons.add('free disk at or below 40 GiB')
            if policy['version'] == 2:
                if int(sample['available_memory']) < policy['min_available_memory']:
                    reasons.add('available memory below 16 GiB')
                counters = sample['cpu_ticks']
                if set(counters) != {str(c) for c in policy['monitored_cpus']}:
                    raise ValueError('Missing CPU counters')
                if any(len(v) != 8 or any(not isinstance(n, int) or n < 0 for n in v) for v in counters.values()):
                    raise ValueError('Invalid CPU counters')
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.add('host sampling failed')
    return dict(performance_eligible=not reasons, exclusion_reasons=sorted(reasons))


class TrialHealth:
    def __init__(self, correctness_only=False, policy=None):
        self.correctness_only = correctness_only
        self.policy = POLICY if policy is None else policy
        self.samples = []
        self.stop = threading.Event()

    def sample(self, phase):
        try:
            value = sample_host(phase)
            if self.policy['version'] == 2:
                value.update(cpu_sample(self.policy['monitored_cpus']))
        except Exception as error:
            value = dict(time=utc(), phase=phase, error=str(error))
        self.samples.append(value)

    def monitor(self):
        while not self.stop.wait(self.policy['sample_interval_s']):
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
        return dict(policy=self.policy, correctness_only=self.correctness_only,
                    samples=self.samples, **qualify(self.samples, self.correctness_only, self.policy))
