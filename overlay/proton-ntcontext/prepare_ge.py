"""Reuse exactly GE's Wine preparation, without fetching unrelated components."""
import argparse
from pathlib import Path


def wine_preparation(text):
    header_marker = "### (1) PREP SECTION ###"
    start_marker = "### (2) WINE PATCHING ###"
    end_marker = "### END WINE PATCHING ###"
    openxr_marker = "    pushd wineopenxr\n"
    for marker in (header_marker, start_marker, end_marker, openxr_marker):
        if text.count(marker) != 1:
            raise RuntimeError("GE preparation changed: " + marker)
    openxr_start = text.index(openxr_marker)
    openxr_end = text.index("    popd", openxr_start) + len("    popd")
    return ("#!/bin/bash\nset -euo pipefail\n" + text[:text.index(header_marker)]
            + text[openxr_start:openxr_end] + "\n"
            + text[text.index(start_marker):text.index(end_marker)] + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proton", type=Path)
    args = parser.parse_args()
    script = args.proton / "patches/protonprep-valve-staging.sh"
    (args.proton / "prepare-ntcontext-wine.sh").write_text(wine_preparation(script.read_text()))
