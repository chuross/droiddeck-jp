import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
LITE = runpy.run_path(str(ROOT / 'overlay/low-memory/droiddeck-steam-ui-lite'))


class SteamUiLiteTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'requires Node for actual JavaScript execution')
    def test_session_flags_reduce_content_without_persisting_or_touching_other_settings(self):
        # Execute the shipped expression, including its startup guards and refresh
        # timer. Native persistence/GC/navigation calls are deliberately absent.
        code = r'''
const assert = require('node:assert/strict');
let callback, timers = 0;
global.window = {setInterval(fn, delay) {
    assert.equal(delay, 30000); timers++; callback = fn; return timers;
}};
const apply = () => eval(SCRIPT);
assert.equal(apply(), 'waiting');
const saved = {library_low_perf_mode: false, library_low_bandwidth_mode: false,
               library_disable_community_content: false, enable_ui_sounds: true};
window.settingsStore = {clientSettings: {...saved}, GetClientSetting() {
    throw Error('must not invoke persistent setting setters');
}};
assert.equal(apply(), 'active');
assert.equal(timers, 1);
assert.equal(apply(), 'active');
assert.equal(timers, 1);
for (const key of Object.keys(saved).slice(0, 3))
    assert.equal(window.settingsStore.clientSettings[key], true);
assert.equal(window.settingsStore.clientSettings.enable_ui_sounds, true);
assert.equal(saved.library_low_perf_mode, false);
window.settingsStore.clientSettings = {...saved};
callback();
assert.equal(window.settingsStore.clientSettings.library_low_perf_mode, true);
window.settingsStore.clientSettings = {library_low_perf_mode: false};
assert.equal(apply(), 'unsupported');
assert.equal(window.settingsStore.clientSettings.library_low_perf_mode, false);
// A new Steam process starts from the unchanged native preferences.
window = {settingsStore: {clientSettings: {...saved}, GetClientSetting() {}}};
assert.equal(window.settingsStore.clientSettings.library_low_perf_mode, false);
'''
        subprocess.run(['node', '-e', 'const SCRIPT = ' + json.dumps(LITE['SCRIPT']) + ';\n' + code],
                       check=True, capture_output=True, text=True)

    def test_install_retries_startup_then_finishes_without_resident_polling(self):
        evaluate = Mock(side_effect=[OSError('private'), {'value': 'waiting'}, {'value': 'active'}])
        log = Mock()
        with patch.object(LITE['time'], 'sleep') as sleep:
            self.assertTrue(LITE['install'](Path('/agent'), evaluate, log))
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(evaluate.call_count, 3)
        self.assertNotIn('private', str(log.call_args_list))

    @unittest.skipUnless(shutil.which('node'), 'requires Node')
    def test_small_mode_is_only_enabled_for_explicit_desktop_sessions(self):
        for small in (False, True):
            code = 'const assert = require("node:assert/strict");\n' + '''
global.window = {setInterval() { return 1; }, settingsStore: {
    GetClientSetting() {}, clientSettings: {library_low_perf_mode: false,
    library_low_bandwidth_mode: false, library_disable_community_content: false,
    small_mode: false}}};
'''
            code += 'assert.equal(eval(' + json.dumps(LITE['expression'](small)) + '), "active");\n'
            code += 'assert.equal(window.settingsStore.clientSettings.small_mode, ' + json.dumps(small) + ');'
            subprocess.run(['node', '-e', code], check=True, capture_output=True, text=True)

    def test_unsupported_store_does_not_retry_or_change_saved_settings(self):
        evaluate = Mock(return_value={'value': 'unsupported'})
        with patch.object(LITE['time'], 'sleep') as sleep:
            self.assertFalse(LITE['install'](Path('/agent'), evaluate, Mock()))
        sleep.assert_not_called()

    def test_timeout_is_bounded_and_has_no_account_data_in_logs(self):
        log = Mock()
        with patch.object(LITE['time'], 'monotonic', side_effect=[0, 100]):
            self.assertFalse(LITE['install'](Path('/agent'), Mock(), log, timeout=90))
        self.assertIn('not ready', log.call_args.args[0])


@unittest.skipUnless(os.environ.get('DROIDDECK_PATCH_SOURCE'), 'requires prepared source')
class SteamUiLiteIntegrationTest(unittest.TestCase):
    def test_low_memory_installs_ui_policy_even_when_cache_reclamation_is_disabled(self):
        source = Path(os.environ['DROIDDECK_PATCH_SOURCE'])
        agent = runpy.run_path(str(source / 'tools/linuxfs/overlay/usr/local/bin/droiddeck-agent'))
        class Finished(BaseException):
            pass
        with tempfile.TemporaryDirectory() as directory:
            for low, display, expected in (('1', ':0', True), ('0', ':0', False), ('1', '', False)):
                start = Mock()
                with patch.dict(os.environ, DISPLAY=display, DROIDDECK_SESSION_LOW_MEMORY=low,
                                DROIDDECK_SESSION_STEAM_TRIM='0'), \
                     patch.dict(agent['serve'].__globals__, FocusWatcher=Mock(), write_json=Mock(),
                                displays=Mock(return_value=[]), log=Mock()), \
                     patch.object(agent['runpy'], 'run_path', return_value={'start': start}), \
                     patch.object(agent['time'], 'sleep', side_effect=Finished):
                    with self.assertRaises(Finished):
                        agent['serve'](Path(directory))
                self.assertEqual(start.called, expected)
                if expected:
                    self.assertIs(start.call_args.args[1], agent['cdp_eval'])


if __name__ == '__main__':
    unittest.main()
