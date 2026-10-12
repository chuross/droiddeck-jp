"""Share bounded, redacted snapshots without rewriting logs with active writers."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "log-share.patch"
FILES = {name: "app/src/" + kind + "/java/com/droiddeck/launcher/session/" + name
         for name, kind in (("SessionLogSnapshot.kt", "main"),
                            ("SessionLogSnapshotTest.kt", "test"),
                            ("LogShareLifecycleTest.kt", "test"))}


def check(source):
    # prepare validates every overlay before applying any of them. Reproduce only the earlier
    # suspend-survival hook in a disposable validation tree, never mutate the caller's checkout.
    with tempfile.TemporaryDirectory() as temporary:
        tree = Path(temporary)
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        for path in re.findall(r"^--- a/(.+)$", PATCH.read_text(), re.MULTILINE):
            text = (source / path).read_text()
            if path.endswith("SessionArtifacts.kt") and "SuspendSurvival.recoverExit" not in text:
                hook = "                val record = Record(dir)\n                if (newest)"
                if text.count(hook) != 1:
                    raise RuntimeError("Log share integration changed; review recovery hook")
                text = text.replace(hook, "                val record = Record(dir)\n"
                                    "                SuspendSurvival.recoverExit(context, dir)\n                if (newest)")
            target = tree / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        result = subprocess.run(["git", "-C", str(tree), "apply", "--check", str(PATCH)],
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
