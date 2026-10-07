"""Publication ordering and failure preservation, only in synthetic projects."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor.dashboard_runtime import build_dashboard
# Load dependency bindings before patching their defining modules. Otherwise a
# first import of trend_dashboard would permanently bind the temporary mocks in
# its `from ... import ...` statements and contaminate later snapshot tests.
from pdd_monitor import dashboard_data, trend_decisions, trend_backtest, trend_dashboard


class DashboardBuildPublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_build_synthetic_')
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.snapshot = self.project / 'dashboard/src/data.json'
        for name in ('dashboard/src/content/dashboard/DashboardContent.jsx',
                     'scripts/export_history.mjs', 'plugin/scripts/data-app.mjs'):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// SYNTHETIC', encoding='utf-8')
        self.original = b'{"id":"SYNTHETIC","queries":{"old":{"rows":[]}}}\n'
        self.snapshot.write_bytes(self.original)
        self.payload = {'id': 'SYNTHETIC', 'metadata': {'default_run_id': 'synthetic-run'},
                        'queries': {'observations': {'rows': [{'observation_id': 1}]},
                                    'trend_signals': {'rows': []}}}

    def run_build(self, *, failed_step=None, bad_number=False):
        commands = []

        def build_source(*args, **kwargs):
            self.assertEqual(self.snapshot.read_bytes(), self.original)
            self.assertFalse(kwargs['write_output'])
            self.assertEqual(kwargs['image_delivery'], 'local_http')
            if bad_number:
                self.payload['invalid'] = float('nan')
            return self.payload

        def freeze(project, queries):
            self.assertEqual(self.snapshot.read_bytes(), self.original)
            return {'revision': 7}

        def run(arguments, **kwargs):
            commands.append(arguments)
            published = json.loads(self.snapshot.read_text(encoding='utf-8'))
            self.assertEqual(published['metadata']['trend_decision_ledger_revision'], 7)
            self.assertEqual(published['buildStatus'], 'complete')
            self.assertEqual(published['queries']['decision-evidence']['rows'], [7])
            if failed_step == len(commands):
                return subprocess.CompletedProcess(arguments, 1, '', 'synthetic failure')
            (self.project / 'dashboard/dist').mkdir(exist_ok=True)
            return subprocess.CompletedProcess(arguments, 0, '{"status":"exported"}', '')

        with patch.dict('os.environ', {'PDD_DATA_PLUGIN': str(self.project / 'plugin')}), \
                patch('pdd_monitor.dashboard_runtime._node', return_value='synthetic-node'), \
                patch.object(dashboard_data, 'build_dashboard_snapshot', side_effect=build_source), \
                patch.object(trend_decisions, 'record_decisions', side_effect=freeze), \
                patch.object(trend_decisions, 'build_decision_queries', return_value={'decision-evidence': {'rows': [7]}}), \
                patch.object(trend_backtest, 'build_backtest_queries', return_value={'backtest-evidence': {'rows': []}}), \
                patch.object(trend_dashboard, 'trend_status_manifest', return_value={'synthetic': True}), \
                patch('pdd_monitor.dashboard_runtime.subprocess.run', side_effect=run):
            result = build_dashboard(self.project)
        return result, commands

    def test_decisions_are_in_the_only_published_snapshot_before_canonical_build(self):
        result, commands = self.run_build()
        self.assertEqual(result['status'], 'built')
        self.assertEqual(len(commands), 3)
        self.assertIn('--check-only', commands[0])
        self.assertIn('--separate-data', commands[1])
        self.assertEqual(commands[2][-2:], ['--project-root', str(self.project)])
        self.assertEqual(result['observation_count'], 1)
        self.assertGreaterEqual(result['timings']['total_seconds'], result['timings']['snapshot_seconds'])
        self.assertTrue((self.project / 'dashboard/dist/trend-status.json').is_file())

    def test_history_preflight_conflict_and_builder_failure_restore_previous_bytes(self):
        for failed_step in (1, 2, 3):
            with self.subTest(failed_step=failed_step), self.assertRaisesRegex(ValueError, 'failed'):
                self.run_build(failed_step=failed_step)
            self.assertEqual(self.snapshot.read_bytes(), self.original)

    def test_nonfinite_payload_is_rejected_before_any_snapshot_replacement(self):
        with self.assertRaises(ValueError):
            self.run_build(bad_number=True)
        self.assertEqual(self.snapshot.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
