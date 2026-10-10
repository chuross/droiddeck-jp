import runpy
from pathlib import Path
import tempfile
import unittest

analyze = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'overlay/steam-diagnostics/droiddeck-log-analyze'))['analyze']


class LogAnalysisTest(unittest.TestCase):
    def report(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'steam-123.log'
            path.write_text(text)
            return analyze(path)

    def test_observed_errors_have_bounded_line_evidence(self):
        result = self.report('Proton started\n' + 'err:module:import_dll missing.dll\n' * 10 + 'SteamAPI_Init failed\nVK_ERROR_DEVICE_LOST\n')
        self.assertEqual(result['counts']['missing_library'], 10)
        self.assertEqual(len(result['evidence']['missing_library']), 5)
        self.assertEqual(result['evidence']['missing_library'][0]['line'], 2)
        self.assertEqual(result['counts']['steam_client'], 1)
        self.assertEqual(result['counts']['graphics'], 1)

    def test_empty_or_quiet_log_does_not_claim_success_or_callback_health(self):
        for text in ('', 'Steam connected\n'):
            result = self.report(text)
            self.assertFalse(any(result['counts'].values()))
            self.assertIn('success is not established', result['assessment'])
            self.assertIn('unknown', result['callback_timing'])
