"""Exercise trial qualification and saved records without a database or Linux host."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from run_suite import Runner
from trial_health import TrialHealth, qualify, shared_policy


def sample(phase, load=1, free=100*2**30):
    return dict(time='2026-10-14T00:00:00+00:00', phase=phase, load1=load, disk_free=free)


class HealthTests(unittest.TestCase):
    def test_shared_host_policy_is_explicit_and_keeps_resource_checks(self):
        policy = shared_policy('User authorized shared-host measurements', [9, 73])
        observations = [dict(sample(phase, load=100), available_memory=100*2**30,
                             cpu_ticks={'9': [1]*8, '73': [1]*8}) for phase in ['start', 'finish']]
        self.assertFalse(qualify(observations)['performance_eligible'])
        self.assertTrue(qualify(observations, policy=policy)['performance_eligible'])
        self.assertFalse(qualify(observations, True, policy)['performance_eligible'])
        observations[-1]['available_memory'] = 8*2**30
        self.assertFalse(qualify(observations, policy=policy)['performance_eligible'])
        observations[-1]['available_memory'] = 100*2**30
        observations[-1]['cpu_ticks'].pop('73')
        self.assertFalse(qualify(observations, policy=policy)['performance_eligible'])
        with self.assertRaises(ValueError):
            shared_policy('', [9])
        with self.assertRaises(ValueError):
            qualify([], policy=dict(policy, max_load1=999))

    def test_boundaries_and_midtrial_spike(self):
        healthy = [sample('start'), sample('finish')]
        self.assertTrue(qualify(healthy)['performance_eligible'])
        self.assertFalse(qualify(healthy, True)['performance_eligible'])
        self.assertFalse(qualify([sample('start')])['performance_eligible'])
        for bad in [sample('during', load=33), sample('during', free=40*2**30),
                    sample('during', load=float('nan')), {'phase': 'during', 'error': 'I/O'}]:
            with self.subTest(sample=bad):
                self.assertFalse(qualify([healthy[0], bad, healthy[1]])['performance_eligible'])

    def test_guard_samples_during_run_and_on_error(self):
        guard = TrialHealth()
        with patch('trial_health.sample_host', side_effect=lambda phase: sample(phase)), \
             patch.object(guard.stop, 'wait', side_effect=[False, True]):
            with self.assertRaisesRegex(RuntimeError, 'workload failed'):
                with guard:
                    guard.thread.join()
                    raise RuntimeError('workload failed')
        self.assertEqual([s['phase'] for s in guard.samples], ['start', 'during', 'finish'])

    def test_runner_keeps_output_but_excludes_unhealthy_trial(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner.__new__(Runner)
            runner.out = Path(temp)
            runner.args = SimpleNamespace(timeout=10)
            runner.counter, runner.config = 0, {}
            runner.command = lambda *a: [sys.executable, '-c', 'print("{\\"rows\\": 1}")']
            with patch('run_suite.monitor'), \
                 patch('trial_health.sample_host', side_effect=lambda phase: sample(phase, load=50)):
                result = runner.call('file', 1, 'point', measured=True, health_check=TrialHealth())
            self.assertEqual(result, {'rows': 1})
            record = json.loads((runner.out/'records.jsonl').read_text())
            self.assertFalse(record['measured'])
            self.assertIn('host load exceeded 32', record['health']['exclusion_reasons'])
            self.assertTrue((runner.out/(record['log']+'.stdout')).is_file())


if __name__ == '__main__':
    unittest.main()
