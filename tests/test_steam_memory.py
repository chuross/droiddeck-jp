import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
MEMORY = runpy.run_path(str(ROOT / "overlay/low-memory/droiddeck-steam-memory"))


class SteamMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_pressure_retries_and_failures_are_rate_limited_across_focus_changes(self):
        policy = MEMORY["Reclaimer"]()
        self.assertFalse(policy.due(0, None, False, 1))
        self.assertTrue(policy.due(1, 1446780, False, 2000000))
        self.assertFalse(policy.due(6, 1446780, False, 1))
        self.assertFalse(policy.due(120, 1446780, False, 1))
        self.assertTrue(policy.due(121, 1446780, False, 1))
        self.assertFalse(policy.due(130, None, False, 1))
        self.assertFalse(policy.due(131, 1446780, False, 1))
        self.assertTrue(policy.due(241, 1446780, False, 1))

    def test_launch_request_reclaims_before_the_first_game_window(self):
        policy = MEMORY["Reclaimer"]()
        self.assertTrue(policy.due(0, None, True, None))
        self.assertFalse(policy.due(5, 1446780, False, None))
        self.assertTrue(policy.due(120, 1446780, False, None))
        self.assertFalse(policy.due(240, 1446780, False, None))
        self.assertFalse(policy.due(245, 1446780, False, 2000000))

    def test_returning_to_steam_cancels_pending_game_trim(self):
        policy = MEMORY["Reclaimer"]()
        self.assertTrue(policy.due(0, 1446780, False, 1))
        self.assertFalse(policy.due(5, 42, False, 1))
        self.assertFalse(policy.due(10, None, False, 1))
        self.assertFalse(policy.due(120, None, False, 1))

    def test_focus_and_system_memory_are_unknown_when_unreadable_or_invalid(self):
        focus = self.root / "focus.json"
        self.assertIsNone(MEMORY["game_focus"](focus))
        for value in (None, 0, 769, -1, "1446780", True):
            focus.write_text(json.dumps({"focusedApp": value}))
            self.assertIsNone(MEMORY["game_focus"](focus))
        focus.write_text(json.dumps({"focusedApp": 1446780}))
        self.assertEqual(MEMORY["game_focus"](focus), 1446780)
        focus.write_text("broken")
        self.assertIsNone(MEMORY["game_focus"](focus))
        self.assertIsNone(MEMORY["available_kb"](self.root))
        (self.root / "meminfo").write_text("MemAvailable: 123456 kB\n")
        self.assertEqual(MEMORY["available_kb"](self.root), 123456)

    def process(self, pid, uid=123, pss=100, binary="steamwebhelper"):
        directory = self.root / str(pid)
        directory.mkdir()
        (directory / "status").write_text("Uid: %d %d %d %d\n" % ((uid,) * 4))
        (directory / "cmdline").write_bytes(("/steam/" + binary + "\0secret-token\0").encode())
        if pss is not None:
            (directory / "smaps_rollup").write_text("Pss: %d kB\n" % pss)

    def test_cef_pss_excludes_other_uids_games_and_arguments(self):
        self.process(1, pss=100)
        (self.root / "self").symlink_to(self.root / "1", target_is_directory=True)
        self.process(2, pss=200)
        self.process(3, uid=456, pss=900)
        self.process(4, pss=1000, binary="Game.exe")
        result = MEMORY["steam_pss"](self.root)
        self.assertEqual(result, {"pss_kb": 300, "processes": 2, "unreadable": 0})
        self.assertNotIn("secret-token", json.dumps(result))

    def test_partial_pss_and_absent_cef_are_unknown_not_zero_or_rss(self):
        self.process(1, binary="droiddeck-agent")
        (self.root / "self").symlink_to(self.root / "1", target_is_directory=True)
        self.assertIsNone(MEMORY["steam_pss"](self.root)["pss_kb"])
        self.process(2, pss=100)
        self.process(3, pss=None)
        self.assertEqual(MEMORY["steam_pss"](self.root),
                         {"pss_kb": None, "processes": 2, "unreadable": 1})

    def test_cdp_requests_pressure_once_and_closes_connection(self):
        connection = Mock()
        connection.recv.side_effect = [json.dumps({"method": "event"}), json.dumps({"id": 1, "result": {}})]
        websocket = Mock(return_value=connection)
        targets = Mock(return_value=[{"title": "SharedJSContext", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/test"}])
        MEMORY["notify"](self.root, targets, websocket)
        self.assertEqual(json.loads(connection.send.call_args.args[0]),
                         {"id": 1, "method": "Memory.simulatePressureNotification", "params": {"level": "critical"}})
        connection.send.assert_called_once()
        connection.close.assert_called_once()
        targets.assert_called_once_with(self.root, timeout=2)

    def test_unavailable_cef_and_protocol_errors_leave_ui_running(self):
        websocket = Mock()
        with self.assertRaises(RuntimeError):
            MEMORY["notify"](self.root, Mock(return_value=[]), websocket)
        websocket.assert_not_called()
        for response in ({"id": 1, "error": {"code": -32601}}, {"id": 1}, "invalid"):
            connection = Mock()
            connection.recv.return_value = response if isinstance(response, str) else json.dumps(response)
            targets = Mock(return_value=[{"title": "SharedJSContext", "webSocketDebuggerUrl": "ws://127.0.0.1/test"}])
            with self.assertRaises((ValueError, RuntimeError)):
                MEMORY["notify"](self.root, targets, Mock(return_value=connection))
            connection.close.assert_called_once()

    def test_watch_logs_observed_change_and_never_reports_unknown_pss_as_saved(self):
        proc = self.root / "proc"
        proc.mkdir()
        (self.root / "focus.json").write_text('{"focusedApp":1446780}')
        class Finished(BaseException):
            pass
        for before, after, expected in ((100, 80, -20), (None, 80, None), (80, 100, 20)):
            log = Mock()
            snapshot = lambda value: {"pss_kb": value, "processes": 1, "unreadable": int(value is None)}
            with patch.dict(MEMORY["watch"].__globals__,
                            steam_pss=Mock(side_effect=[snapshot(before), snapshot(after)]), notify=Mock()), \
                 patch.object(MEMORY["time"], "sleep", side_effect=[None, None, Finished()]):
                with self.assertRaises(Finished):
                    MEMORY["watch"](self.root, Mock(), Mock(), log, proc)
            record = json.loads(log.call_args.args[0].split(": ", 1)[1])
            self.assertEqual(record["observed_pss_delta_kb"], expected)

    def test_daemon_thread_does_not_spawn_another_process(self):
        with patch.object(MEMORY["threading"], "Thread") as thread:
            MEMORY["start"](self.root, Mock(), Mock(), Mock())
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.return_value.start.assert_called_once()


@unittest.skipUnless(os.environ.get("DROIDDECK_PATCH_SOURCE"), "requires prepared agent/session scripts")
class SteamMemoryIntegrationTest(unittest.TestCase):
    def test_pressure_notification_over_the_real_agent_websocket(self):
        source = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        fixture = runpy.run_path(str(source / "tools/tests/test_droiddeck_agent.py"))
        agent = fixture["agent"]
        server = fixture["FakeDevTools"]({"result": {}})
        self.addCleanup(server.server.close)
        server.start()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cdp-port").write_text(str(server.port))
            MEMORY["notify"](root, agent.cdp_targets, agent.WebSocket)
        self.assertEqual(server.seen, [{"id": 1, "method": "Memory.simulatePressureNotification",
                                       "params": {"level": "critical"}}])

    def test_existing_agent_starts_reclaim_only_with_focus_and_session_snapshot(self):
        source = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        agent = runpy.run_path(str(source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-agent"))
        class Finished(BaseException):
            pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for focus, display, trim, expected in ((True, ":0", "1", True), (True, ":0", "0", False),
                                                   (False, ":0", "1", False), (True, "", "1", False)):
                start = Mock()
                with patch.dict(os.environ, DISPLAY=display, DROIDDECK_SESSION_STEAM_TRIM=trim), \
                     patch.dict(agent["serve"].__globals__, FocusWatcher=Mock(), write_json=Mock(),
                                displays=Mock(return_value=[]), log=Mock()), \
                     patch.object(agent["runpy"], "run_path", return_value={"start": start}), \
                     patch.object(agent["time"], "sleep", side_effect=Finished):
                    with self.assertRaises(Finished):
                        agent["serve"](root, focus=focus)
                self.assertEqual(start.called, expected)

    def test_session_decky_skip_preserves_marker_and_normal_launch(self):
        import subprocess
        source = Path(os.environ["DROIDDECK_PATCH_SOURCE"])
        session = (source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-session").read_text()
        # Exercise the actual shell condition with a harmless arm64 fixture loader.
        hook = session.split('    decky_loader=$HOME/homebrew/services/PluginLoader', 1)[1].split('    _bl_cdp=()', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            services = root / "homebrew/services"
            services.mkdir(parents=True)
            loader = services / "PluginLoader"
            loader.write_text('#!/bin/sh\necho started >> "$CALLS"\nexec sleep 60\n')
            loader.chmod(0o755)
            (services / ".droiddeck-decky-arch").write_text("arm64")
            marker = root / ".droiddeck-decky-enabled"
            marker.touch()
            calls = root / "calls"
            code = 'decky_loader="$HOME/homebrew/services/PluginLoader"\n' + hook + '''
if [ -n "${decky_supervisor:-}" ]; then
  for n in {1..100}; do [ -e "$CALLS" ] && break; sleep 0.01; done
  kill "$decky_supervisor" 2>/dev/null || true
  wait "$decky_supervisor" 2>/dev/null || true
fi
'''
            for skip in ("1", "0"):
                result = subprocess.run(["bash", "-c", code], env=dict(os.environ, HOME=directory,
                    CALLS=str(calls), DROIDDECK_SESSION_SKIP_DECKY=skip), capture_output=True, text=True, timeout=5, check=True)
                self.assertEqual(calls.exists(), skip == "0")
                self.assertTrue(marker.exists())
                if skip == "1":
                    self.assertIn("Decky loader/plugins skipped", result.stdout)


if __name__ == "__main__":
    unittest.main()
