"""Local regression tests for cleanup and evidence rejection, including python -O."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from run_suite import Runner


class HarnessTests(unittest.TestCase):
    def test_docker_cleanup_attempts_both_after_failure(self):
        runner = Runner.__new__(Runner)
        runner.args = SimpleNamespace(manage_user_services=False, manage_containers=True)
        runner.config = {'postgres': {'container': 'pg'}, 'opengauss': {'container': 'og'}}
        with patch('run_suite.subprocess.run', side_effect=[RuntimeError('pg failed'), None]) as call:
            with self.assertRaisesRegex(RuntimeError, 'pg failed'):
                runner.cleanup()
            self.assertEqual([c.args[0] for c in call.call_args_list],
                             [['docker', 'stop', 'pg'], ['docker', 'stop', 'og']])

    def test_user_cleanup_attempts_both_after_failure(self):
        runner = Runner.__new__(Runner)
        runner.args = SimpleNamespace(manage_user_services=True, manage_containers=False)
        runner.config = {'postgres': {'service': 'pg'}, 'opengauss': {'service': 'og'}}
        with patch.object(Runner, 'stop_user_service', side_effect=[RuntimeError('pg failed'), None]) as call:
            with self.assertRaisesRegex(RuntimeError, 'pg failed'):
                runner.cleanup()
            self.assertEqual([c.args[0] for c in call.call_args_list], ['pg', 'og'])

    def test_audit_rejects_incomplete_corrupt_and_truncated_evidence_under_optimization(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            def write(name, obj):
                (out/name).write_text(json.dumps(obj), encoding='utf-8')
            args = dict(profile='compact', scales=[1], backends=['file'], repeats=1,
                        concurrency_scales=[], clients=[1], load_repeats=1)
            write('run.json', {'args': args})
            write('s1-manifest.json', {'expected': {'updated_people': 1}})
            jobs = [('build_index', '', 0)]
            jobs += [(op, word, 0) for op in ['search_count', 'search_rows'] for word in ['The', 'Love', 'Star']]
            jobs += [(op, '', 0) for op in ['point', 'join', 'aggregate', 'update']]
            jobs += [('point', '', 1)]
            records = []
            for i, (op, keyword, indexed) in enumerate(jobs):
                value = dict(elapsed_s=1.0, rows=1, checksum='2', affected=1)
                record = dict(backend='file', scale=1, op=op, keyword=keyword,
                              indexed=indexed, repeat=0, measured=True, log=str(i), result=value)
                records.append(record)
                write(str(i)+'.stdout', value)
            (out/'records.jsonl').write_text('\n'.join(map(json.dumps, records)))
            (out/'COMPLETE').touch()
            source = out/'source-snapshot/src/filmbench.cpp'
            source.parent.mkdir(parents=True)
            source.write_text('// test fixture\n')
            write('source-sha256.json', {'src/filmbench.cpp': hashlib.sha256(source.read_bytes()).hexdigest()})
            subprocess.run([sys.executable, str(ROOT/'scripts/summarize.py'), str(out)], check=True, capture_output=True)
            def audit(ok):
                result = subprocess.run([sys.executable, '-O', str(ROOT/'scripts/analyze_results.py'), str(out)], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, ok, result.stderr)
            audit(True)
            (out/'COMPLETE').unlink(); audit(False); (out/'COMPLETE').touch()
            original = source.read_text(); source.write_text('// corrupt\n'); audit(False); source.write_text(original)
            summary = (out/'summary.csv').read_text()
            (out/'summary.csv').write_text(summary.splitlines()[0]+'\n'); audit(False)
            (out/'summary.csv').write_text(summary)
            write('0.stdout', {'elapsed_s': 999}); audit(False)


if __name__ == '__main__':
    unittest.main()
