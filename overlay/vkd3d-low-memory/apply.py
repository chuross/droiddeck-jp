"""Opt-in reclamation of VKD3D's unused scratch and command-pool memory."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "vkd3d-low-memory.patch"
HEADER = "libs/vkd3d/droiddeck_memory.h"


def check(source):
    if (source / HEADER).exists():
        raise RuntimeError("VKD3D low-memory header already exists; use a clean checkout")
    subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)], check=True)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    shutil.copy2(ROOT / "droiddeck_memory.h", source / HEADER)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    (check if args.check else apply)(args.source.resolve())
