"""Keep annotation-only source identity checks from accepting code or SQL changes."""
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
        for name in ('src/filmbench.cpp', 'docs/source-readability.json', self.record['baseline']):
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

    def test_current_source_and_nested_regions(self):
        self.verify()
        depth = regions = 0
        for line in (self.root / 'src/filmbench.cpp').read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('#pragma region '):
                depth += 1
                regions += 1
            elif line == '#pragma endregion':
                depth -= 1
                self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)
        self.assertEqual(regions, 16)

    def test_stale_manifest_rejected(self):
        self.alter(b'20261012', b'20261013')
        with self.assertRaisesRegex(ValueError, 'Annotated source hash mismatch'):
            self.verify()

    def test_updated_manifest_cannot_hide_changed_code(self):
        self.alter(b'20261012', b'20261013', update_manifest=True)
        with self.assertRaisesRegex(ValueError, 'changed existing code'):
            self.verify()

    def test_updated_manifest_cannot_hide_inserted_code(self):
        self.alter(b'int main(', b'int unexpected = 1;\nint main(', update_manifest=True)
        with self.assertRaisesRegex(ValueError, 'executable changes'):
            self.verify()

    def test_comment_in_raw_sql_is_not_an_annotation(self):
        self.alter(b'CREATE TABLE countries', b'// changes the SQL\nCREATE TABLE countries',
                   update_manifest=True)
        with self.assertRaisesRegex(ValueError, 'raw literal'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
