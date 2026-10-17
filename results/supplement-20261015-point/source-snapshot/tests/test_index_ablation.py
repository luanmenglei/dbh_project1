"""Check ablation controls, pairing and failure cleanup without touching services."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import run_index_ablation as ablation


class AblationTests(unittest.TestCase):
    def test_only_index_definition_changes(self):
        ddl = (ROOT/'sql/schema.sql').read_text()
        variants = ablation.schema_variants(ddl)
        self.assertEqual(variants['with_index'], ddl)
        self.assertEqual(variants['without_index'] + '', ddl.replace(ablation.INDEX, ''))
        with self.assertRaises(ValueError):
            ablation.schema_variants(variants['without_index'])

    def test_pairing_and_cleanup(self):
        for failure in [None, 'query', 'cleanup']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                data = root/'data/s1'
                data.mkdir(parents=True)
                (data/'manifest.json').write_text(json.dumps(dict(files={}, counts={}, expected={'join_peopleid': 1})))
                calls, cleaned = [], []

                class Runner:
                    def __init__(self, args):
                        self.out = args.out
                        self.out.mkdir()

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
                flags = ['ablation', '--config', 'unused.json', '--out', str(out), '--data', str(root/'data'),
                         '--scale', '1', '--repeats', '2', '--manage-user-services', '--correctness-only']
                sample = lambda phase: dict(phase=phase, load1=1, disk_free=100*2**30)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(ablation, 'Runner', Runner))
                    stack.enter_context(patch.object(ablation, 'sample_host', side_effect=sample))
                    stack.enter_context(patch('trial_health.sample_host', side_effect=sample))
                    stack.enter_context(patch('sys.argv', flags))
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
                    self.assertEqual(loads[:2], [('postgres', 'with_index'), ('postgres', 'without_index')])
                    self.assertEqual(loads[4:6], [('opengauss', 'without_index'), ('opengauss', 'with_index')])


if __name__ == '__main__':
    unittest.main()
