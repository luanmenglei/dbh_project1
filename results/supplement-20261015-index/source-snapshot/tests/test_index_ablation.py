"""Check ablation controls, pairing and failure cleanup without touching services."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import run_index_ablation as ablation
from manage_person_index import statements


class AblationTests(unittest.TestCase):
    def test_index_control_scope(self):
        for invalid in ['public', 'filmbench_s0', 'filmbench_s1; DROP TABLE movies']:
            with self.assertRaises(ValueError):
                statements(invalid, True)
        self.assertEqual(statements('filmbench_s100', False)[-2:], ['DROP INDEX IF EXISTS credits_person_idx', 'COMMIT'])
        self.assertNotIn('CREATE INDEX', '\n'.join(statements('filmbench_s100', False)))
        self.assertIn(ablation.INDEX.rstrip(';'), statements('filmbench_s100', True))

    def test_only_index_definition_changes(self):
        ddl = (ROOT/'sql/schema.sql').read_text()
        variants = ablation.schema_variants(ddl)
        self.assertEqual(variants['with_index'], ddl)
        self.assertEqual(variants['without_index'] + '', ddl.replace(ablation.INDEX, ''))
        with self.assertRaises(ValueError):
            ablation.schema_variants(variants['without_index'])

    def test_pairing_and_cleanup(self):
        for reuse, failure in [(False, None), (False, 'query'), (False, 'cleanup'), (True, None)]:
            with self.subTest(failure=failure, reuse=reuse), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                data = root/'data/s1'
                data.mkdir(parents=True)
                (data/'manifest.json').write_text(json.dumps(dict(files={}, counts={}, expected={'join_peopleid': 1})))
                calls, cleaned = [], []

                class Runner:
                    def __init__(self, args):
                        self.out = args.out
                        self.out.mkdir()
                        self.config = json.loads(args.config.read_text())

                    def activate(self, backend):
                        pass

                    def call(self, backend, scale, op, extra=None, **labels):
                        calls.append((backend, op, labels))
                        if failure == 'query' and labels.get('phase') == 'index_ablation':
                            raise RuntimeError('query failure')
                        if labels.get('health_check'):
                            with labels['health_check']:
                                pass
                        return {'rows': 54, 'checksum': '123', 'elapsed_s': 1}

                    def equal(self, a, b, context):
                        if a['rows'] != b['rows'] or a['checksum'] != b['checksum']:
                            raise AssertionError(context)

                    def cleanup(self):
                        if (self.out/'COMPLETE').exists():
                            raise AssertionError('Premature completion')
                        cleaned.append(True)
                        if failure == 'cleanup':
                            raise RuntimeError('cleanup failure')

                out = root/'result'
                config = root/'config.json'
                config.write_text(json.dumps({b: {'index_control': ['mock-control']} for b in
                                              ['postgres', 'opengauss']} if reuse else {}))
                flags = ['ablation', '--config', str(config), '--out', str(out), '--data', str(root/'data'),
                         '--scale', '1', '--repeats', '2', '--manage-user-services', '--correctness-only']
                sample = lambda phase: dict(phase=phase, load1=1, disk_free=100*2**30)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(ablation, 'Runner', Runner))
                    stack.enter_context(patch.object(ablation, 'sample_host', side_effect=sample))
                    stack.enter_context(patch('trial_health.sample_host', side_effect=sample))
                    stack.enter_context(patch('sys.argv', flags))
                    stack.enter_context(patch.object(ablation.subprocess, 'run', side_effect=lambda cmd, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps({'person_index_present': cmd[-1] == '1'}), stderr='')))
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, failure+' failure'):
                            ablation.main()
                    else:
                        ablation.main()
                self.assertEqual(cleaned, [True])
                self.assertEqual((out/'COMPLETE').exists(), failure is None)
                self.assertEqual((out/'FAILED').exists(), failure is not None)
                if failure is None:
                    report = json.loads((out/'ablation.json').read_text())
                    self.assertEqual(len(report['pairs']), 4)
                    self.assertTrue(all(p['speedup'] is None for p in report['pairs']))
                    loads = [(b, labels['variant']) for b, op, labels in calls if op == 'load']
                    if reuse:
                        self.assertEqual(loads, [('postgres', 'with_index'), ('opengauss', 'with_index')])
                    else:
                        self.assertEqual(loads[:2], [('postgres', 'with_index'), ('postgres', 'without_index')])
                        self.assertEqual(loads[4:6], [('opengauss', 'without_index'), ('opengauss', 'with_index')])


if __name__ == '__main__':
    unittest.main()
