"""Opt-in low memory settings and bounded session memory profiling."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "low-memory.patch"
HOME_PATCH = ROOT / "home-memory.patch"
HELPERS = ("droiddeck-low-memory", "droiddeck-memory-profiler", "droiddeck-steam-memory", "droiddeck-steam-ui-lite")
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS)
KOTLIN = "app/src/main/java/com/droiddeck/launcher/ui/LowMemoryRow.kt"
HOME_KOTLIN = "app/src/main/java/com/droiddeck/launcher/session/LowMemoryHome.kt"
HOME_TEST = "app/src/test/java/com/droiddeck/launcher/session/LowMemoryHomeTest.kt"


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    home = subprocess.run(["git", "-C", str(source), "apply", "--check", str(HOME_PATCH)],
                          capture_output=True, text=True)
    if (result.returncode or home.returncode or (source / KOTLIN).exists()
            or (source / HOME_KOTLIN).exists() or (source / HOME_TEST).exists()):
        raise RuntimeError("Low memory integration changed; review launch/settings hooks:\n" + result.stderr + home.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    subprocess.run(["git", "-C", str(source), "apply", str(HOME_PATCH)], check=True)
    shutil.copy2(ROOT / "LowMemoryRow.kt", source / KOTLIN)
    shutil.copy2(ROOT / "LowMemoryHome.kt", source / HOME_KOTLIN)
    shutil.copy2(ROOT / "LowMemoryHomeTest.kt", source / HOME_TEST)
