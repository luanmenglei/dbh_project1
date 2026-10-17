"""Reject damaged supplemental evidence before it reaches the report."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from analyze_supplement import analyze


@unittest.skipUnless((ROOT/'results/supplement-20261015-index/COMPLETE').exists(),
                     'Completed supplementary evidence is required')
class SupplementAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.point = Path(cls.temp.name)/'point'
        cls.index = Path(cls.temp.name)/'index'
        for source, destination in [('point', cls.point), ('index', cls.index)]:
            shutil.copytree(ROOT/('results/supplement-20261015-'+source), destination,
                            ignore=shutil.ignore_patterns('*resources.jsonl'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def changed(self, path, content):
        original = path.read_bytes()
        path.write_bytes(content)
        try:
            yield
        finally:
            path.write_bytes(original)

    def test_complete_evidence(self):
        result = analyze(self.point, self.index)
        self.assertEqual(result['cases'], 110)
        self.assertEqual(result['repeats'], 5)

    def test_raw_output_mismatch(self):
        rows = [json.loads(s) for s in (self.point/'records.jsonl').read_text().splitlines()]
        row = next(r for r in rows if r.get('phase') == 'coverage')
        value = dict(row['result'], rows=row['result']['rows']+1)
        with self.changed(self.point/(row['log']+'.stdout'), json.dumps(value).encode()):
            with self.assertRaisesRegex(ValueError, 'Raw coverage output changed'):
                analyze(self.point, self.index)

    def test_relabelled_health_policy(self):
        path = self.index/'health-policy.json'
        value = json.loads(path.read_text())
        value['max_load1'] = 1000
        with self.changed(path, json.dumps(value).encode()):
            with self.assertRaisesRegex(ValueError, 'health policy'):
                analyze(self.point, self.index)

    def test_wrong_index_state(self):
        path = self.index/'index-control-postgres-0-without_index.stdout'
        value = dict(schema='filmbench_s100', person_index_present=True)
        with self.changed(path, json.dumps(value).encode()):
            with self.assertRaisesRegex(ValueError, 'index control result'):
                analyze(self.point, self.index)

    def test_changed_source_snapshot(self):
        path = self.index/'source-snapshot/src/filmbench.cpp'
        with self.changed(path, path.read_bytes()+b'\n// altered\n'):
            with self.assertRaises(ValueError):
                analyze(self.point, self.index)


if __name__ == '__main__':
    unittest.main()
