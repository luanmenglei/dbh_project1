#!/usr/bin/env python3
"""Supplementary external-stream point queries, varied joins and transaction demonstrations."""
import argparse
import json
from pathlib import Path
import shutil
from evidence import require
from prepare import checksum
from prepare_requests import generate
from run_suite import ROOT, Runner, utc
from trial_health import TrialHealth, POLICY


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--data', type=Path, default=ROOT/'data/generated')
    p.add_argument('--scale', type=int, default=10)
    p.add_argument('--requests', type=int, default=10000)
    p.add_argument('--repeats', type=int, default=5)
    p.add_argument('--timeout', type=int, default=1800)
    p.add_argument('--correctness-only', action='store_true', help='No performance claims; tolerates busy host')
    management = p.add_mutually_exclusive_group(required=True)
    management.add_argument('--manage-user-services', action='store_true')
    management.add_argument('--manage-containers', action='store_true')
    a = p.parse_args()
    if min(a.scale, a.requests, a.repeats) < 1:
        p.error('scale, requests and repeats must be positive')
    runner = Runner(a)
    results = []
    functionality = {}
    eligibility = []
    try:
        health = {'time': utc(), 'load1': float(Path('/proc/loadavg').read_text().split()[0]),
                  'disk_free': shutil.disk_usage(ROOT).free}
        (runner.out/'health.json').write_text(json.dumps(health, indent=2))
        require(health['disk_free'] > 40*2**30, 'Insufficient free disk space')
        require(a.correctness_only or health['load1'] <= 32, 'Busy host; defer performance experiment')
        data = a.data/f's{a.scale}'
        manifest = json.loads((data/'manifest.json').read_text())
        for table, info in manifest['files'].items():
            require(checksum(data/f'{table}.tsv') == info['sha256'], 'Input changed: '+table)
        (runner.out/'data-manifest.json').write_text(json.dumps(manifest, indent=2))
        workload = generate(data, runner.out/'requests', a.requests, a.repeats)
        runner.activate('file')
        reference = {t: runner.call('file', a.scale, 'fingerprint', ['--table', t]) for t in manifest['counts']}
        runner.call('file', a.scale, 'build_index')
        expected = {}
        for repeat in range(a.repeats):
            backends = ['file', 'postgres', 'opengauss'] if repeat % 2 == 0 else ['file', 'opengauss', 'postgres']
            for backend in backends:
                runner.activate(backend)
                if backend != 'file':
                    runner.load(backend, a.scale)
                    for table, original in reference.items():
                        runner.equal(original, runner.call(backend, a.scale, 'fingerprint', ['--table', table]), table)
                    snapshot = runner.call(backend, a.scale, 'snapshot')
                    settings = {r[0]: r[1] for r in snapshot['settings']}
                    require(settings.get('fsync') == 'on' and settings.get('synchronous_commit') == 'on', 'Durability settings changed')
                    functionality[backend] = runner.call(backend, a.scale, 'functional')
                    for table, original in reference.items():
                        runner.equal(original, runner.call(backend, a.scale, 'fingerprint', ['--table', table]), 'post-rollback '+table)
                cases = [('join', item['label'], ['--id', str(item['id'])], item['expected_rows']) for item in workload['joins']]
                for stream in workload['streams']:
                    if stream['repeat'] == repeat:
                        cases.append(('point', stream['distribution'], ['--requests', str(a.requests), '--request-file',
                                      str(runner.out/'requests'/stream['file']), '--indexed', '1' if backend == 'file' else '0'], a.requests))
                for op, label, extra, rows in cases:
                    runner.call(backend, a.scale, op, extra, phase='warmup', case=label)
                    health_check = TrialHealth(a.correctness_only)
                    value = runner.call(backend, a.scale, op, extra, measured=not a.correctness_only,
                                        health_check=health_check, phase='coverage', repeat=repeat, case=label)
                    eligibility.append(health_check.report()['performance_eligible'])
                    require(value['rows'] == rows, 'Unexpected row count: '+label)
                    key = (repeat, op, label)
                    if backend == 'file':
                        expected[key] = value
                    else:
                        runner.equal(expected[key], value, backend+' '+label)
                    results.append(dict(backend=backend, repeat=repeat, op=op, case=label, result=value))
                    print(backend, repeat, op, label, 'verified', flush=True)
        report = dict(kind='correctness only' if a.correctness_only else 'supplementary serial performance',
                      eligibility_version=2, health_policy=POLICY, eligible_cases=sum(eligibility),
                      performance_eligible=all(eligibility), cases=len(results), repeats=a.repeats,
                      requests=a.requests, joins=workload['joins'], functional=functionality, trials=results)
        (runner.out/'coverage.json').write_text(json.dumps(report, indent=2))
    except BaseException:
        (runner.out/'FAILED').write_text(utc()+'\n')
        raise
    finally:
        try:
            runner.cleanup()
        except BaseException:
            (runner.out/'FAILED').write_text(utc()+'\nCleanup failed\n')
            raise
    (runner.out/'COMPLETE').write_text(utc()+'\n')


if __name__ == '__main__':
    main()
