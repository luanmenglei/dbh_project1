"""Run scaling entry points with fake services; never connect to a database."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_scaling_probe as probe
import run_scaling_confirmation as confirmation


class ScalingCleanupTests(unittest.TestCase):
    def run_entry(self, module, flags, expected_trials, failure):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            calls = {'measured': 0, 'stops': 0}

            class FakeRunner:
                def __init__(self, args):
                    self.out = out
                    self.config = {b: {'start_command': ['taskset', '-c', '8-15']}
                                   for b in ['postgres', 'opengauss']}

                def stop_user_services(self):
                    calls['stops'] += 1
                    # Completion must never be published before cleanup finishes.
                    if (out / 'COMPLETE').exists():
                        raise AssertionError('Premature COMPLETE')
                    if failure == 'cleanup' and calls['measured'] == expected_trials:
                        raise RuntimeError('simulated cleanup failure')

                def activate(self, backend):
                    pass

                def load(self, *args, **kwargs):
                    pass

                def equal(self, *args):
                    pass

                def call(self, backend, scale, op, *args, **kwargs):
                    if op == 'snapshot':
                        return {'settings': [('fsync', 'on'), ('synchronous_commit', 'on')]}
                    if kwargs.get('measured'):
                        if failure == 'trial':
                            raise RuntimeError('simulated trial failure')
                        calls['measured'] += 1
                    return {'valid': True, 'errors': 0}

            read_text, read_bytes = Path.read_text, Path.read_bytes

            def fixture_text(path, *args, **kwargs):
                name = path.as_posix()
                if name == '/proc/meminfo':
                    return 'MemAvailable: 100000000 kB\n'
                if name == '/proc/loadavg':
                    return '0.0 0.0 0.0 1/1 1\n'
                if path == ROOT / 'data/generated/s10/manifest.json':
                    return json.dumps({'files': {}, 'counts': {}})
                return read_text(path, *args, **kwargs)

            def fixture_bytes(path):
                if path.parent == ROOT / 'build':
                    return b'fake binary: no execution'
                return read_bytes(path)

            with ExitStack() as stack:
                stack.enter_context(patch.object(module.suite, 'Runner', FakeRunner))
                stack.enter_context(patch.object(module.suite, 'monitor'))
                stack.enter_context(patch.dict(probe.LAYOUTS, clear=False))
                stack.enter_context(patch('sys.argv', ['scaling', '--out', str(out), *flags]))
                stack.enter_context(patch.object(Path, 'read_text', fixture_text))
                stack.enter_context(patch.object(Path, 'read_bytes', fixture_bytes))
                stack.enter_context(patch.object(module.shutil, 'disk_usage',
                                                 return_value=SimpleNamespace(free=100 * 2**30)))
                stack.enter_context(redirect_stdout(io.StringIO()))
                if failure:
                    with self.assertRaisesRegex(RuntimeError, f'simulated {failure} failure'):
                        module.main()
                else:
                    module.main()
            self.assertEqual((out / 'COMPLETE').exists(), failure is None)
            self.assertEqual((out / 'FAILED').exists(), failure is not None)
            self.assertGreater(calls['stops'], 0)
            if failure != 'trial':
                self.assertEqual(calls['measured'], expected_trials)

    def test_completion_requires_successful_trials_and_cleanup(self):
        for module, flags, trials in [(probe, ['--smoke'], 6),
                                      (confirmation, [], 4),
                                      (confirmation, ['--balance'], 6)]:
            for failure in [None, 'trial', 'cleanup']:
                with self.subTest(entry=module.__name__, flags=flags, failure=failure):
                    self.run_entry(module, flags, trials, failure)


if __name__ == '__main__':
    unittest.main()
