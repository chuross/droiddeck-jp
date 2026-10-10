"""Exercise the actual JavaScript state transitions without a running Steam client."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "overlay/offline-mode/droiddeck-steam-connection"))
NODE = shutil.which("node")


class AgentError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@unittest.skipUnless(NODE, "Node is needed to execute the Steam UI callbacks")
class SteamConnectionTest(unittest.TestCase):
    def invoke(self, operation, *, offline=False, running=False, behavior="success", ready=True):
        observed = {}

        def evaluate(directory, expression, **kwargs):
            script = """
let offline = OFFLINE, ready = READY, pending = null, now = 0;
const calls = [];
Date.now = () => now;
globalThis.setTimeout = callback => {
  now += 200;
  if (pending !== null) { offline = pending; pending = null; }
  callback();
};
globalThis.App = {BHasCurrentUser: () => ready, BIsOfflineMode: () => offline};
globalThis.SteamUIStore = {IsAnyAppRunning: () => RUNNING};
function switchMode(value) {
  calls.push(value);
  if (BEHAVIOR === 'throw') throw new Error('failure');
  if (BEHAVIOR === 'reject') return Promise.reject(new Error('failure'));
  if (BEHAVIOR === 'success') pending = value;
}
globalThis.SteamClient = {User: {
  GoOffline: () => switchMode(true), GoOnline: () => switchMode(false),
}};
(async () => {
  const value = await EXPRESSION;
  console.log(JSON.stringify({value, calls}));
})();
"""
            for name, value in {"OFFLINE": offline, "RUNNING": running, "READY": ready,
                                "BEHAVIOR": behavior}.items():
                script = script.replace(name, json.dumps(value))
            script = script.replace("EXPRESSION", expression)
            proc = subprocess.run([NODE, "-e", script], check=True, text=True, capture_output=True, timeout=5)
            observed.update(json.loads(proc.stdout))
            return {"value": observed["value"]}

        try:
            result = HELPER["connection"](Path("/unused"), {"op": operation}, evaluate, AgentError)
            return result, observed.get("calls", [])
        except AgentError as error:
            return error.code, observed.get("calls", [])

    def test_online_and_offline_wait_for_reported_state_without_restarting(self):
        for operation, before, after in (("offline", False, True), ("online", True, False)):
            with self.subTest(operation=operation):
                result, calls = self.invoke(operation, offline=before)
                self.assertEqual(result, {"offline": after, "running": False})
                self.assertEqual(calls, [after])

    def test_status_and_already_selected_mode_do_not_change_steam(self):
        for operation, offline in (("status", True), ("offline", True), ("online", False)):
            result, calls = self.invoke(operation, offline=offline)
            self.assertEqual(result["offline"], offline)
            self.assertEqual(calls, [])

    def test_running_game_blocks_go_offline_without_killing_the_game(self):
        result, calls = self.invoke("offline", running=True)
        self.assertEqual(result, "GAME_RUNNING")
        self.assertEqual(calls, [])

    def test_failure_or_no_transition_is_not_reported_as_success(self):
        for behavior, code in (("throw", "STEAM_SWITCH_FAILED"), ("reject", "STEAM_SWITCH_FAILED"),
                               ("nochange", "STEAM_SWITCH_TIMEOUT")):
            with self.subTest(behavior=behavior):
                result, _ = self.invoke("offline", behavior=behavior)
                self.assertEqual(result, code)

    def test_not_signed_in_does_not_send_a_switch(self):
        result, calls = self.invoke("offline", ready=False)
        self.assertEqual(result, "STEAM_NOT_READY")
        self.assertEqual(calls, [])

    def test_only_fixed_operations_are_accepted(self):
        result, calls = self.invoke("ForceShutdown")
        self.assertEqual(result, "INVALID_REQUEST")
        self.assertEqual(calls, [])

    def test_malformed_reply_is_rejected(self):
        with self.assertRaises(AgentError) as raised:
            HELPER["connection"](Path("/unused"), {}, lambda *a, **k: {"value": {"ok": True, "offline": "false", "running": False}}, AgentError)
        self.assertEqual(raised.exception.code, "INVALID_STEAM_RESPONSE")


@unittest.skipUnless(os.environ.get("DROIDDECK_JP_SOURCE"), "needs a prepared checkout")
class GuestConnectionIntegrationTest(unittest.TestCase):
    def test_dedicated_request_uses_the_live_cdp_transport(self):
        from unittest.mock import patch
        agent = runpy.run_path(str(Path(os.environ["DROIDDECK_JP_SOURCE"]) / "tools/linuxfs/overlay/usr/local/bin/droiddeck-agent"))
        with patch.dict(agent["handle"].__globals__, cdp_eval=lambda *a, **k: {"value": {"ok": True, "offline": True, "running": False}}):
            self.assertEqual(agent["handle"](Path("/unused"), {"kind": "steam_connection", "op": "status"}),
                             {"offline": True, "running": False})


if __name__ == "__main__":
    unittest.main()
