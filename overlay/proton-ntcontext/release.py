"""Patch a public DroidDeck-Proton ARM64 build and attach it to the current DroidDeck release."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
REPOSITORY = "Droid-Deck/DroidDeck-Proton"
TAG = re.compile(r"DroidDeck-Proton-([0-9]+)-([0-9]+(?:\.[0-9]+)*)\Z")
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
        raise RuntimeError("Unexpected DroidDeck-Proton build name")
    return match.groups()


def build_info(tag):
    version(tag)
    name = tag + "-aarch64.build-info.json"
    with tempfile.TemporaryDirectory() as folder:
        run("gh", "release", "download", tag, "--repo", REPOSITORY,
            "--pattern", name, "--dir", folder)
        return json.loads((Path(folder) / name).read_text())


def resolve_source(tag):
    # Release tags can move after a build; use the published build's exact commit.
    info = build_info(tag)
    commit = info.get("banner_proton_commit", "")
    if info.get("name") != tag + "-aarch64" or not SHA.fullmatch(commit):
        raise RuntimeError("Invalid DroidDeck-Proton build provenance")
    endpoint = "repos/" + REPOSITORY
    tree = api(endpoint + "/git/trees/" + commit)
    commits = {}
    for name in ("wine", "dxvk", "vkd3d-proton"):
        entries = [entry for entry in tree["tree"] if entry["path"] == name
                   and entry["mode"] == "160000" and entry["type"] == "commit"
                   and SHA.fullmatch(entry["sha"])]
        if tree.get("truncated") or len(entries) != 1:
            raise RuntimeError("Cannot resolve DroidDeck-Proton " + name + " submodule")
        commits[name] = entries[0]["sha"]
    makefile = api(endpoint + "/contents/Makefile.in?ref=" + commit)
    sdk = arm64_sdk(base64.b64decode(makefile["content"]).decode())
    if info.get("proton_wine_commit") != commits["wine"] or info.get("sdk_image") != sdk:
        raise RuntimeError("DroidDeck-Proton build provenance differs from source")
    return commit, commits["wine"], commits["dxvk"], commits["vkd3d-proton"], sdk


def arm64_sdk(makefile):
    blocks = re.findall(r"(?m)^else ifeq \(\$\(TARGET_ARCH\),arm64\)\n(.*?)(?=^else|^endif)",
                        makefile, re.S)
    images = [image for block in blocks
              for image in re.findall(r"(?m)^\s*STEAMRT_IMAGE\s*\?=\s*(\S+)\s*$", block)]
    if len(images) != 1 or not re.fullmatch(
            r"registry\.gitlab\.steamos\.cloud/proton/[A-Za-z0-9._/-]+/sdk/arm64-llvm:[A-Za-z0-9._-]+",
            images[0]):
        raise RuntimeError("Proton ARM64 SDK definition changed")
    return images[0]


def candidate_lock(lock, upstream=None, resolve=resolve_source):
    if upstream is None:
        raise RuntimeError("A published DroidDeck-Proton ARM64 release is required")
    tag = upstream["tag_name"]
    if not TAG.fullmatch(tag):
        raise RuntimeError("Unexpected DroidDeck-Proton release tag")
    name = tag
    assets = [asset for asset in upstream.get("assets", []) if asset["name"] == name + "-aarch64.tar.gz"]
    if len(assets) != 1:
        raise RuntimeError("Expected exactly one published ARM64 archive")
    asset = assets[0]
    url = "https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/" + asset["name"]
    digest = asset.get("digest") or ""
    if asset["browser_download_url"] != url or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Published ARM64 archive URL or SHA256 is unavailable")
    proton, wine, dxvk, vkd3d, sdk = resolve(tag)
    if not all(SHA.fullmatch(value) for value in (proton, wine, dxvk, vkd3d)):
        raise RuntimeError("Invalid DroidDeck-Proton source commit")
    return dict(proton_repository="https://github.com/" + REPOSITORY + ".git",
                proton_branch=tag, source_tag=tag,
                proton_commit=proton, wine_commit=wine, dxvk_commit=dxvk, vkd3d_commit=vkd3d,
                base_name=name, base_directory="DroidDeck-Proton " + tag.removeprefix("DroidDeck-Proton-"),
                build_name=name + "-droiddeck",
                sdk_image=sdk, base_sha256=digest[7:], base_archive=url)


def latest_arm_release(releases):
    usable = [item for item in releases if not item.get("draft") and not item.get("prerelease")
              and item.get("published_at") and TAG.fullmatch(item["tag_name"])
              and any(asset["name"] == item["tag_name"] + "-aarch64.tar.gz"
                      for asset in item.get("assets", []))]
    if not usable:
        raise RuntimeError("No published DroidDeck-Proton ARM64 release")
    return max(usable, key=lambda item: tuple(map(int, re.findall(r"[0-9]+", item["tag_name"]))))


def source_identity(lock):
    digest = hashlib.sha256(json.dumps(lock, sort_keys=True).encode())
    files = sorted(path for path in ROOT.iterdir()
                   if path.is_file() and path.name != "build.lock.json")
    files += [ROOT.parents[1] / ".github/workflows/proton.yml",
              ROOT.parents[1] / "tests/test_proton_ntcontext.py",
              ROOT.parents[1] / "tests/test_vkd3d_low_memory.py"]
    files += sorted(path for path in (ROOT.parent / "vkd3d-low-memory").iterdir() if path.is_file())
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return "<!-- proton-source: " + digest.hexdigest() + " -->"


def asset_names(lock):
    return [runpy.run_path(str(ROOT / "asset_name.py"))["archive_name"](lock)]


def existing_release(repository, tag):
    result = run("gh", "release", "view", tag, "--repo", repository,
                 "--json", "isDraft,assets,body", check=False)
    if result.returncode:
        if not any(error in result.stderr.lower() for error in ("not found", "404")):
            raise RuntimeError(result.stderr)
        return None
    return json.loads(result.stdout)


def current_droiddeck_release(repository):
    releases = []
    page = 1
    while True:
        batch = api("repos/" + repository + "/releases?per_page=100&page=" + str(page))
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    candidates = [item for item in releases
                  if not item.get("draft") and not item.get("prerelease")
                  and item.get("published_at")
                  and any(asset["name"] == "DroidDeck-Patch.apk"
                          for asset in item.get("assets", []))]
    if not candidates:
        raise RuntimeError("No published DroidDeck APK release; Proton must not create a release")
    return max(candidates, key=lambda item: item["published_at"])


def proton_notes(body, marker):
    # The visible release notes contain only the three APK fields.
    fields = [line for line in body.splitlines()
              if line.startswith(("- 本家リリース:", "- 本家コミット:", "APK SHA-256:"))]
    if len(fields) != 3:
        raise RuntimeError("DroidDeck release notes are missing the three APK fields")
    apk = re.search(r"<!-- jp-source: [0-9a-f]{64} -->", body)
    return "\n".join(fields) + "\n\n" + (apk.group() + "\n" if apk else "") + marker + "\n"


def make_plan(lock, release, event, existing=None, resolve=resolve_source, target_tag=None):
    candidate = candidate_lock(lock, release, resolve)
    marker = source_identity(candidate)
    complete = (existing is not None and not existing["isDraft"]
                and set(asset_names(candidate)) <= {asset["name"] for asset in existing["assets"]}
                and marker in existing["body"])
    return {"build": event in ("push", "workflow_dispatch") or not complete or candidate != lock,
            "tag": target_tag, "lock": candidate, "original_lock": lock,
            "source_marker": marker}


def plan(repository, event, output):
    lock = json.loads((ROOT / "build.lock.json").read_text())
    target = current_droiddeck_release(repository)
    existing = existing_release(repository, target["tag_name"])
    releases = api("repos/" + REPOSITORY + "/releases?per_page=100")
    upstream = latest_arm_release(releases)
    result = make_plan(lock, upstream, event, existing, target_tag=target["tag_name"])
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.with_name("candidate-proton.lock.json").write_text(json.dumps(result["lock"], indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as destination:
            destination.write("build=" + str(result["build"]).lower() + "\n")
            destination.write("tag=" + result["tag"] + "\n")
    print("DroidDeck-Proton:", result["lock"]["base_name"], "| build:", result["build"])


def publish(repository, manager, plan, directory):
    lock = plan["lock"]
    if lock["build_name"] != lock["base_name"] + "-droiddeck":
        raise RuntimeError("Unexpected Proton build name")
    version(lock["base_name"])
    names = asset_names(lock)
    assets = [directory / name for name in names]
    if not all(path.is_file() and path.stat().st_size for path in assets):
        raise RuntimeError("Proton release assets are missing")
    manifest = json.loads((directory / "proton-build.json").read_text())
    if manifest.get("manager_commit") != manager or any(manifest.get(key) != value for key, value in lock.items()):
        raise RuntimeError("Proton build provenance differs from release plan")
    if (manifest.get("dll_profile") != "stock" or manifest.get("replaced_dlls") != []
            or manifest.get("features") != [] or manifest.get("patches") != []):
        raise RuntimeError("Only stock-DLL recovery builds may be published")
    for original, installed in (("original_ntdll_sha256", "rebuilt_ntdll_sha256"),
                                ("original_dxvk_dxgi_sha256", "rebuilt_dxvk_dxgi_sha256"),
                                ("original_vkd3d_core_sha256", "rebuilt_vkd3d_core_sha256")):
        if not re.fullmatch(r"[0-9a-f]{64}", manifest.get(original, "")) or manifest[original] != manifest.get(installed):
            raise RuntimeError("Public Proton must preserve stock DLL hashes")
    expected = {}
    for path in (assets[0], directory / (lock["build_name"] + "-wine-source.tar.xz")):
        with path.open("rb") as archive:
            expected[path.name] = hashlib.file_digest(archive, "sha256").hexdigest()
    checksums = dict((name, digest) for digest, name in
                     (line.split() for line in (directory / "SHA256SUMS").read_text().splitlines()))
    if checksums != expected:
        raise RuntimeError("Proton release archive checksum mismatch")
    target = current_droiddeck_release(repository)
    tag = target["tag_name"]
    existing = existing_release(repository, tag)
    if existing is None or existing["isDraft"] or not any(
            asset["name"] == "DroidDeck-Patch.apk" for asset in existing["assets"]):
        raise RuntimeError("DroidDeck release is unavailable; refusing to create or publish a release")
    with tempfile.TemporaryDirectory() as folder:
        notes = Path(folder) / "notes.md"
        notes.write_text(proton_notes(existing["body"], plan["source_marker"]))
        run("gh", "release", "upload", tag, "--repo", repository,
            *map(str, assets), "--clobber")
        # Preserve the APK release's title, publication state and latest status.
        run("gh", "release", "edit", tag, "--repo", repository,
            "--notes-file", str(notes))
        # Remove only obsolete patched Proton binaries after the new assets succeed.
        for asset in existing["assets"]:
            name = asset["name"]
            if name not in names and (name in ("Proton-SHA256SUMS", "proton-build.json", "signal-arm64ec-disassembly.txt")
                    or re.fullmatch(r"DroidDeck-Proton-[0-9]+-[0-9]+(?:\.[0-9]+)*-droiddeck-aarch64\.tar\.gz", name)
                    or re.fullmatch(r"GE-Proton[0-9]+-[0-9]+-(?:ntcontext-fix|droiddeck)-(?:aarch64\.tar\.gz|wine-source\.tar\.xz)", name)
                    or re.fullmatch(r"proton-plop-[0-9]+\.[0-9]+-[0-9]{8}-arm64-droiddeck-(?:aarch64\.tar\.gz|wine-source\.tar\.xz)", name)
                    or re.fullmatch(r"proton-plop-[0-9]+\.[0-9]+-droiddeck-arm64\.tar\.gz", name)
                    or re.fullmatch(r"Proton-Experimental-[0-9]+\.[0-9]+-[0-9a-f]{12}-droiddeck-(?:aarch64\.tar\.gz|wine-source\.tar\.xz)", name)):
                run("gh", "release", "delete-asset", tag, name, "--repo", repository, "--yes")



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
    payload = {"message": "Track built Proton DroidDeck-Proton " + plan["lock"]["base_name"],
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
    parser.add_argument("--event", choices=("push", "schedule", "workflow_dispatch", "workflow_run"))
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



