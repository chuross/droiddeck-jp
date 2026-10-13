"""Add explicit Quick Share buttons without using the Android default share target."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "quick-share.patch"
BASE = "app/src/main/"
FILES = {
    "QuickShareLogs.kt": BASE + "java/com/droiddeck/launcher/session/QuickShareLogs.kt",
    "QuickShareLogButton.kt": BASE + "java/com/droiddeck/launcher/ui/QuickShareLogButton.kt",
    "strings.xml": BASE + "res/values/quick_share.xml",
    "strings-ja.xml": BASE + "res/values-ja/quick_share.xml",
    "QuickShareLogsTest.kt": "app/src/test/java/com/droiddeck/launcher/session/QuickShareLogsTest.kt",
}


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode or any((source / path).exists() for path in FILES.values()):
        raise RuntimeError("Quick Share integration changed; review sharing hooks:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    for name, path in FILES.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
