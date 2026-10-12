import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("jp_fonts", str(ROOT / "overlay/japanese-fonts/droiddeck-patch-fonts"))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class FontsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.font = self.root / "NotoSansCJK-Regular.ttc"
        self.font.write_bytes(b"fixture font")
        self.data = self.root / "compatdata/123"
        self.prefix = self.data / "pfx"
        self.env = {"STEAM_COMPAT_DATA_PATH": str(self.data), "EXAMPLE": "retained",
                    "DROIDDECK_PATCH_FONT_FAMILY": helper.FAMILY}
        self.command = ["/selected/Proton/proton", "waitforexitandrun", "game.exe"]
        self.calls = []
        self.registry = None

    def run_proton(self, proton, arguments, env):
        self.calls.append((proton, arguments, env.copy()))
        self.assertEqual(proton, self.command[0])
        self.assertEqual(env, self.env)
        if arguments[0] == "cmd.exe":
            self.prefix.mkdir(parents=True, exist_ok=True)
            (self.prefix / "user.reg").write_text("existing registry contents")
        else:
            filename = arguments[2][2:].replace("\\", "/")
            self.registry = Path(filename).read_bytes().decode("utf-16")

    def prepare(self):
        with patch.object(helper, "find_font", return_value=self.font), \
             patch.object(helper, "proton_run", side_effect=self.run_proton):
            return helper.prepare(self.command, self.env)

    def test_first_launch_initializes_then_imports_and_second_launch_skips(self):
        self.assertTrue(self.prepare())
        self.assertEqual([call[1][0] for call in self.calls], ["cmd.exe", "reg.exe"])
        self.assertIn('"MS Gothic"="Noto Sans CJK JP"', self.registry)
        self.assertIn('"ＭＳ ゴシック"="Noto Sans CJK JP"', self.registry)
        self.assertIn('"Arial"=hex(7):', self.registry)
        placed = self.prefix / "drive_c/windows/Fonts/droiddeck-patch.ttc"
        self.assertFalse(placed.is_symlink())
        self.assertEqual(placed.read_bytes(), self.font.read_bytes())
        self.assertEqual((self.prefix / "user.reg").read_text(), "existing registry contents")
        self.calls.clear()
        self.assertTrue(self.prepare())
        self.assertEqual(self.calls, [])

    def test_existing_prefix_and_changed_font_are_applied(self):
        self.prefix.mkdir(parents=True)
        (self.prefix / "user.reg").write_text("untouched")
        self.prepare()
        self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])
        self.calls.clear()
        self.font.write_bytes(b"updated font contents")
        self.prepare()
        self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])
        self.assertEqual((self.prefix / "user.reg").read_text(), "untouched")

    def test_import_failure_has_no_success_marker_and_retries(self):
        self.prefix.mkdir(parents=True)
        (self.prefix / "user.reg").write_text("untouched")
        with patch.object(helper, "find_font", return_value=self.font), \
             patch.object(helper, "proton_run", side_effect=RuntimeError("import failed")):
            with self.assertRaises(RuntimeError):
                helper.prepare(self.command, self.env)
        self.assertFalse((self.prefix / ".droiddeck-patch-fonts-v1").exists())
        self.assertTrue(self.prepare())

    def test_probes_nonlaunch_verbs_and_opt_out_never_touch_prefix(self):
        with patch.object(helper, "find_font") as lookup:
            for value in ("0", "0-probe"):
                env = dict(self.env, STEAM_COMPAT_DATA_PATH=str(self.root / "compatdata" / value))
                self.assertFalse(helper.prepare(self.command, env))
            self.assertFalse(helper.prepare(["proton", "getcompatpath", "game.exe"], self.env))
            self.assertFalse(helper.prepare(self.command, dict(self.env, DROIDDECK_PATCH_FONTS="0")))
            self.assertFalse(helper.prepare(self.command, {}))
            self.assertFalse(helper.prepare(self.command, dict(self.env, STEAM_COMPAT_DATA_PATH="relative")))
            lookup.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_font_missing_leaves_prefix_unchanged(self):
        with patch.object(helper, "find_font", side_effect=RuntimeError("no CJK font")):
            with self.assertRaises(RuntimeError):
                helper.prepare(self.command, self.env)
        self.assertFalse(self.data.exists())

    def test_latin_fallback_is_rejected(self):
        result = type("Result", (), {"stdout": str(self.font) + "\nDejaVu Sans\n"})()
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                helper.find_font(self.env)

    def test_user_font_file_is_not_overwritten(self):
        fonts = self.prefix / "drive_c/windows/Fonts"
        fonts.mkdir(parents=True)
        (self.prefix / "user.reg").write_text("untouched")
        own = fonts / "droiddeck-patch.ttc"
        own.write_bytes(b"user font")
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertEqual(own.read_bytes(), b"user font")

    def test_switch_family_updates_existing_prefix_and_can_return_to_noto(self):
        self.assertTrue(self.prepare())
        self.calls.clear()
        self.env["DROIDDECK_PATCH_FONT_FAMILY"] = "IPAGothic"
        self.assertTrue(self.prepare())
        self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])
        self.assertIn('"MS Gothic"="IPAGothic"', self.registry)
        self.assertIn('"IPAGothic (TrueType)"=', self.registry)
        original = self.prefix / "drive_c/windows/Fonts/droiddeck-patch.ttc"
        self.assertEqual(original.read_bytes(), self.font.read_bytes())
        self.calls.clear()
        self.assertTrue(self.prepare())
        self.assertEqual(self.calls, [])
        self.env["DROIDDECK_PATCH_FONT_FAMILY"] = helper.FAMILY
        self.assertTrue(self.prepare())
        self.assertIn('"MS Gothic"="Noto Sans CJK JP"', self.registry)

    def test_family_change_retries_after_failed_import(self):
        self.prepare()
        self.env["DROIDDECK_PATCH_FONT_FAMILY"] = "IPAGothic"
        with patch.object(helper, "find_font", return_value=self.font), \
             patch.object(helper, "proton_run", side_effect=RuntimeError("import failed")):
            with self.assertRaises(RuntimeError):
                helper.prepare(self.command, self.env)
        self.calls.clear()
        self.assertTrue(self.prepare())
        self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])

    def test_custom_file_is_scanned_and_family_verified(self):
        env = dict(self.env, DROIDDECK_PATCH_FONT_FILE=str(self.font),
                   DROIDDECK_PATCH_FONT_FAMILY="IPAGothic")
        result = type("Result", (), {"stdout": str(self.font) + "\nIPAGothic\n"})()
        with patch.object(helper.subprocess, "run", return_value=result) as run:
            self.assertEqual(helper.find_font(env), self.font)
            self.assertEqual(run.call_args.args[0][0], "fc-scan")
        result.stdout = str(self.font) + "\nWrong Family\n"
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                helper.find_font(env)

    def test_invalid_custom_file_does_not_touch_prefix(self):
        for value in ("relative.ttf", str(self.root / "missing.ttf")):
            with self.assertRaises(RuntimeError):
                helper.prepare(self.command, dict(self.env, DROIDDECK_PATCH_FONT_FILE=value))
        self.assertFalse(self.data.exists())

    def test_requested_family_does_not_accept_noto_fallback(self):
        result = type("Result", (), {"stdout": str(self.font) + "\nNoto Sans CJK JP\n"})()
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                helper.find_font(dict(self.env, DROIDDECK_PATCH_FONT_FAMILY="IPAGothic"))

    def test_default_installs_five_fonts_and_migrates_existing_noto_prefix(self):
        self.prepare()
        self.calls.clear()
        del self.env["DROIDDECK_PATCH_FONT_FAMILY"]
        bundled = self.root / "bundled"
        bundled.mkdir()
        for filename in helper.COMPAT_FONTS.values():
            (bundled / filename).write_bytes(filename.encode())
        with patch.object(helper, "BUNDLED_DIR", bundled):
            self.assertTrue(self.prepare())
            self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])
            for alias, family in helper.COMPAT_ALIASES.items():
                self.assertIn(helper.reg_string(alias) + "=" + helper.reg_string(family), self.registry)
            self.assertIn('"Meiryo"="Noto Sans CJK JP"', self.registry)
            for filename in helper.COMPAT_FONTS.values():
                self.assertEqual((self.prefix / "drive_c/windows/Fonts" / ("droiddeck-patch-" + filename)).read_bytes(), filename.encode())
            self.calls.clear()
            self.assertTrue(self.prepare())
            self.assertEqual(self.calls, [])
            (bundled / "ipam-mona.ttf").write_bytes(b"changed mincho")
            self.assertTrue(self.prepare())
            self.assertEqual([call[1][0] for call in self.calls], ["reg.exe"])

    def test_missing_bundled_font_does_not_modify_prefix(self):
        del self.env["DROIDDECK_PATCH_FONT_FAMILY"]
        with patch.object(helper, "BUNDLED_DIR", self.root / "missing"):
            with self.assertRaises(RuntimeError):
                self.prepare()
        self.assertFalse(self.data.exists())

    def test_default_still_works_without_android_noto(self):
        del self.env["DROIDDECK_PATCH_FONT_FAMILY"]
        bundled = self.root / "bundled"
        bundled.mkdir()
        for filename in helper.COMPAT_FONTS.values():
            (bundled / filename).write_bytes(filename.encode())
        with patch.object(helper, "BUNDLED_DIR", bundled), \
             patch.object(helper, "find_font", side_effect=RuntimeError("no Noto")), \
             patch.object(helper, "proton_run", side_effect=self.run_proton):
            self.assertTrue(helper.prepare(self.command, self.env))
        self.assertIn('"MS Gothic"="IPAMonaGothic"', self.registry)
        self.assertIn('"MS UI Gothic"="IPAMonaUIGothic"', self.registry)
        self.assertIn('"Meiryo"="IPAMonaGothic"', self.registry)


if __name__ == "__main__":
    unittest.main()


