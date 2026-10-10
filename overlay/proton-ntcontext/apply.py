"""Patch the external GE Wine source, keeping the context fully zero-initialized."""
import argparse
from pathlib import Path
import subprocess

PATCH = Path(__file__).with_name("ntgetcontext-null.patch")


def check(source):
    subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)], check=True)


def apply(source):
    check(source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine-source", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    (check if args.check else apply)(args.wine_source.resolve())
