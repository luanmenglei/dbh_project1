#!/usr/bin/env python3
"""Validate the complete supplementary workload matrix and its source snapshot."""
import argparse
import json
from pathlib import Path
from evidence import require, verify_source
from prepare import checksum

ROOT = Path(__file__).resolve().parents[1]


def analyze(path):
    require((path/'COMPLETE').is_file() and not (path/'FAILED').exists(), 'Incomplete coverage experiment')
    report = json.loads((path/'coverage.json').read_text())
    plan = json.loads((path/'requests/manifest.json').read_text())
    records = [json.loads(line) for line in (path/'records.jsonl').read_text().splitlines()]
    selected = [r for r in records if r.get('phase') == 'coverage']
    expected = {(b, r, op, case) for b in ['file', 'postgres', 'opengauss'] for r in range(plan['repeats'])
                for op, cases in [('join', [j['label'] for j in plan['joins']]), ('point', ['uniform', 'hot90'])] for case in cases}
    actual = {(r['backend'], r['repeat'], r['op'], r['case']) for r in selected}
    require(actual == expected and len(selected) == len(expected), 'Coverage matrix incomplete or duplicated')
    for stream in plan['streams']:
        require(checksum(path/'requests'/stream['file']) == stream['sha256'], 'Request stream changed')
        require(stream['count'] == report['requests'], 'Request count mismatch')
    parity = {}
    for r in selected:
        value = r['result']
        require(json.loads((path/(r['log']+'.stdout')).read_text()) == value, 'Raw coverage output changed')
        require(r['measured'] == report['performance_eligible'], 'Incorrect measured label')
        require(value['timing_version'] == 2, 'Expected split timing')
        require(min(value[k] for k in ['request_prepare_s', 'backend_setup_s', 'elapsed_s']) >= 0, 'Negative timing')
        rows = plan['requests'] if r['op'] == 'point' else next(j['expected_rows'] for j in plan['joins'] if j['label'] == r['case'])
        require(value['rows'] == rows, 'Wrong coverage result count')
        key = (r['repeat'], r['op'], r['case'])
        digest = (value['rows'], value['checksum'])
        require(parity.setdefault(key, digest) == digest, 'Cross-backend coverage mismatch')
    logged = [dict(backend=r['backend'], repeat=r['repeat'], op=r['op'], case=r['case'], result=r['result']) for r in selected]
    require(logged == report['trials'] and report['cases'] == len(selected), 'Coverage summary differs from records')
    for backend in ['postgres', 'opengauss']:
        f = report['functional'][backend]
        require(f['foreign_key_rejected'] and f['transaction_rollback_restored'] and f['intermediate_update_observed'] and f['sqlstate'] == '23503', 'Functional checks failed')
        raw = [r['result'] for r in records if r['backend'] == backend and r['op'] == 'functional']
        require(len(raw) == plan['repeats'] and all(v == f for v in raw), 'Missing functional evidence')
    hashes = json.loads((path/'source-sha256.json').read_text())
    require('src/filmbench.cpp' in hashes, 'Missing source identity')
    for name, digest in hashes.items():
        verify_source(path, ROOT, name, digest)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('results', type=Path)
    a = p.parse_args()
    r = analyze(a.results)
    print(json.dumps({k: v for k, v in r.items() if k != 'trials'}, indent=2))
