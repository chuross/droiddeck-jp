import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay/quick-share"
spec = importlib.util.spec_from_file_location("quick_share_apply", OVERLAY / "apply.py")
APPLY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APPLY)


@unittest.skipUnless(os.environ.get("DROIDDECK_PATCH_SOURCE"), "requires upstream checkout")
class QuickShareIntegrationTest(unittest.TestCase):
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

    def test_application_and_duplicate_rejection(self):
        APPLY.check(self.source)
        APPLY.apply(self.source)
        for name, path in APPLY.FILES.items():
            self.assertEqual((self.source / path).read_bytes(), (OVERLAY / name).read_bytes())
        for name in ("FrontEndSetup.kt", "SessionOverlay.kt", "SessionLoading.kt"):
            text = (self.source / "app/src/main/java/com/droiddeck/launcher/ui" / name).read_text()
            self.assertIn("QuickShareLogButton(", text)
            self.assertIn("SessionLogShare.progress", text)
        with self.assertRaises(RuntimeError):
            APPLY.apply(self.source)

    def test_drift_rejected_before_mutation(self):
        target = self.source / "app/src/main/java/com/droiddeck/launcher/ui/FrontEndSetup.kt"
        target.write_text(target.read_text().replace("a.onShareLogs,", "a.changedShareLogs,"))
        before = target.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "integration changed"):
            APPLY.apply(self.source)
        self.assertEqual(before, target.read_bytes())
        self.assertFalse(any((self.source / path).exists() for path in APPLY.FILES.values()))
