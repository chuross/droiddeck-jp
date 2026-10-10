import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay/steam-diagnostics"


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(file))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


diag = load("steam_diag_overlay", OVERLAY / "apply.py")
helper = load("steam_diag_check", OVERLAY / "droiddeck-steam-diag-check")


class SteamDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def dll(self, markers=True):
        data = bytearray(256)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 128)
        data[128:132] = b"PE\0\0"
        struct.pack_into("<H", data, 132, 0xa641)
        struct.pack_into("<H", data, 150, 0x2000)
        if markers:
            data.extend(b"CALLDIAG:\0CBDIAG:\0")
        target = self.root / "files/lib/wine/aarch64-windows/lsteamclient.dll"
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        (self.root / "version").write_text("test-version\n")
        return target

    def test_standard_runtime_is_accepted_without_custom_markers(self):
        self.dll(markers=False)
        result = subprocess.run(["python3", str(OVERLAY / "droiddeck-steam-diag-check"), str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("CALLDIAG", result.stdout)

    def test_missing_runtime_is_reported(self):
        result = subprocess.run(["python3", str(OVERLAY / "droiddeck-steam-diag-check"), str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)

    def test_hash_identifies_modified_runtime_not_just_its_name(self):
        target = self.dll()
        before = helper.inspect(self.root)
        target.write_bytes(target.read_bytes() + b"changed")
        after = helper.inspect(self.root)
        self.assertNotEqual(before["sha256"], after["sha256"])
        self.assertEqual(after["version"], "test-version")

    def test_entry_point_symlink_keeps_selected_tree(self):
        self.dll()
        other = self.root / "outside"
        other.mkdir()
        entry = other / "proton"
        entry.write_text("upstream entry point")
        (self.root / "proton").symlink_to(entry)
        self.assertEqual(helper.inspect(self.root / "proton")["proton"], str(self.root))

    def test_changed_hook_fails_without_mutating_source(self):
        script = diag.script(self.root)
        script.parent.mkdir(parents=True)
        text = "def main():\n    upstream_changed()\n"
        script.write_text(text)
        with self.assertRaises(RuntimeError):
            diag.apply(self.root)
        self.assertEqual(script.read_text(), text)

    def test_launch_provenance_never_changes_launch_environment(self):
        script = diag.script(self.root)
        script.parent.mkdir(parents=True)
        # Executable launch/fallback fixture, with exactly the upstream hooks.
        script.write_text('''import os, sys, subprocess
def main():
    launch, launch_env = command, env
    if fallback:
        try:
            os.execvpe(launch[0], launch, launch_env)
        except OSError:
            launch, launch_env = stock, env
    os.execvpe(launch[0], launch, launch_env)
''')
        diag.apply(self.root)
        namespace = {}
        exec(compile(script.read_text(), str(script), "exec"), namespace)
        from unittest.mock import patch
        for enabled, fallback in ((False, False), (True, False), (True, True)):
            environment = {"PROTON_LOG": "1" if enabled else "0", "PROTON_NO_ESYNC": "1"}
            seen = []

            def launch(path, argv, env):
                seen.append((path, argv[:], dict(env)))
                if len(seen) == 1 and fallback:
                    raise OSError("primary failed")

            namespace.update(command=["/selected/proton", "waitforexitandrun", "game.exe"],
                             stock=["/stock/proton", "waitforexitandrun", "game.exe"],
                             env=environment, fallback=fallback)
            with patch.object(namespace["os"], "execvpe", side_effect=launch), \
                 patch.object(namespace["subprocess"], "run", side_effect=OSError("helper unavailable")) as report:
                namespace["main"]()
            self.assertEqual(report.call_count, (2 if fallback else 1) if enabled else 0)
            self.assertTrue(all(row[2] == environment for row in seen))
            self.assertEqual(seen[-1][0], "/stock/proton" if fallback else "/selected/proton")


if __name__ == "__main__":
    unittest.main()
