"""Public download names retain the DroidDeck-Proton version."""
import re


def archive_name(lock):
    if not re.fullmatch(r"DroidDeck-Proton-[0-9]+-[0-9]+(?:\.[0-9]+)*-droiddeck", lock["build_name"]):
        raise ValueError("Unexpected Proton build name")
    return lock["build_name"] + "-aarch64.tar.gz"
