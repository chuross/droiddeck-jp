import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


feature = load(HERE / 'apply.py', 'background_apply')
prepare = load(ROOT / 'tools/prepare.py', 'background_prepare')


from test_overlays import fixture_from_patch


class BackgroundOverlayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        fixture_from_patch(feature.PATCH, self.source)
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)

    def test_apply_owns_exactly_manifest_and_removal_recreates_baseline(self):
        baseline = {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file() and '.git' not in p.parts}
        feature.apply(self.source)
        changed = {p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file() and '.git' not in p.parts
                   and baseline.get(p.relative_to(self.source).as_posix()) != p.read_bytes()}
        self.assertEqual(changed, set(feature.OWNED_PATHS))
        # Re-preparing without the registration never modifies or creates any owned file.
        fresh = self.source / 'without'; fresh.mkdir()
        for name, data in baseline.items():
            path = fresh / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        self.assertNotIn('background-memory', {name: mod for name, mod in prepare.PATCHES.items() if name != 'background-memory'})
        for name, data in baseline.items(): self.assertEqual((fresh / name).read_bytes(), data)
        for name in feature.FILES.values(): self.assertFalse((fresh / name).exists())

    def test_drift_and_partial_match_fail_before_install(self):
        main = self.source / feature.BASE / 'MainActivity.kt'
        main.write_text(main.read_text().replace('super.onCreate(savedInstanceState)', 'changed()'))
        with self.assertRaisesRegex(RuntimeError, 'drift'): feature.apply(self.source)
        for name in feature.FILES.values(): self.assertFalse((self.source / name).exists())
        policy = self.source / feature.BASE / 'core/BackgroundAppLimit.kt'
        policy.write_text('max_cached_processes')
        with self.assertRaisesRegex(RuntimeError, 'partial'): feature.check(self.source)

    def test_equivalent_reference_upstream_is_explicitly_unnecessary(self):
        files = {'core/BackgroundAppLimit.kt':'max_cached_processes previous commit() restoreAsync applyAsync device_config delete',
                 'session/SessionService.kt':'BackgroundAppLimit.applyAsync BackgroundAppLimit.restoreAsync clearCachedApps',
                 'MainActivity.kt':'!SessionState.running BackgroundAppLimit.restoreAsync',
                 'ui/FrontEndSetup.kt':'backgroundAppLimit clearCachedApps adbPaired',
                 'core/WirelessAdbFix.kt':'am kill-all hasSavedPairing',
                 'session/SessionPrefs.kt':'backgroundAppLimit clearCachedApps'}
        for name, text in files.items():
            p = self.source / feature.BASE / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        with patch('builtins.print') as output:
            self.assertFalse(feature.check(self.source))
            self.assertIn('overlay unnecessary', output.call_args.args[0])

    def test_recovery_hook_and_policy_are_owned_and_defaults_off(self):
        feature.apply(self.source)
        policy = (HERE / 'BackgroundAppLimit.kt').read_text()
        self.assertIn('if (!SessionState.running) policy(it).restore()', policy)
        self.assertIn('getInt("limit", OFF)', policy)
        self.assertIn('getBoolean("clear", false)', policy)
        self.assertIn('if (paired && clearCached(app)) WirelessAdbFix.shell(app, "am kill-all")', policy)
        self.assertIn('check(journal.save(previous))', policy)
        self.assertNotIn('SessionPrefs.', policy)
        service = (self.source / feature.BASE / 'session/SessionService.kt').read_text()
        self.assertIn('memoryPreparation.get()', service)
        self.assertIn('if (gen == sessionGen && SessionState.running) runSession(gen)', service)
        adb = (self.source / feature.BASE / 'core/WirelessAdbFix.kt').read_text()
        self.assertNotIn('max_cached_processes', adb)
        self.assertNotIn('BackgroundAppLimit', adb)
        self.assertIn('setChildProcessLimit(app, host, port, enabled)', adb)
        self.assertNotIn('PhantomProcessLimit.verifyCommand', feature.PATCH.read_text())

    def test_feature_has_no_turnip_or_other_overlay_dependency(self):
        for path in HERE.glob('*'):
            if path.is_file() and path.name != Path(__file__).name:
                self.assertNotIn('turnip-process-heap-usage', path.read_text())
                self.assertNotIn('overlay/proton', path.read_text())
