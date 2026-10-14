#!/usr/bin/env python3
"""Audit the compact experiment against its full expected matrix; no server access."""
from evidence import require, verify_source
import argparse
import csv
import hashlib
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path


def audit(path):
    require((path / 'COMPLETE').is_file() and not (path / 'FAILED').exists(), 'analyze_results.py:14: validation failed')
    records = [json.loads(s) for s in (path / 'records.jsonl').read_text().splitlines()]
    args = json.loads((path / 'run.json').read_text())['args']
    require(args['profile'] in ['compact', 'full'], 'Unsupported experiment profile')
    groups = defaultdict(list)
    for r in records:
        raw = json.loads((path / (r['log'] + '.stdout')).read_text())
        require(raw == r['result'], 'Record differs from raw output: ' + r['log'])
        if r['measured']:
            require(r['result'].get('valid', True) and not r['result'].get('errors', 0), 'analyze_results.py:21: validation failed')
            key = (r['backend'], r['scale'], r['op'], r.get('keyword', ''),
                   r.get('indexed', 0), r.get('clients', 0))
            groups[key].append(r)
    expected = {}
    for scale in args['scales']:
        expected['file', scale, 'build_index', '', 0, 0] = 1
        for backend in args['backends']:
            for op in ['search_count', 'search_rows']:
                for keyword in ['The', 'Love', 'Star']:
                    expected[backend, scale, op, keyword, 0, 0] = args['repeats']
            for op in ['point', 'join', 'aggregate', 'update']:
                expected[backend, scale, op, '', 0, 0] = args['repeats']
            if backend == 'file':
                expected[backend, scale, 'point', '', 1, 0] = args['repeats']
    for scale in set(args['concurrency_scales']) & set(args['scales']):
        for backend in [b for b in args['backends'] if b != 'file']:
            for op in ['concurrent_read', 'mixed']:
                for clients in args['clients']:
                    expected[backend, scale, op, '', 0, clients] = args['load_repeats']
    require(set(groups) == set(expected), 'Missing or unexpected measured group')
    for key, rows in groups.items():
        require(len(rows) == expected[key], 'analyze_results.py:43: validation failed')
        if key[2] != 'build_index':
            require(sorted(r['repeat'] for r in rows) == list(range(expected[key])), 'analyze_results.py:45: validation failed')
    parity = defaultdict(set)
    for key, rows in groups.items():
        backend, scale, op, keyword, indexed, clients = key
        for r in rows:
            v = r['result']
            if op in ['point', 'search_count', 'search_rows', 'join', 'aggregate']:
                parity[scale, op, keyword].add((v['rows'], v['checksum']))
            if op == 'update':
                manifest = json.loads((path / f's{scale}-manifest.json').read_text())
                require(v['affected'] == manifest['expected']['updated_people'], 'analyze_results.py:55: validation failed')
    require(all(len(v) == 1 for v in parity.values()), 'Cross-backend digest mismatch')
    summary = list(csv.DictReader((path / 'summary.csv').open()))
    metrics = ['elapsed_s', 'setup_s', 'request_prepare_s', 'backend_setup_s', 'mean_ms',
               'throughput_ops_s', 'p50_ms', 'p95_ms', 'p99_ms', 'read_p95_ms', 'write_p95_ms', 'bytes']
    expected_summary = {(key, metric) for key, rows in groups.items()
                        for metric in metrics if metric in rows[0]['result']}
    seen_summary = set()
    for row in summary:
        key = (row['backend'], int(row['scale']), row['op'], row['keyword'],
               int(row['indexed']), int(row['clients']))
        entry = (key, row['metric'])
        require(entry in expected_summary and entry not in seen_summary, 'Unexpected/duplicate CSV statistic')
        seen_summary.add(entry)
        values = [r['result'][row['metric']] for r in groups[key]]
        require(len(values) == int(row['trials']), 'analyze_results.py:62: validation failed')
        for field, value in [('median', st.median(values)), ('minimum', min(values)), ('maximum', max(values))]:
            require(math.isclose(float(row[field]), value, rel_tol=1e-12, abs_tol=1e-12), 'analyze_results.py:64: validation failed')
    require(seen_summary == expected_summary, 'Missing CSV statistics')
    source_hashes = {}
    root = Path(__file__).resolve().parents[1]
    if (path / 'formal-source-sha256.txt').exists():
        hashes = {name: digest for digest, name in
                  (line.split(maxsplit=1) for line in (path / 'formal-source-sha256.txt').read_text().splitlines())}
    else:
        hashes = json.loads((path / 'source-sha256.json').read_text())
    require('src/filmbench.cpp' in hashes, 'Missing benchmark source identity')
    for name, digest in hashes.items():
        if name.startswith(('src/', 'scripts/', 'sql/')):
            verify_source(path, root, name, digest)
            source_hashes[name] = digest
    stats = []
    for key, rows in sorted(groups.items()):
        metrics = {}
        for metric in ['elapsed_s', 'setup_s', 'request_prepare_s', 'backend_setup_s', 'mean_ms', 'throughput_ops_s', 'p95_ms', 'p99_ms', 'read_p95_ms', 'write_p95_ms']:
            if metric in rows[0]['result']:
                values = [r['result'][metric] for r in rows]
                metrics[metric] = {'median': st.median(values), 'min': min(values), 'max': max(values)}
        if 'setup_s' in rows[0]['result']:
            values = [r['result']['setup_s'] + r['result']['elapsed_s'] for r in rows]
            metrics['setup_plus_elapsed_s'] = {'median': st.median(values), 'min': min(values), 'max': max(values)}
        stats.append(dict(zip(['backend', 'scale', 'op', 'keyword', 'indexed', 'clients'], key), n=len(rows), metrics=metrics))
    result = {'record_count': len(records), 'measured_count': sum(map(len, groups.values())),
              'group_count': len(groups), 'parity_groups': len(parity),
              'summary_rows_verified': len(summary), 'source_hashes_verified': source_hashes,
              'checks': 'complete matrix, repeats, measured validity, cross-backend digests, affected counts, CSV statistics, source hashes',
              'groups': stats}
    (path / 'analysis.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('results', type=Path)
    a = p.parse_args()
    result = audit(a.results)
    print(json.dumps({k: v for k, v in result.items() if k != 'groups'}, indent=2))
