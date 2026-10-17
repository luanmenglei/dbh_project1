#!/usr/bin/env python3
"""Compare the same join with/without credits_person_idx in dedicated benchmark schemas."""
import argparse
import json
from pathlib import Path

from evidence import require
from prepare import checksum
from run_suite import ROOT, Runner, utc
from trial_health import TrialHealth, sample_host, POLICY

INDEX = 'CREATE INDEX credits_person_idx ON credits(peopleid, movieid);'


def schema_variants(ddl):
    require(ddl.count(INDEX) == 1, 'Expected exactly one person index definition')
    return {'with_index': ddl, 'without_index': ddl.replace(INDEX, '')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data', type=Path, default=ROOT/'data/generated')
    parser.add_argument('--scale', type=int, default=100)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--correctness-only', action='store_true')
    management = parser.add_mutually_exclusive_group(required=True)
    management.add_argument('--manage-user-services', action='store_true')
    management.add_argument('--manage-containers', action='store_true')
    args = parser.parse_args()
    if min(args.scale, args.repeats) < 1:
        parser.error('scale and repeats must be positive')
    runner = Runner(args)
    trials = []
    try:
        health = sample_host('start')
        (runner.out/'health.json').write_text(json.dumps(health, indent=2))
        require(health['disk_free'] > POLICY['min_disk_free'], 'Insufficient free disk space')
        require(args.correctness_only or health['load1'] <= POLICY['max_load1'], 'Busy host; defer performance experiment')
        data = args.data/f's{args.scale}'
        manifest = json.loads((data/'manifest.json').read_text())
        for table, info in manifest['files'].items():
            require(checksum(data/f'{table}.tsv') == info['sha256'], 'Input changed: '+table)
        (runner.out/'data-manifest.json').write_text(json.dumps(manifest, indent=2))
        variants = schema_variants((ROOT/'sql/schema.sql').read_text())
        for name, ddl in variants.items():
            (runner.out/(name+'.sql')).write_text(ddl)
        query_args = ['--id', str(manifest['expected']['join_peopleid'])]
        runner.activate('file')
        expected = runner.call('file', args.scale, 'join', query_args)
        fingerprints = {table: runner.call('file', args.scale, 'fingerprint', ['--table', table])
                        for table in manifest['counts']}
        for repeat in range(args.repeats):
            backends = ['postgres', 'opengauss']
            order = list(variants)
            if repeat % 2:
                backends.reverse()
                order.reverse()
            for backend in backends:
                runner.activate(backend)
                for variant in order:
                    labels = dict(repeat=repeat, variant=variant)
                    runner.call(backend, args.scale, 'load',
                                ['--reset', '--ddl', str(runner.out/(variant+'.sql'))], **labels)
                    for table, reference in fingerprints.items():
                        runner.equal(reference, runner.call(backend, args.scale, 'fingerprint', ['--table', table]), table)
                    runner.call(backend, args.scale, 'snapshot', **labels)
                    runner.call(backend, args.scale, 'explain', ['--query', 'join', *query_args], **labels)
                    warm = runner.call(backend, args.scale, 'join', query_args, phase='warmup', **labels)
                    runner.equal(expected, warm, 'warm join')
                    guard = TrialHealth(args.correctness_only)
                    value = runner.call(backend, args.scale, 'join', query_args, measured=not args.correctness_only,
                                        health_check=guard, phase='index_ablation', **labels)
                    runner.equal(expected, value, backend+' '+variant)
                    trials.append(dict(backend=backend, **labels, result=value,
                                       performance_eligible=guard.report()['performance_eligible']))
        pairs = []
        for backend in ['postgres', 'opengauss']:
            for repeat in range(args.repeats):
                pair = {r['variant']: r for r in trials if r['backend'] == backend and r['repeat'] == repeat}
                eligible = all(r['performance_eligible'] for r in pair.values())
                pairs.append(dict(backend=backend, repeat=repeat, performance_eligible=eligible,
                                  speedup=(pair['without_index']['result']['elapsed_s'] /
                                           pair['with_index']['result']['elapsed_s']) if eligible else None))
        (runner.out/'ablation.json').write_text(json.dumps(dict(
            kind='index ablation', health_policy=POLICY, trials=trials, pairs=pairs,
            performance_eligible=all(p['performance_eligible'] for p in pairs)), indent=2))
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
