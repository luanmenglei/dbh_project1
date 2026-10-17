#!/usr/bin/env python3
"""Audit the shared-host point and index experiments before deriving report numbers."""
import argparse
import json
import math
from pathlib import Path
import statistics

from analyze_coverage import analyze as audit_coverage
from evidence import require, verify_source
from trial_health import qualify

ROOT = Path(__file__).resolve().parents[1]


def stats(values):
    require(bool(values) and all(math.isfinite(v) and v >= 0 for v in values), 'Invalid measurements')
    return dict(n=len(values), median=statistics.median(values), min=min(values), max=max(values))


def records(path):
    require((path/'COMPLETE').exists() and not (path/'FAILED').exists(), 'Incomplete experiment')
    rows = [json.loads(s) for s in (path/'records.jsonl').read_text().splitlines()]
    for row in rows:
        require(json.loads((path/(row['log']+'.stdout')).read_text()) == row['result'], 'Raw output mismatch')
        if 'health' in row:
            h = row['health']
            verdict = qualify(h['samples'], h['correctness_only'], h['policy'])
            require(all(h[k] == v for k, v in verdict.items()), 'Health verdict mismatch')
            require(row['measured'] == verdict['performance_eligible'], 'Measurement label mismatch')
    hashes = json.loads((path/'source-sha256.json').read_text())
    for name, digest in hashes.items():
        verify_source(path, ROOT, name, digest)
    return rows, hashes


