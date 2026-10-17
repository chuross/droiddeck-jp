"""Apply the independently removable background-memory policy and UI."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "background-memory.patch"
PATCHES = (PATCH, ROOT / "background-memory-nightly.patch")
BASE = "app/src/main/java/com/droiddeck/launcher/"
FILES = {
    "BackgroundAppLimit.kt": BASE + "core/BackgroundAppLimit.kt",
    "BackgroundMemorySettings.kt": BASE + "ui/BackgroundMemorySettings.kt",
    "BackgroundAppLimitTest.kt": "app/src/test/java/com/droiddeck/launcher/core/BackgroundAppLimitTest.kt",
}
# A manifest of every modified or added path, also used by removal tests.
OWNED_PATHS = tuple(FILES.values()) + tuple(
    line.removeprefix("+++ b/") for line in PATCH.read_text().splitlines() if line.startswith("+++ b/"))


def equivalent(source):
    policy = source / BASE / "core/BackgroundAppLimit.kt"
    if not policy.exists():
        return False
    text = policy.read_text()
    service = (source / BASE / "session/SessionService.kt").read_text()
    main = (source / BASE / "MainActivity.kt").read_text()
    ui = (source / BASE / "ui/FrontEndSetup.kt").read_text()
    adb = (source / BASE / "core/WirelessAdbFix.kt").read_text()
    prefs_path = source / BASE / "session/SessionPrefs.kt"
    prefs = prefs_path.read_text() if prefs_path.exists() else ""
    checks = [all(word in text for word in ("max_cached_processes", "previous", "commit()", "restoreAsync", "applyAsync", "device_config delete")),
              all(word in service for word in ("BackgroundAppLimit.applyAsync", "BackgroundAppLimit.restoreAsync", "clearCachedApps")),
              "!SessionState.running" in main and "BackgroundAppLimit.restoreAsync" in main,
              all(word in ui for word in ("backgroundAppLimit", "clearCachedApps", "adbPaired")),
              "am kill-all" in adb and "hasSavedPairing" in adb,
              "backgroundAppLimit" in prefs and "clearCachedApps" in prefs]
    if all(checks):
        return True
    raise RuntimeError("background-memory partial upstream match; review before applying")


def check(source):
    if equivalent(source):
        print("background-memory: equivalent upstream fix detected; overlay unnecessary")
        return False
    if any((source / name).exists() for name in FILES.values()):
        raise RuntimeError("background-memory generated file collision")
    failures = []
    for patch in PATCHES:
        result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(patch)], capture_output=True, text=True)
        if result.returncode == 0:
            return patch
        failures.append(patch.name + ":\n" + result.stderr)
    raise RuntimeError("background-memory upstream drift:\n" + "\n".join(failures))


def apply(source):
    patch = check(source)
    if not patch:
        return
    subprocess.run(["git", "-C", str(source), "apply", str(patch)], check=True)
    for name, target in FILES.items():
        path = source / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
