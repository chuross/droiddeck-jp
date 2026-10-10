"""Live Steam connection controls."""
from pathlib import Path
import subprocess

PATCH = Path(__file__).with_name("steam-connection.patch")
HELPERS = ("droiddeck-steam-connection",)
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS)


def check(source):
    result = subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Steam connection integration changed; review the live controls:\n" + result.stderr)


def apply(source):
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
