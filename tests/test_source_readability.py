"""Bind the namespace refactor to its separate three-backend correctness evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evidence import verify_readable_source


class SourceReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = json.loads((ROOT / 'docs/source-readability.json').read_text())
        for name in ('src/filmbench.cpp', 'docs/source-readability.json', self.record['baseline'],
                     self.record['verification'], 'results/review-namespace-20261018/COMPLETE',
                     'results/review-namespace-20261018/source-snapshot/src/filmbench.cpp'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def verify(self):
        verify_readable_source(self.root, self.record['verified_source_sha256'])

    def alter(self, old, new, update_manifest=False):
        path = self.root / 'src/filmbench.cpp'
        data = path.read_bytes()
        self.assertIn(old, data)
        path.write_bytes(data.replace(old, new, 1))
        if update_manifest:
            self.record['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (self.root / 'docs/source-readability.json').write_text(json.dumps(self.record))

    def test_current_source(self):
        self.verify()

    def test_stale_manifest_rejected(self):
        self.alter(b'20261012', b'20261013')
        with self.assertRaisesRegex(ValueError, 'Reorganized source hash mismatch'):
            self.verify()

    def test_updated_manifest_cannot_hide_changed_code(self):
        self.alter(b'20261012', b'20261013', update_manifest=True)
        with self.assertRaisesRegex(ValueError, 'verified namespace source'):
            self.verify()

    def test_failed_run_is_not_accepted(self):
        (self.root / 'results/review-namespace-20261018/FAILED').write_text('cleanup failed')
        with self.assertRaisesRegex(ValueError, 'Incomplete namespace verification'):
            self.verify()

    def test_each_backend_must_have_passed(self):
        path = self.root / self.record['verification']
        report = json.loads(path.read_text())
        report['backends']['opengauss'] = 'not run'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'backend verification failed'):
            self.verify()

    def test_changed_verification_snapshot_is_rejected(self):
        path = self.root / 'results/review-namespace-20261018/source-snapshot/src/filmbench.cpp'
        path.write_bytes(path.read_bytes() + b'// changed snapshot\n')
        with self.assertRaisesRegex(ValueError, 'Experiment source hash mismatch'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
