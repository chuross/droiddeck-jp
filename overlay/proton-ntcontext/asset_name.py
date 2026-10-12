"""Public download names omit the upstream snapshot date; provenance stays in the archive."""
import re


def archive_name(lock):
    match = re.fullmatch(r"proton-plop-([0-9]+\.[0-9]+)-[0-9]{8}-arm64-droiddeck", lock["build_name"])
    if match is None:
        raise ValueError("Unexpected Proton build name")
    return "proton-plop-" + match[1] + "-droiddeck-arm64.tar.gz"
