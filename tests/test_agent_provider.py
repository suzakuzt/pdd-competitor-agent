"""Provider choice must preserve explicit configuration and local query scope."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor.agent_provider import configured_planner, UnavailablePlanner
from pdd_monitor.agent_codex import CodexPlanner
from pdd_monitor.agent_credentials import set_provider_config
from pdd_monitor.agent_deepseek import DeepSeekPlanner
from pdd_monitor.agent_service import AgentService


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def test_missing_config_keeps_original_codex(self):
        with patch('pdd_monitor.agent_provider.get_api_key', side_effect=AssertionError('not selected')):
            self.assertIsInstance(configured_planner(self.project), CodexPlanner)

    def test_deepseek_is_explicit_and_reads_secret_only_when_needed(self):
        set_provider_config(self.project, 'deepseek', 'deepseek-flash')
        with patch('pdd_monitor.agent_provider.get_api_key', return_value=None) as read:
            planner = configured_planner(self.project)
            self.assertIsInstance(planner, DeepSeekPlanner)
            read.assert_not_called()
            status = planner.status()
            self.assertEqual(status['status'], 'configuration_required')
            self.assertEqual(status['provider'], 'deepseek')

    def test_bad_config_does_not_fallback_or_expose_content(self):
        (self.project/'state').mkdir()
        (self.project/'state/agent_runtime.json').write_text('{"secret":"DO_NOT_ECHO"}', encoding='utf-8')
        planner = configured_planner(self.project)
        self.assertIsInstance(planner, UnavailablePlanner)
        self.assertEqual(planner.status()['status'], 'unavailable')
        self.assertNotIn('DO_NOT_ECHO', str(planner.status()))

    def test_factory_used_by_service(self):
        marker = object()
        with patch('pdd_monitor.agent_service.configured_planner', return_value=marker) as choose:
            self.assertIs(AgentService(self.project).planner, marker)
            choose.assert_called_once_with(self.project)

    def test_injected_planner_unchanged(self):
        marker = object()
        with patch('pdd_monitor.agent_service.configured_planner', side_effect=AssertionError('unused')):
            self.assertIs(AgentService(self.project, planner=marker).planner, marker)

    def test_status_has_no_network_call(self):
        set_provider_config(self.project, 'deepseek', 'deepseek-flash')
        with patch('pdd_monitor.agent_provider.get_api_key', return_value='sk-synthetic-test-value'), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('no network')):
            status = AgentService(self.project).status()
        self.assertEqual(status['provider'], 'deepseek')
        self.assertEqual(status['status'], 'ready')
        self.assertFalse(status['website_collection_performed'])


if __name__ == '__main__':
    unittest.main()
