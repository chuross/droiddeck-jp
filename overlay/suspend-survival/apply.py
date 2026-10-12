"""Suspend diagnostics and an opt-in CPU keepalive experiment."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "suspend-survival.patch"
FILES = {
    "SuspendSurvival.kt": "app/src/main/java/com/droiddeck/launcher/session/SuspendSurvival.kt",
    "SuspendSurvivalRow.kt": "app/src/main/java/com/droiddeck/launcher/ui/SuspendSurvivalRow.kt",
    "SuspendSurvivalTest.kt": "app/src/test/java/com/droiddeck/launcher/session/SuspendSurvivalTest.kt",
    "HostProcessTrackedTest.kt": "app/src/test/java/com/droiddeck/launcher/core/HostProcessTrackedTest.kt",
}


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode or any((source / path).exists() for path in FILES.values()):
        raise RuntimeError("Suspend survival integration changed; review lifecycle/log/lock hooks:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    for name, path in FILES.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
