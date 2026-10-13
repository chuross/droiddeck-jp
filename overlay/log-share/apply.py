"""Share bounded, redacted snapshots without rewriting logs with active writers."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "log-share.patch"
FILES = {name: "app/src/" + kind + "/java/com/droiddeck/launcher/session/" + name
         for name, kind in (("SessionLogSnapshot.kt", "main"),
                            ("SessionLogSnapshotTest.kt", "test"),
                            ("LogShareLifecycleTest.kt", "test"))}


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode or any((source / path).exists() for path in FILES.values()):
        raise RuntimeError("Log share integration changed; review snapshot/lifecycle hooks:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)], check=True)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    for name, path in FILES.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
