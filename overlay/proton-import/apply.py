"""Import local ARM64 Proton archives through Android's document picker."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "proton-import.patch"
HELPERS = ("droiddeck-proton-import",)
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS)
KOTLIN = "app/src/main/java/com/droiddeck/launcher/session/ProtonImport.kt"
TEST = "app/src/test/java/com/droiddeck/launcher/session/ProtonImportTest.kt"


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode or (source / KOTLIN).exists() or (source / TEST).exists():
        raise RuntimeError("Proton import integration changed; review the file picker:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    shutil.copy2(ROOT / "ProtonImport.kt", source / KOTLIN)
    (source / TEST).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "ProtonImportTest.kt", source / TEST)

