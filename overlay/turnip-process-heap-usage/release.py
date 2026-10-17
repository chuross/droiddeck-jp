"""Publish only DD-Turnip assets; never edit APK/Proton releases or Latest."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from driver import source_identity, verify_source


def run(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("DD-Turnip publication failed:\n" + result.stderr + result.stdout)
    return result.stdout


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


def publish(repository, manager, work):
    plan, assets = validate(work)
    if not re.fullmatch(r"[0-9a-f]{40}", manager):
        raise RuntimeError("Expected exact manager commit")
    tag = plan["tag"]
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", repository, "--json", "isDraft"], capture_output=True, text=True)
    with tempfile.TemporaryDirectory() as temporary:
        notes = Path(temporary) / "notes"
        notes.write_text(
            f'Official DD-Turnip + process-wide heapUsage only.\n\n'
            f'Official: https://github.com/{plan["official_repository"]}/releases/tag/{tag}\n'
            f'Recipe: {plan["recipe_commit"]}\nMesa: {plan["mesa_commit"]}\n'
            f'Local patched recipe: {plan["patched_recipe_commit"]}\n'
            f'Corresponding Source: {tag}-source.tar.xz\n\n'
            f'<!-- turnip-source: {plan["identity"]} -->\n')
        if existing.returncode:
            if "not found" not in existing.stderr.lower() and "404" not in existing.stderr:
                raise RuntimeError(existing.stderr)
            run("gh", "release", "create", tag, "--repo", repository, "--target", manager,
                "--title", tag, "--notes-file", notes, "--draft", "--latest=false")
        # Never delete assets and never touch a release outside this exact DD-Turnip tag.
        run("gh", "release", "upload", tag, "--repo", repository, *assets, "--clobber")
        run("gh", "release", "edit", tag, "--repo", repository, "--notes-file", notes,
            "--draft=false", "--latest=false")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manager", required=True)
    parser.add_argument("--work", type=Path, default=Path("build/turnip-process-heap-usage"))
    args = parser.parse_args()
    publish(args.repository, args.manager, args.work)
