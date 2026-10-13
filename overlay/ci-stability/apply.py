"""Bound dependency detection to the game tree and clean up deferred test workers."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "ci-stability.patch"
TEST = "app/src/test/java/com/droiddeck/launcher/frontend/DependencyDetectorRootTest.kt"


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode or (source / TEST).exists():
        raise RuntimeError("CI stability integration changed; review detector/test hooks:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    target = source / TEST
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "DependencyDetectorRootTest.kt", target)
