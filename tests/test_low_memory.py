import importlib.util
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay/low-memory"
LOW = runpy.run_path(str(OVERLAY / "droiddeck-low-memory"))
PROFILE = runpy.run_path(str(OVERLAY / "droiddeck-memory-profiler"))
spec = importlib.util.spec_from_file_location("low_memory_apply", OVERLAY / "apply.py")
APPLY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APPLY)


class LowMemoryTest(unittest.TestCase):
    def test_default_and_explicit_disabled_are_unchanged(self):
        for value in (None, "0", "false"):
            env = {"DXVK_CONFIG": "existing", "LD_PRELOAD": "gameoverlayrenderer.so"}
            if value is not None:
                env[LOW["MODE"]] = value
            self.assertIs(LOW["game_environment"](env), env)

    def test_overlay_removal_preserves_other_libraries_and_overrides(self):
        original = {LOW["MODE"]: "1", "LD_PRELOAD": "/steam/gameoverlayrenderer.so:/usr/lib/keep.so /usr/lib/libMangoHud.so",
                    "WINEDLLOVERRIDES": "foo=b", "DXVK_HUD": "fps", "MANGOHUD": "1",
                    "VK_INSTANCE_LAYERS": "VK_LAYER_MANGOHUD_overlay:VK_LAYER_keep"}
        result = LOW["game_environment"](original)
        self.assertEqual(result["LD_PRELOAD"], "/usr/lib/keep.so")
        self.assertEqual(result["VK_INSTANCE_LAYERS"], "VK_LAYER_keep")
        self.assertEqual(result["WINEDLLOVERRIDES"], "foo=b;gameoverlayrenderer,gameoverlayrenderer64=d")
        self.assertEqual(result["FEX_DYNAMICL1CACHE"], "1")
        self.assertEqual(result["FEX_DISABLEL2CACHE"], "1")
        self.assertEqual(result["MANGOHUD"], "0")
        self.assertEqual(result["DXVK_HUD"], "0")
        self.assertEqual(original["MANGOHUD"], "1")

    def test_gpu_report_is_separate_opt_in_and_preserves_dxvk_options(self):
        env = {LOW["MODE"]: "1", "DXVK_CONFIG": "dxvk.maxFrameRate = 60;"}
        self.assertEqual(LOW["game_environment"](env)["DXVK_CONFIG"], env["DXVK_CONFIG"])
        result = LOW["game_environment"](dict(env, DROIDDECK_GPU_MEMORY_MB="2048"))
        self.assertEqual(result["DXVK_CONFIG"], "dxvk.maxFrameRate = 60; dxgi.maxDeviceMemory = 2048; dxgi.maxSharedMemory = 2048")
        self.assertNotIn("VKD3D_VRAM_SIZE", result)
        for value in ("0", "-1", "255", "16385", "$(touch /tmp/no)", "100;bad"):
            with self.subTest(value=value):
                self.assertEqual(LOW["game_environment"](dict(env, DROIDDECK_GPU_MEMORY_MB=value))["DXVK_CONFIG"], env["DXVK_CONFIG"])

    def run_session(self, shared, env=None):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".config/droiddeck/game-environment.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"version": 1, "shared": shared}))
            result = subprocess.run(["python3", str(OVERLAY / "droiddeck-low-memory")],
                                    env=dict(os.environ, HOME=directory, **(env or {})),
                                    capture_output=True, text=True, check=True)
            return result.stdout

    def test_shared_session_mode_and_profiler_can_be_controlled_independently(self):
        self.assertEqual(self.run_session({}), "")
        self.assertEqual(self.run_session({LOW["MODE"]: "1"}),
                         "export BL_MANGOAPP=0\nexport DROIDDECK_SESSION_LOW_MEMORY_ALLOCATOR=1\nexport DROIDDECK_SESSION_MEMORY_PROFILER=1\n")
        self.assertEqual(self.run_session({LOW["MODE"]: "1", LOW["PROFILER"]: "0"}),
                         "export BL_MANGOAPP=0\nexport DROIDDECK_SESSION_LOW_MEMORY_ALLOCATOR=1\n")
        self.assertEqual(self.run_session({LOW["PROFILER"]: "1"}), "export DROIDDECK_SESSION_MEMORY_PROFILER=1\n")
        self.assertEqual(self.run_session({LOW["MODE"]: "$(touch /tmp/no)"}), "")
        self.assertEqual(self.run_session({LOW["MODE"]: None}, {LOW["MODE"]: "1"}), "")

    def test_allocator_preserves_unrelated_tunables_and_replaces_duplicates(self):
        baseline = "glibc.pthread.rseq=0:glibc.malloc.top_pad=16777216:glibc.malloc.arena_max=8:glibc.malloc.top_pad=42:glibc.malloc.tcache_count=7"
        expected = "glibc.pthread.rseq=0:glibc.malloc.tcache_count=7:glibc.malloc.top_pad=1048576:glibc.malloc.arena_max=2:glibc.malloc.trim_threshold=131072"
        self.assertEqual(LOW["allocator_tunables"](baseline), expected)
        self.assertEqual(LOW["allocator_tunables"](expected), expected)

    def test_allocator_can_be_disabled_without_disabling_low_memory_mode(self):
        baseline = "glibc.pthread.rseq=0:glibc.malloc.top_pad=16777216"
        env = {LOW["MODE"]: "1", LOW["ALLOCATOR"]: "0", "GLIBC_TUNABLES": baseline}
        result = LOW["game_environment"](env)
        self.assertEqual(result["GLIBC_TUNABLES"], baseline)
        self.assertEqual(result["MANGOHUD"], "0")
        self.assertNotIn("LOW_MEMORY_ALLOCATOR", self.run_session({LOW["MODE"]: "1", LOW["ALLOCATOR"]: "0"}))

    def test_game_opt_out_restores_session_baseline_and_respects_custom_tunables(self):
        baseline = "glibc.pthread.rseq=0:glibc.malloc.top_pad=16777216"
        lowered = LOW["allocator_tunables"](baseline)
        for settings in ({LOW["MODE"]: "0"}, {LOW["MODE"]: "1", LOW["ALLOCATOR"]: "0"}):
            env = {**settings, LOW["BASE_GLIBC"]: baseline, LOW["LOW_GLIBC"]: lowered, "GLIBC_TUNABLES": lowered}
            self.assertEqual(LOW["game_environment"](env)["GLIBC_TUNABLES"], baseline)
            env["GLIBC_TUNABLES"] = "glibc.malloc.top_pad=2097152"
            self.assertEqual(LOW["game_environment"](env)["GLIBC_TUNABLES"], env["GLIBC_TUNABLES"])

    def test_allocator_cli_returns_data_without_shell_evaluation(self):
        value = "glibc.pthread.rseq=0:$(touch /tmp/allocator-should-not-execute)"
        result = subprocess.run(["python3", str(OVERLAY / "droiddeck-low-memory"), "--allocator"],
                                env=dict(os.environ, GLIBC_TUNABLES=value), capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, LOW["allocator_tunables"](value))

    @unittest.skipUnless(os.environ.get("DROIDDECK_JP_SOURCE"), "requires prepared session script")
    def test_native_session_allocator_on_off_and_ab_toggle(self):
        source = Path(os.environ["DROIDDECK_JP_SOURCE"])
        script = (source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-session").read_text()
        hook = script.split("# Shared low-memory settings", 1)[1].split("# Until the runtime is stable", 1)[0]
        hook = "# Shared low-memory settings" + hook
        hook = hook.replace("/usr/local/bin/droiddeck-low-memory", str(source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-low-memory"))
        keys = ("GLIBC_TUNABLES", LOW["BASE_GLIBC"], LOW["LOW_GLIBC"])
        child = "import json,os; print(json.dumps({k:os.environ.get(k) for k in %r}))" % (keys,)
        code = hook + "\n" + shlex.quote(sys.executable) + " -c " + shlex.quote(child)
        baseline = "glibc.pthread.rseq=0:glibc.malloc.top_pad=16777216:glibc.malloc.tcache_count=7"
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / ".config/droiddeck/game-environment.json"
            config.parent.mkdir(parents=True)
            for mode, allocator in (("1", "1"), ("0", "1"), ("1", "0")):
                config.write_text(json.dumps({"version": 1, "shared": {
                    LOW["MODE"]: mode, LOW["ALLOCATOR"]: allocator, LOW["PROFILER"]: "0"}}))
                result = subprocess.run(["bash", "-c", code], env=dict(os.environ, HOME=directory,
                                        GLIBC_TUNABLES=baseline, BL_INSIDE=""),
                                        capture_output=True, text=True, check=True)
                values = json.loads(result.stdout.splitlines()[-1])
                if mode == allocator == "1":
                    self.assertEqual(values["GLIBC_TUNABLES"], LOW["allocator_tunables"](baseline))
                    self.assertEqual(values[LOW["BASE_GLIBC"]], baseline)
                else:
                    self.assertEqual(values["GLIBC_TUNABLES"], baseline)
                    self.assertIsNone(values[LOW["BASE_GLIBC"]])

    def test_changed_source_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            with self.assertRaisesRegex(RuntimeError, "Low memory integration changed"):
                APPLY.apply(Path(directory))


class MemoryProfilerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name) / "proc"
        self.proc.mkdir()
        (self.proc / "meminfo").write_text("MemTotal: 8000 kB\nMemAvailable: 1000 kB\nSwapTotal: 2000 kB\nSwapFree: 1000 kB\n")

    def process(self, pid, name, uid=123, pss=100):
        directory = self.proc / str(pid)
        directory.mkdir()
        (directory / "status").write_text("Name: %s\nUid: %s %s %s %s\nVmRSS: 999 kB\n" % (name, uid, uid, uid, uid))
        (directory / "cmdline").write_bytes((name + "\0secret-token\0").encode())
        if pss is not None:
            (directory / "smaps_rollup").write_text("Pss: %s kB\nSwapPss: 10 kB\n" % pss)

    def test_pss_has_exclusive_categories_and_does_not_log_secrets(self):
        self.process(1, "steam", pss=100)
        self.process(2, "steamwebhelper", pss=200)
        self.process(3, "wineserver", pss=50)
        self.process(4, "Game.exe", pss=300)
        self.process(5, "FEXInterpreter", pss=150)
        self.process(6, "other-user", uid=456, pss=900)
        result = PROFILE["snapshot"](self.proc, uid=123)
        self.assertEqual(sum(result["pss_by_category"].values()), 800)
        self.assertEqual(result["pss_by_category"]["steamwebhelper"], 200)
        self.assertEqual(len(result["processes"]), 5)
        self.assertEqual(result["system"]["MemAvailable"], 1000)
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertEqual(result["gpu_allocations"], "not_measured")

    def test_unreadable_pss_is_unknown_not_zero_or_rss(self):
        self.process(1, "Game.exe", pss=None)
        result = PROFILE["snapshot"](self.proc, uid=123)
        self.assertEqual(result["pss_unreadable_processes"], 1)
        self.assertIsNone(result["processes"][0]["pss_kb"])
        self.assertEqual(result["processes"][0]["rss_kb"], 999)
        self.assertEqual(result["pss_by_category"], {})

    def test_android_uid_is_read_from_proc_even_under_proot_root(self):
        self.process(1, "steam")
        (self.proc / "self").symlink_to(self.proc / "1", target_is_directory=True)
        with patch.object(PROFILE["os"], "getuid", return_value=0):
            result = PROFILE["snapshot"](self.proc)
        self.assertEqual(result["pss_by_category"], {"steam": 100})

    def test_log_rotation_keeps_current_and_one_previous_file(self):
        path = Path(self.temp.name) / "memory.jsonl"
        with patch.dict(PROFILE["append"].__globals__, CAP=1):
            for sample in range(3):
                PROFILE["append"](path, {"sample": sample})
        self.assertEqual(json.loads(path.read_text()), {"sample": 2})
        self.assertEqual(json.loads(path.with_suffix(".jsonl.1").read_text()), {"sample": 1})

    def test_process_identity_handles_parentheses_pid_reuse_and_zombies(self):
        directory = self.proc / "42"
        directory.mkdir()
        stat = directory / "stat"
        stat.write_text("42 (comm with ) spaces) S " + "0 " * 18 + "1234 0")
        self.assertEqual(PROFILE["identity"](self.proc, 42), "1234")
        stat.write_text("42 (other) S " + "0 " * 18 + "5678 0")
        self.assertEqual(PROFILE["identity"](self.proc, 42), "5678")
        stat.write_text("42 (other) Z " + "0 " * 18 + "5678 0")
        self.assertIsNone(PROFILE["identity"](self.proc, 42))

    def test_zram_stats_are_in_bytes_and_unreadable_devices_are_unknown(self):
        block = Path(self.temp.name) / "block"
        (block / "zram0").mkdir(parents=True)
        (block / "zram0/mm_stat").write_text("1000 200 300 0 0 0 0")
        (block / "zram1").mkdir()
        result = PROFILE["zram_stats"](block)
        self.assertEqual(result["zram0"], {"original_bytes": 1000, "compressed_bytes": 200, "physical_used_bytes": 300})
        self.assertIsNone(result["zram1"])

    def test_monitor_exits_after_session_parent_exits(self):
        directory = Path(self.temp.name) / "session"
        parent = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: parent.poll() is None and parent.kill())
        monitor = subprocess.Popen(["python3", str(OVERLAY / "droiddeck-memory-profiler"), str(parent.pid)],
                                   env=dict(os.environ, BL_DEBUG_DIR=str(directory)),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: monitor.poll() is None and monitor.kill())
        deadline = time.monotonic() + 3
        log = directory / "memory.jsonl"
        while not log.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(log.exists())
        parent.terminate()
        parent.wait(timeout=2)
        self.assertEqual(monitor.wait(timeout=8), 0)


if __name__ == "__main__":
    unittest.main()
