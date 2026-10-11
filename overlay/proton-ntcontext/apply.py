"""Apply the GE Wine context fix and DXVK reported memory budget patch."""
import argparse
from pathlib import Path
import subprocess

PATCH = Path(__file__).with_name("ntgetcontext-null.patch")
DXVK_PATCH = Path(__file__).with_name("dxvk-memory-budget.patch")


def check(source, dxvk_source=None):
    subprocess.run(["git", "-C", str(source), "apply", "--check", str(PATCH)], check=True)
    if dxvk_source is not None:
        subprocess.run(["git", "-C", str(dxvk_source), "apply", "--check", str(DXVK_PATCH)], check=True)


def apply(source, dxvk_source=None):
    # Check both source trees before either one is modified.
    check(source, dxvk_source)
    subprocess.run(["git", "-C", str(source), "apply", str(PATCH)], check=True)
    if dxvk_source is not None:
        subprocess.run(["git", "-C", str(dxvk_source), "apply", str(DXVK_PATCH)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine-source", type=Path, required=True)
    parser.add_argument("--dxvk-source", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    (check if args.check else apply)(args.wine_source.resolve(), args.dxvk_source.resolve())
