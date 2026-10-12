import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay/suspend-survival"
spec = importlib.util.spec_from_file_location("suspend_survival_apply", OVERLAY / "apply.py")
APPLY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APPLY)


@unittest.skipUnless(os.environ.get("DROIDDECK_PATCH_SOURCE"), "requires prepared upstream checkout")
class SuspendSurvivalIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        upstream = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        # The prepared tree has all overlays, but HEAD is the original pinned upstream.
        paths = re.findall(r"^--- a/(.+)$", APPLY.PATCH.read_text(), re.MULTILINE)
        for path in paths:
            data = subprocess.run(["git", "-C", str(upstream), "show", "HEAD:" + path],
                                  check=True, capture_output=True).stdout
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def test_pristine_upstream_applies_and_second_application_is_rejected(self):
        APPLY.check(self.source)
        APPLY.apply(self.source)
        for name, path in APPLY.FILES.items():
            self.assertEqual((self.source / path).read_bytes(), (OVERLAY / name).read_bytes())
        with self.assertRaisesRegex(RuntimeError, "integration changed"):
            APPLY.apply(self.source)

    def test_upstream_drift_fails_before_any_helper_is_installed(self):
        target = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionService.kt"
        original = target.read_text().replace("private fun releaseLocks()", "private fun upstreamChangedLocks()")
        target.write_text(original)
        with self.assertRaisesRegex(RuntimeError, "integration changed"):
            APPLY.apply(self.source)
        self.assertEqual(original, target.read_text())
        for path in APPLY.FILES.values():
            self.assertFalse((self.source / path).exists())

    def test_applies_after_low_memory_without_overwriting_it(self):
        low_patch = ROOT / "overlay/low-memory/low-memory.patch"
        upstream = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        for path in re.findall(r"^--- a/(.+)$", low_patch.read_text(), re.MULTILINE):
            target = self.source / path
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.run(["git", "-C", str(upstream), "show", "HEAD:" + path],
                                                 check=True, capture_output=True).stdout)
        subprocess.run(["git", "-C", str(self.source), "apply", str(low_patch)], check=True)
        APPLY.apply(self.source)
        prefs = (self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionPrefs.kt").read_text()
        self.assertIn("fun lowMemoryEnabled", prefs)
        self.assertIn("fun keepCpuAwakeWhileSuspended", prefs)
        ui = (self.source / "app/src/main/java/com/droiddeck/launcher/ui/ModeSettingsDialog.kt").read_text()
        self.assertIn("LowMemoryRow()", ui)
        self.assertIn("SuspendSurvivalRow()", ui)


if __name__ == "__main__":
    unittest.main()
