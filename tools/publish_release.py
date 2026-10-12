#!/usr/bin/env python3
"""Publish APK/SHA256 to an explicitly selected repository."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def run(*args, check=True):
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if check and result.returncode:
        print(result.stderr or result.stdout or "Command failed without output", file=sys.stderr)
        result.check_returncode()
    return result


def api(endpoint):
    return json.loads(run("gh", "api", endpoint).stdout)


def publish(repository, manager, plan, directory):
    tag = plan["tag"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", tag):
        raise RuntimeError("Invalid JP tag")
    assets = [directory / "DroidDeck-Patch.apk"]
    if not all(path.is_file() and path.stat().st_size for path in assets):
        raise RuntimeError("Release assets are missing")
    manifest = json.loads((directory / "jp-build.json").read_text())
    if (manifest["upstream"] != plan["lock"]["base"] or manifest["manager"] != manager
            or manifest["version_code"] != plan["version_code"]):
        raise RuntimeError("Build provenance differs from this release plan")
    upstream = plan["lock"]["upstream_release"]["tag_name"]
    with assets[0].open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    checksum = directory / "SHA256SUMS"
    checksum.write_text(digest + "  DroidDeck-Patch.apk\n", encoding="utf-8")
    assets.append(checksum)
    notes = directory / "release-notes.md"
    notes.write_text(
        "- 本家リリース: " + upstream + "\n"
        "- 本家コミット: " + manifest["upstream"] + "\n"
        "APK SHA-256: `" + digest + "`\n\n<!-- jp-source: " + hashlib.sha256(manager.encode()).hexdigest() + " -->\n", encoding="utf-8")
    # The workflow checks out an immutable source commit before building.
    target = api("repos/" + repository + "/commits/HEAD")["sha"]
    existing = run("gh", "release", "view", tag, "--repo", repository, "--json", "isDraft,assets,body", check=False)
    previous_assets = []
    if existing.returncode:
        if "not found" not in existing.stderr.lower() and "404" not in existing.stderr:
            raise RuntimeError(existing.stderr)
        run("gh", "release", "create", tag, "--repo", repository, "--draft",
            "--target", target, "--title", tag,
            "--notes-file", str(notes))
    else:
        info = json.loads(existing.stdout)
        previous_assets = info.get("assets", [])
        proton = re.search(r"<!-- proton-source: [0-9a-f]{64} -->", info.get("body", ""))
        if proton:
            notes.write_text(notes.read_text().rstrip() + "\n\n" + proton.group() + "\n", encoding="utf-8")
        if not info["isDraft"]:
            # Hide the release while replacing the APK.
            run("gh", "release", "edit", tag, "--repo", repository, "--draft")
    run("gh", "release", "upload", tag, "--repo", repository,
        *[str(path) for path in assets], "--clobber")
    # These legacy assets describe an older APK; current source is linked above.
    # The by-tag REST endpoint does not resolve draft releases. Use the
    # draft-aware CLI's initial asset list rather than querying that endpoint.
    for asset in previous_assets:
        if asset["name"] in ("DroidDeck-Patch-source.tar.gz", "jp-build.json"):
            run("gh", "release", "delete-asset", tag, asset["name"],
                "--repo", repository, "--yes")
    run("gh", "release", "edit", tag, "--repo", repository,
        "--title", tag, "--notes-file", str(notes),
        "--draft=false", "--latest")
    return tag


def record_success(repository, manager, plan):
    if not plan["upstream_changed"]:
        return False
    endpoint = "repos/" + repository
    current = api(endpoint + "/git/ref/heads/main")["object"]["sha"]
    if current != manager:
        print("main advanced while building; leave its lock unchanged")
        return False
    remote = api(endpoint + "/contents/upstream.lock.json?ref=main")
    original = json.loads(base64.b64decode(remote["content"]).decode())
    if original != plan["original_lock"]:
        raise RuntimeError("Upstream lock changed; refusing to overwrite it")
    payload = {
        "message": "Track tested upstream release " + plan["lock"]["upstream_release"]["tag_name"],
        "branch": "main", "sha": remote["sha"],
        "content": base64.b64encode((json.dumps(plan["lock"], indent=2) + "\n").encode()).decode(),
    }
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "update.json"
        path.write_text(json.dumps(payload))
        run("gh", "api", endpoint + "/contents/upstream.lock.json",
            "--method", "PUT", "--input", str(path))
    # Workflow publication is manual; recording a lock never starts another build.
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manager", required=True)
    parser.add_argument("--plan", type=Path, default=Path("release-plan.json"))
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--release-repository")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish-only", action="store_true")
    mode.add_argument("--record-only", action="store_true")
    args = parser.parse_args()
    if args.publish_only and not args.release_repository:
        parser.error("--publish-only requires an explicit --release-repository")
    plan = json.loads(args.plan.read_text())
    if args.publish_only:
        publish(args.release_repository, args.manager, plan, args.directory)
    else:
        record_success(args.repository, args.manager, plan)



if __name__ == "__main__":
    main()
