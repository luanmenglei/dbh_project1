#!/usr/bin/env python3
"""Compare the same join with/without credits_person_idx in dedicated benchmark schemas."""
import argparse
import json
from pathlib import Path
import subprocess

from evidence import require
from prepare import checksum
from run_suite import ROOT, Runner, utc
from trial_health import TrialHealth, sample_host, add_policy_args, policy_for, cpu_sample

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
    parser.add_argument('--reuse-loaded-data', action='store_true',
                        help='Load once per DB, then switch only the index with configured index_control commands')
    add_policy_args(parser)
    management = parser.add_mutually_exclusive_group(required=True)
    management.add_argument('--manage-user-services', action='store_true')
    management.add_argument('--manage-containers', action='store_true')
    args = parser.parse_args()
    targets = json.loads(args.config.read_text())
    args.reuse_loaded_data = args.reuse_loaded_data or all(
        targets.get(b, {}).get('index_control') for b in ['postgres', 'opengauss'])
    policy = policy_for(args)
    if min(args.scale, args.repeats) < 1:
        parser.error('scale and repeats must be positive')
    if args.reuse_loaded_data and not all(targets.get(b, {}).get('index_control') for b in ['postgres', 'opengauss']):
        parser.error('reuse-loaded-data needs index_control commands for both databases')
    runner = Runner(args)
    trials = []
    loaded = set()
    index_state = {}
    try:
        health = sample_host('start')
        (runner.out/'health.json').write_text(json.dumps(health, indent=2))
        (runner.out/'health-policy.json').write_text(json.dumps(policy, indent=2))
        require(health['disk_free'] > policy['min_disk_free'], 'Insufficient free disk space')
        require(args.correctness_only or policy['max_load1'] is None or health['load1'] <= policy['max_load1'], 'Busy host; defer performance experiment')
        if policy['version'] == 2:
            require(cpu_sample(policy['monitored_cpus'])['available_memory'] >= policy['min_available_memory'], 'Insufficient available memory')
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
                    if not args.reuse_loaded_data or backend not in loaded:
                        runner.call(backend, args.scale, 'load',
                                    ['--reset', '--ddl', str(runner.out/(variant+'.sql'))], **labels)
                        for table, reference in fingerprints.items():
                            runner.equal(reference, runner.call(backend, args.scale, 'fingerprint', ['--table', table]), table)
                        loaded.add(backend)
                        index_state[backend] = variant
                    if args.reuse_loaded_data and index_state[backend] != variant:
                        command = runner.config[backend]['index_control'] + [
                            '--schema', f'filmbench_s{args.scale}', '--present', '1' if variant == 'with_index' else '0']
                        changed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=args.timeout)
                        stem = f'index-control-{backend}-{repeat}-{variant}'
                        (runner.out/(stem+'.stdout')).write_text(changed.stdout)
                        (runner.out/(stem+'.stderr')).write_text(changed.stderr)
                        require(changed.returncode == 0, 'Index switch failed; see '+stem)
                        require(json.loads(changed.stdout)['person_index_present'] == (variant == 'with_index'), 'Index switch mismatch')
                        index_state[backend] = variant
                    runner.call(backend, args.scale, 'snapshot', **labels)
                    runner.call(backend, args.scale, 'explain', ['--query', 'join', *query_args], **labels)
                    warm = runner.call(backend, args.scale, 'join', query_args, phase='warmup', **labels)
                    runner.equal(expected, warm, 'warm join')
                    guard = TrialHealth(args.correctness_only, policy)
                    value = runner.call(backend, args.scale, 'join', query_args, measured=not args.correctness_only,
                                        health_check=guard, phase='index_ablation', **labels)
                    runner.equal(expected, value, backend+' '+variant)
                    trials.append(dict(backend=backend, **labels, result=value,
                                       performance_eligible=guard.report()['performance_eligible']))
        if args.reuse_loaded_data:
            for backend in ['postgres', 'opengauss']:
                runner.activate(backend)
                for table, reference in fingerprints.items():
                    runner.equal(reference, runner.call(backend, args.scale, 'fingerprint', ['--table', table], phase='final_data_check'), table)
        pairs = []
        for backend in ['postgres', 'opengauss']:
            for repeat in range(args.repeats):
                pair = {r['variant']: r for r in trials if r['backend'] == backend and r['repeat'] == repeat}
                eligible = all(r['performance_eligible'] for r in pair.values())
                pairs.append(dict(backend=backend, repeat=repeat, performance_eligible=eligible,
                                  speedup=(pair['without_index']['result']['elapsed_s'] /
                                           pair['with_index']['result']['elapsed_s']) if eligible else None))
        (runner.out/'ablation.json').write_text(json.dumps(dict(
            kind='index ablation', health_policy=policy, trials=trials, pairs=pairs,
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