def analyze(point_path, index_path):
    coverage = audit_coverage(point_path)
    point_records, point_hashes = records(point_path)
    index_records, index_hashes = records(index_path)
    require(point_hashes['src/filmbench.cpp'] == index_hashes['src/filmbench.cpp'], 'Different client sources')
    require(coverage['requests'] == 100000 and coverage['repeats'] == 5, 'Unexpected point protocol')
    require(coverage['performance_eligible'], 'Point supplement contains excluded trials')
    for directory in [point_path, index_path]:
        require(json.loads((directory/'health-policy.json').read_text()) == coverage['health_policy'],
                'Saved health policy differs from measured policy')
    point = []
    for backend in ['file', 'postgres', 'opengauss']:
        for distribution in ['uniform', 'hot90']:
            rows = [r for r in point_records if r.get('phase') == 'coverage' and r['backend'] == backend
                    and r['op'] == 'point' and r['case'] == distribution]
            require(len(rows) == 5 and all(r['scale'] == 100 and r['result']['requests'] == 100000 for r in rows), 'Wrong point sample')
            point.append(dict(backend=backend, distribution=distribution,
                              mean_us=stats([r['result']['mean_ms'] * 1000 for r in rows]),
                              batch_s=stats([r['result']['elapsed_s'] for r in rows]),
                              setup_s=stats([r['result']['backend_setup_s'] for r in rows]),
                              prepare_s=stats([r['result']['request_prepare_s'] for r in rows])))
    summary = json.loads((index_path/'ablation.json').read_text())
    require(summary['health_policy'] == coverage['health_policy'] and summary['performance_eligible'],
            'Ablation eligibility or policy mismatch')
    args = json.loads((index_path/'run.json').read_text())['args']
    require(args['scale'] == 100 and args['repeats'] == 5, 'Unexpected ablation protocol')
    require(args.get('reuse_loaded_data'), 'Expected fixed-data index switching protocol')
    references = {}
    final_checks = set()
    for row in index_records:
        if row['op'] != 'fingerprint':
            continue
        command = row['command']
        table = command[command.index('--table')+1]
        if row['backend'] == 'file':
            references[table] = row['result']
        else:
            require(row['result'] == references[table], 'Ablation changed table contents')
            if row.get('phase') == 'final_data_check':
                final_checks.add((row['backend'], table))
    require(final_checks == {(b, t) for b in ['postgres', 'opengauss']
                             for t in ['countries', 'movies', 'people', 'credits']}, 'Missing final table validation')
    selected = [r for r in index_records if r.get('phase') == 'index_ablation']
    expected = {(b, r, v) for b in ['postgres', 'opengauss'] for r in range(5)
                for v in ['with_index', 'without_index']}
    require(len(selected) == len(expected) and
            {(r['backend'], r['repeat'], r['variant']) for r in selected} == expected, 'Incomplete ablation matrix')
    from run_index_ablation import schema_variants
    variants = schema_variants((index_path/'source-snapshot/sql/schema.sql').read_text())
    for name, ddl in variants.items():
        require((index_path/(name+'.sql')).read_text() == ddl, 'Unexpected DDL change')
    reference = next(r['result'] for r in index_records if r['backend'] == 'file' and r['op'] == 'join')
    require(reference['rows'] == 54, 'Unexpected join cardinality')
    logged = []
    for r in selected:
        require(r['measured'] and 'health' in r, 'Excluded or unmonitored ablation trial')
        require(all(r['result'][k] == reference[k] for k in ['rows', 'checksum']), 'Join result mismatch')
        key = (r['backend'], r['repeat'], r['variant'])
        plans = [x for x in index_records if x['op'] == 'explain' and
                 (x['backend'], x['repeat'], x['variant']) == key]
        require(len(plans) == 1, 'Missing or duplicated execution plan')
        uses_index = any('credits_person_idx' in line for line in plans[0]['result']['plan'])
        require(uses_index == (r['variant'] == 'with_index'), 'Unexpected join access path')
        logged.append(dict(backend=r['backend'], repeat=r['repeat'], variant=r['variant'],
                           result=r['result'], performance_eligible=r['measured']))
    require(logged == summary['trials'], 'Ablation summary differs from records')
    control_logs = set()
    for backend in ['postgres', 'opengauss']:
        for repeat in range(5):
            variant = 'without_index' if repeat % 2 == 0 else 'with_index'
            name = f'index-control-{backend}-{repeat}-{variant}.stdout'
            control_logs.add(name)
            require(json.loads((index_path/name).read_text()) == dict(
                schema='filmbench_s100', person_index_present=variant == 'with_index'),
                'Missing or inconsistent index control result')
    require({p.name for p in index_path.glob('index-control-*.stdout')} == control_logs,
            'Unexpected index switches')
    index = []
    for backend in ['postgres', 'opengauss']:
        trials = {(r['repeat'], r['variant']): r['result']['elapsed_s'] for r in selected if r['backend'] == backend}
        ratios = [trials[r, 'without_index'] / trials[r, 'with_index'] for r in range(5)]
        saved = [p for p in summary['pairs'] if p['backend'] == backend]
        require(len(saved) == 5 and [p['repeat'] for p in saved] == list(range(5)), 'Incorrect pairing')
        require(all(p['performance_eligible'] and math.isclose(p['speedup'], value, rel_tol=1e-12)
                    for p, value in zip(saved, ratios)), 'Incorrect paired speedup')
        index.append(dict(backend=backend, speedup=stats(ratios),
                          with_index_ms=stats([trials[r, 'with_index']*1000 for r in range(5)]),
                          without_index_ms=stats([trials[r, 'without_index']*1000 for r in range(5)])))
    guarded = [r for r in point_records+index_records if 'health' in r]
    require(len(guarded) == 110, 'Unexpected number of monitored measurements')
    require(all(r['health']['policy'] == coverage['health_policy'] for r in guarded), 'Different health protocols')
    configurations = []
    for directory in [point_path, index_path]:
        configuration = json.loads((directory/'run.json').read_text())
        require(configuration['args']['shared_host_reason'] == coverage['health_policy']['shared_host_reason'], 'Missing shared-host declaration')
        configurations.append({b: {k: configuration['targets'][b].get(k) for k in
                                  ['binary', 'client_prefix', 'dsn_env', 'start_command']}
                               for b in ['file', 'postgres', 'opengauss']})
    require(configurations[0] == configurations[1], 'Different backend configurations')
    for row in point_records+index_records:
        if row['op'] == 'snapshot':
            settings = {key: value for key, value, unit in row['result']['settings']}
            require(settings.get('fsync') == 'on' and settings.get('synchronous_commit') == 'on', 'Unexpected durability settings')
    loads = [s['load1'] for r in guarded for s in r['health']['samples']]
    cpu_deltas = {}
    for r in guarded:
        samples = r['health']['samples']
        for a, b in zip(samples, samples[1:]):
            for cpu, first in a['cpu_ticks'].items():
                last = b['cpu_ticks'][cpu]
                total = sum(last)-sum(first)
                idle = last[3]+last[4]-first[3]-first[4]
                if total > 0 and 0 <= idle <= total:
                    acc = cpu_deltas.setdefault(cpu, [0, 0])
                    acc[0] += total
                    acc[1] += total-idle
    return dict(source_sha256=point_hashes['src/filmbench.cpp'], point=point, index=index,
                policy=coverage['health_policy'], load_range=[min(loads), max(loads)],
                cpu_busy_percent={k: 100*v[1]/v[0] for k, v in cpu_deltas.items()},
                cases=len(guarded), point_requests=coverage['requests'], repeats=5,
                index_build_s=next(r['result']['elapsed_s'] for r in point_records if r['op'] == 'build_index'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--point', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.point, args.index)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
