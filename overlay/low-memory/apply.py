"""Opt-in low memory settings and bounded session memory profiling."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
PATCH = ROOT / "low-memory.patch"
HELPERS = ("droiddeck-low-memory", "droiddeck-memory-profiler")
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS)


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Low memory integration changed; review launch/settings hooks:\n" + result.stderr)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
