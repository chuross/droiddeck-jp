"""Give foreground launcher toasts explicit contrasting colors on vendor Android builds."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
BASE = Path("app/src/main/java/com/droiddeck/launcher")
TARGETS = ("MainActivity.kt", "ProtonMenu.kt")
BEFORE = "android.widget.Toast.makeText("
AFTER = "com.droiddeck.launcher.ui.PatchToast.makeText("
KOTLIN = BASE / "ui/PatchToast.kt"


def check(source):
    if (source / KOTLIN).exists():
        raise RuntimeError("Readable toast overlay already applied")
    for name in TARGETS:
        if BEFORE not in (source / BASE / name).read_text():
            raise RuntimeError("Launcher toast integration changed: " + name)


def apply(source):
    check(source)
    for name in TARGETS:
        path = source / BASE / name
        path.write_text(path.read_text().replace(BEFORE, AFTER))
    (source / KOTLIN).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "PatchToast.kt", source / KOTLIN)
