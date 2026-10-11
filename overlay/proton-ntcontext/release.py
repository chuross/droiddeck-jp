"""Discover, publish, and record tested ARM64 GE releases independently of APKs."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
REPOSITORY = "GloriousEggroll/proton-ge-custom"
TAG = re.compile(r"GE-Proton([0-9]+)-([0-9]+)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


def api(endpoint, *args):
    return json.loads(run("gh", "api", endpoint, *args).stdout)


def version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise RuntimeError("Unexpected GE release tag")
    return tuple(map(int, match.groups()))


def latest_arm_release(releases):
    stable = [
        release for release in releases
        if not release.get("draft") and not release.get("prerelease")
        and release.get("published_at") and TAG.fullmatch(release["tag_name"])
        and any(asset["name"] == release["tag_name"] + "-aarch64.tar.gz"
                for asset in release.get("assets", []))
    ]
    if not stable:
        raise RuntimeError("No stable GE release containing an ARM64 archive")
    return max(stable, key=lambda release: version(release["tag_name"]))


def resolve_source(tag):
    endpoint = "repos/" + REPOSITORY
    ref = api(endpoint + "/git/ref/tags/" + tag)["object"]
    for _ in range(5):
        if ref["type"] == "commit":
            break
        if ref["type"] != "tag":
            raise RuntimeError("Unexpected GE tag object")
        ref = api(endpoint + "/git/tags/" + ref["sha"])["object"]
    if ref["type"] != "commit" or not SHA.fullmatch(ref["sha"]):
        raise RuntimeError("Cannot resolve exact GE release commit")
    tree = api(endpoint + "/git/trees/" + ref["sha"])
    dxvk = [entry for entry in tree["tree"]
            if entry["path"] == "dxvk" and entry["mode"] == "160000"
            and entry["type"] == "commit" and SHA.fullmatch(entry["sha"])]
    if tree.get("truncated") or len(dxvk) != 1:
        raise RuntimeError("Cannot resolve GE DXVK submodule")
    wine = [entry for entry in tree["tree"]
            if entry["path"] == "wine" and entry["mode"] == "160000"
            and entry["type"] == "commit" and SHA.fullmatch(entry["sha"])]
    if tree.get("truncated") or len(wine) != 1:
        raise RuntimeError("Cannot resolve GE Wine submodule")
    makefile = api(endpoint + "/contents/Makefile.in?ref=" + ref["sha"])
    sdk = arm64_sdk(base64.b64decode(makefile["content"]).decode())
    return ref["sha"], wine[0]["sha"], dxvk[0]["sha"], sdk


def arm64_sdk(makefile):
    blocks = re.findall(r"(?m)^else ifeq \(\$\(TARGET_ARCH\),arm64\)\n(.*?)(?=^else|^endif)",
                        makefile, re.S)
    images = [image for block in blocks
              for image in re.findall(r"(?m)^\s*STEAMRT_IMAGE\s*\?=\s*(\S+)\s*$", block)]
    if len(images) != 1 or not re.fullmatch(
            r"registry\.gitlab\.steamos\.cloud/proton/[A-Za-z0-9._/-]+/sdk/arm64-llvm:[A-Za-z0-9._-]+",
            images[0]):
        raise RuntimeError("GE ARM64 SDK definition changed")
    return images[0]


def candidate_lock(lock, release, resolve=resolve_source):
    tag = release["tag_name"]
    if version(tag) < version(lock["base_name"]):
        raise RuntimeError("Refusing to roll back ProtonGE")
    archives = [asset for asset in release["assets"]
                if asset["name"] == tag + "-aarch64.tar.gz"]
    if len(archives) != 1:
        raise RuntimeError("Expected exactly one ARM64 GE archive")
    archive = archives[0]
    url = ("https://github.com/" + REPOSITORY + "/releases/download/"
           + tag + "/" + tag + "-aarch64.tar.gz")
    digest = archive.get("digest") or ""
    if archive["browser_download_url"] != url or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Official ARM64 archive URL or SHA256 is unavailable")
    proton, wine, dxvk, sdk = resolve(tag)
    candidate = dict(lock, proton_repository="https://github.com/" + REPOSITORY + ".git",
                     proton_commit=proton, wine_commit=wine, dxvk_commit=dxvk, base_archive=url,
                     base_sha256=digest[7:], base_name=tag, build_name=tag + "-droiddeck",
                     sdk_image=sdk)
    return candidate


def source_identity(lock):
    digest = hashlib.sha256(json.dumps(lock, sort_keys=True).encode())
    files = sorted(path for path in ROOT.iterdir()
                   if path.is_file() and path.name != "build.lock.json")
    files += [ROOT.parents[1] / ".github/workflows/proton.yml",
              ROOT.parents[1] / "tests/test_proton_ntcontext.py"]
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return "<!-- proton-source: " + digest.hexdigest() + " -->"


def asset_names(lock):
    name = lock["build_name"]
    return [name + "-aarch64.tar.gz", name + "-wine-source.tar.xz",
            "SHA256SUMS", "proton-build.json", "signal-arm64ec-disassembly.txt"]


def existing_release(repository, tag):
    result = run("gh", "release", "view", tag, "--repo", repository,
                 "--json", "isDraft,assets,body", check=False)
    if result.returncode:
        if not any(error in result.stderr.lower() for error in ("not found", "404")):
            raise RuntimeError(result.stderr)
        return None
    return json.loads(result.stdout)


def make_plan(lock, release, event, existing=None, resolve=resolve_source):
    candidate = candidate_lock(lock, release, resolve)
    marker = source_identity(candidate)
    complete = (existing is not None and not existing["isDraft"]
                and set(asset_names(candidate)) <= {asset["name"] for asset in existing["assets"]}
                and marker in existing["body"])
    return {"build": event in ("push", "workflow_dispatch") or not complete or candidate != lock,
            "tag": candidate["build_name"], "lock": candidate, "original_lock": lock,
            "source_marker": marker}


def plan(repository, event, output):
    releases = []
    page = 1
    while True:
        batch = api("repos/" + REPOSITORY + "/releases?per_page=100&page=" + str(page))
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    release = latest_arm_release(releases)
    lock = json.loads((ROOT / "build.lock.json").read_text())
    existing = existing_release(repository, release["tag_name"] + "-droiddeck")
    result = make_plan(lock, release, event, existing)
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.with_name("candidate-proton.lock.json").write_text(json.dumps(result["lock"], indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as destination:
            destination.write("build=" + str(result["build"]).lower() + "\n")
            destination.write("tag=" + result["tag"] + "\n")
    print("ProtonGE:", result["lock"]["base_name"], "| build:", result["build"])


def publish(repository, manager, plan, directory):
    lock = plan["lock"]
    if plan["tag"] != lock["build_name"] or lock["build_name"] != lock["base_name"] + "-droiddeck":
        raise RuntimeError("Release tag differs from Proton build")
    version(lock["base_name"])
    assets = [directory / name for name in asset_names(lock)]
    if not all(path.is_file() and path.stat().st_size for path in assets):
        raise RuntimeError("Proton release assets are missing")
    manifest = json.loads((directory / "proton-build.json").read_text())
    if manifest.get("manager_commit") != manager or any(manifest.get(key) != value for key, value in lock.items()):
        raise RuntimeError("Proton build provenance differs from release plan")
    expected = {}
    for path in assets[:2]:
        with path.open("rb") as archive:
            expected[path.name] = hashlib.file_digest(archive, "sha256").hexdigest()
    checksums = dict((name, digest) for digest, name in
                     (line.split() for line in (directory / "SHA256SUMS").read_text().splitlines()))
    if checksums != expected:
        raise RuntimeError("Proton release archive checksum mismatch")
    with tempfile.TemporaryDirectory() as folder:
        notes = Path(folder) / "notes.md"
        notes.write_text("- ProtonGE: [" + lock["base_name"] + "](https://github.com/" + REPOSITORY
                         + "/releases/tag/" + lock["base_name"] + ")\n"
                         + "- ARM64EC NtGetContextThread(NULL) fix\n"
                         + "- DXVK DXGI reported memory budget cap via existing maxDeviceMemory/maxSharedMemory options\n"
                         + "- Applies to the normal D3D12/VKD3D-Proton DXGI path; not a RAM allocation limit\n"
                         + "- DXVK commit: " + lock["dxvk_commit"] + "\n"
                         + "- Source commit: " + lock["proton_commit"] + "\n\n"
                         + plan["source_marker"] + "\n")
        existing = existing_release(repository, plan["tag"])
        if existing is None:
            run("gh", "release", "create", plan["tag"], "--repo", repository, "--draft",
                "--target", manager, "--title", plan["tag"], "--notes-file", str(notes))
        elif not existing["isDraft"]:
            run("gh", "release", "edit", plan["tag"], "--repo", repository, "--draft")
        run("gh", "release", "upload", plan["tag"], "--repo", repository,
            *map(str, assets), "--clobber")
        run("gh", "release", "edit", plan["tag"], "--repo", repository,
            "--title", plan["tag"], "--notes-file", str(notes), "--draft=false", "--latest=false")


def record(repository, manager, plan):
    if plan["lock"] == plan["original_lock"]:
        return False
    endpoint = "repos/" + repository
    if api(endpoint + "/git/ref/heads/main")["object"]["sha"] != manager:
        print("main advanced while building; leave Proton lock unchanged")
        return False
    remote = api(endpoint + "/contents/overlay/proton-ntcontext/build.lock.json?ref=main")
    if json.loads(base64.b64decode(remote["content"])) != plan["original_lock"]:
        raise RuntimeError("Proton lock changed; refusing to overwrite it")
    payload = {"message": "Track tested ProtonGE " + plan["lock"]["base_name"],
               "branch": "main", "sha": remote["sha"],
               "content": base64.b64encode((json.dumps(plan["lock"], indent=2) + "\n").encode()).decode()}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "update.json"
        path.write_text(json.dumps(payload))
        run("gh", "api", endpoint + "/contents/overlay/proton-ntcontext/build.lock.json",
            "--method", "PUT", "--input", str(path))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "publish"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--event", choices=("push", "schedule", "workflow_dispatch"))
    parser.add_argument("--manager")
    parser.add_argument("--plan", type=Path, default=Path("proton-release-plan.json"))
    parser.add_argument("--directory", type=Path, default=Path("build/proton-ntcontext/dist"))
    args = parser.parse_args()
    if args.action == "plan":
        if not args.event:
            parser.error("plan requires --event")
        plan(args.repository, args.event, args.plan)
    else:
        if not args.manager or not SHA.fullmatch(args.manager):
            parser.error("publish requires an exact --manager SHA")
        result = json.loads(args.plan.read_text())
        publish(args.repository, args.manager, result, args.directory)
        record(args.repository, args.manager, result)


if __name__ == "__main__":
    main()
