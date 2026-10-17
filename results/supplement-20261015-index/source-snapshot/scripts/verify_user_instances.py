#!/usr/bin/env python3
"""Run the independent correctness oracle against private user services, never formal timings."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

from run_suite import Runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'deploy/state/targets.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    spec = importlib.util.spec_from_file_location('project_tests', ROOT/'tests/test_project.py')
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    report = {'kind': 'correctness only; not formal performance results',
              'started': dt.datetime.now(dt.timezone.utc).isoformat(), 'backends': {}}

    def stop():
        for backend in ['postgres', 'opengauss']:
            Runner.stop_user_service(config[backend]['service'])

    try:
        stop()
        tests.BIN = Path(config['file']['binary'])
        tests.test_fixture(args.out)
        tests.test_course(args.out)
        report['backends']['file'] = 'passed'
        for backend in ['postgres', 'opengauss']:
            stop()
            subprocess.run(config[backend]['start_command'], check=True, capture_output=True)
            tests.BIN = Path(config[backend]['binary'])
            env = os.environ.copy()
            env['FILMBENCH_DSN'] = env[config[backend]['dsn_env']]
            deadline = time.monotonic()+60
            while True:
                try:
                    snapshot = tests.run(args.out, 'snapshot', backend, env=env)
                    break
                except AssertionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            report[backend+'_version'] = snapshot['version']
            tests.test_fixture(args.out, backend, env)
            tests.test_course(args.out, backend, env)
            report.setdefault('functional', {})[backend] = tests.run(args.out, 'functional', backend, env=env)
            report['backends'][backend] = 'passed'
        report['completed'] = dt.datetime.now(dt.timezone.utc).isoformat()
        report['coverage'] = ['independent Python result fingerprints', 'NULL and empty substring',
                              'apostrophe, tab, newline, backslash and Unicode', 'multiple credit roles',
                              'all-NULL aggregation', 'sequential/indexed/SQL point parity',
                              'durable update', 'stale index rejection', 'read-only execution plans',
                              'concurrent read and mixed write', 'restore after updates',
                              's1/s2 course data', 'schema scope guard', 'external requests and split setup timing',
                              'foreign-key SQLSTATE 23503', 'failed transaction restores earlier update']
        import hashlib
        report['source_sha256'] = hashlib.sha256((ROOT/'src/filmbench.cpp').read_bytes()).hexdigest()
    except Exception:
        (args.out/'FAILED').write_text(dt.datetime.now(dt.timezone.utc).isoformat()+'\n')
        raise
    finally:
        stop()
        (args.out/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    (args.out/'COMPLETE').write_text(report['completed']+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
