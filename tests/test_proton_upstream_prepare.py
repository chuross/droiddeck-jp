import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "overlay/proton-ntcontext"
spec = importlib.util.spec_from_file_location("prepare_graphics", ROOT / "prepare_upstream.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def patch(before, after):
    return "--- a/state.c\n+++ b/state.c\n@@ -1,2 +1,2 @@\n-" + before + "\n+" + after + "\n stable\n"


class UpstreamPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        for component in ("dxvk", "vkd3d-proton"):
            repo = self.source / component
            repo.mkdir()
            (repo / "state.c").write_text("one\nstable\ntail\n")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "base"], check=True)
            directory = self.source / "patches" / component
            directory.mkdir(parents=True)
            (directory / "first.patch").write_text(patch("one", "two"))
        (self.source / "patches/dxvk/second.patch").write_text(patch("two", "three"))
        disabled = self.source / "patches/vkd3d-proton/disabled"
        disabled.mkdir()
        (disabled / "never.patch").write_text("invalid disabled patch")
        self.script = self.source / "patches/protonprep-valve-staging.sh"
        self.script.write_text('''pushd dxvk
git reset --hard HEAD
git clean -xdf
patch -Np1 < ../patches/dxvk/first.patch
apply_patch "../patches/dxvk/second.patch"
popd
pushd vkd3d-proton
apply_all_in_dir "../patches/vkd3d-proton/"
popd
''')
        self.report = self.source / "upstream-patches.json"

    def prepare_custom(self):
        prep.prepare(self.source, self.report)
        custom = {}
        for component, before in (("dxvk", "three"), ("vkd3d-proton", "two")):
            path = self.source / (component + "-custom.patch")
            path.write_text(patch(before, "custom"))
            subprocess.run(["git", "-C", str(self.source / component), "apply", str(path)], check=True)
            custom[component] = path
        return custom

    def test_upstream_order_is_preserved_and_disabled_patches_are_excluded(self):
        custom = self.prepare_custom()
        record = prep.verify(self.source, self.report, custom)
        self.assertEqual(len(record["patches"]), 3)
        self.assertEqual(record["patches"][1]["path"], "patches/dxvk/second.patch")
        self.assertEqual((self.source / "dxvk/state.c").read_text(), "custom\nstable\ntail\n")

    def test_lost_upstream_changes_or_changed_patch_provenance_are_rejected(self):
        custom = self.prepare_custom()
        path = self.source / "dxvk/state.c"
        path.write_text("custom\nstable\nbroken-tail\n")
        with self.assertRaisesRegex(ValueError, "compatibility patch was lost"):
            prep.verify(self.source, self.report, custom)
        path.write_text("custom\nstable\ntail\n")
        (self.source / "patches/dxvk/first.patch").write_text(patch("one", "altered"))
        with self.assertRaisesRegex(ValueError, "provenance"):
            prep.verify(self.source, self.report, custom)

    def test_source_drift_and_unknown_preparation_commands_fail(self):
        (self.source / "dxvk/state.c").write_text("local change\n")
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            prep.prepare(self.source, self.report)
        self.script.write_text(self.script.read_text().replace("git clean -xdf", "unexpected command"))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            prep.plan(self.source)


if __name__ == "__main__":
    unittest.main()
