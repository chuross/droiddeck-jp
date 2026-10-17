"""Validate local driver bundles and corresponding source; never publish releases."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from driver import source_identity, verify_source


def validate(work):
    plan = json.loads((work / "plan.json").read_text())
    tag = plan["tag"]
    if not re.fullmatch(r"DD-Turnip-v\d+\.\d+\.\d+", tag) or not plan["build"] or plan["status"] != "patch required":
        raise RuntimeError("Not a patched DD-Turnip build plan")
    if plan["identity"] != source_identity(plan):
        raise RuntimeError("Overlay identity changed after plan")
    dist = work / "dist"
    bundle = dist / (tag + ".zip")
    source = dist / (tag + "-source.tar.xz")
    checksum = dist / (tag + ".zip.sha256")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if checksum.read_text() != digest + "  " + bundle.name + "\n":
        raise RuntimeError("Bundle checksum mismatch")
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Corrupt driver bundle")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["tag"] != tag or manifest["mesa"]["commit"] != plan["mesa_commit"]:
            raise RuntimeError("Bundle provenance mismatch")
        for target in ("android", "linux"):
            record = manifest["drivers"][target]
            if record["sha256"] != hashlib.sha256(archive.read(record["library"])).hexdigest():
                raise RuntimeError("Driver checksum mismatch")
    verify_source(source, plan)
    return plan, [bundle, checksum, source]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path("build/turnip-process-heap-usage"))
    validate(parser.parse_args().work)
