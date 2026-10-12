import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay/log-share"
spec = importlib.util.spec_from_file_location("log_share_apply", OVERLAY / "apply.py")
APPLY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APPLY)


@unittest.skipUnless(os.environ.get("DROIDDECK_PATCH_SOURCE"), "requires prepared upstream checkout")
class LogShareIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        upstream = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        for path in re.findall(r"^--- a/(.+)$", APPLY.PATCH.read_text(), re.MULTILINE):
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "-C", str(upstream), "show", "HEAD:" + path]))

    def install_recovery_hook(self):
        target = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionArtifacts.kt"
        target.write_text(target.read_text().replace("                val record = Record(dir)\n",
                          "                val record = Record(dir)\n                SuspendSurvival.recoverExit(context, dir)\n"))

    def test_preflight_and_application_after_suspend_overlay(self):
        APPLY.check(self.source)
        self.install_recovery_hook()
        APPLY.apply(self.source)
        for name, path in APPLY.FILES.items():
            self.assertEqual((self.source / path).read_bytes(), (OVERLAY / name).read_bytes())
        with self.assertRaises(RuntimeError):
            APPLY.apply(self.source)

    def test_source_drift_is_rejected_before_helpers_are_installed(self):
        target = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionLogShare.kt"
        target.write_text(target.read_text().replace("val scrubbed = SessionArtifacts.scrubbedFiles(folder)", "upstreamChanged()"))
        before = target.read_text()
        with self.assertRaisesRegex(RuntimeError, "integration changed"):
            APPLY.apply(self.source)
        self.assertEqual(before, target.read_text())
        self.assertFalse(any((self.source / path).exists() for path in APPLY.FILES.values()))
