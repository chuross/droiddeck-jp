"""APK bundled source hooks; official sources and DriverPairs remain unchanged."""
from pathlib import Path
import subprocess
import shutil

PATCH = Path(__file__).resolve().with_name("source.patch")
TEST = "app/src/test/java/com/droiddeck/launcher/gpu/TurnipSourceTest.kt"
PATCHES = (PATCH, PATCH.with_name("source-nightly.patch"))
HELPER = "app/src/main/java/com/droiddeck/launcher/gpu/BundledTurnip.kt"
OWNED_PATHS = ("app/src/main/java/com/droiddeck/launcher/gpu/TurnipReleases.kt", TEST, HELPER)


def check(source):
    if (source / TEST).exists() or (source / HELPER).exists():
        raise RuntimeError("Turnip source test collision")
    failures = []
    for patch in PATCHES:
        result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(patch)], capture_output=True, text=True)
        if result.returncode == 0:
            return patch
        failures.append(patch.name + ":\n" + result.stderr)
    raise RuntimeError("Turnip source registration drift:\n" + "\n".join(failures))


def apply(source):
    patch = check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(patch)], check=True)
    (source / TEST).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PATCH.with_name("TurnipSourceTest.kt"), source / TEST)

    shutil.copy2(PATCH.with_name("BundledTurnip.kt"), source / HELPER)
