"""Official release provenance, disposable recipe preparation and packaging."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from process_heap_usage import DEVICE, equivalent

ROOT = Path(__file__).resolve().parent
OFFICIAL = "Droid-Deck/Drivers-CI"
RECIPE = "https://github.com/Droid-Deck/Drivers.git"
MESA = "https://gitlab.freedesktop.org/mesa/mesa.git"


def run(*args, **kwargs):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError("Turnip source/build command failed (upstream drift or tool failure):\n" + result.stderr + result.stdout)
    return result.stdout.strip()


def api(endpoint):
    return json.loads(run("gh", "api", endpoint))


def releases(repository):
    result = []
    page = 1
    while True:
        batch = api(f"repos/{repository}/releases?per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def official_release(items):
    stable = [r for r in items if not r.get("draft") and not r.get("prerelease") and r.get("published_at")
              and re.fullmatch(r"DD-Turnip-v\d+\.\d+\.\d+", r["tag_name"])]
    if not stable:
        raise RuntimeError("No official stable DD-Turnip release")
    return max(stable, key=lambda r: tuple(map(int, r["tag_name"].removeprefix("DD-Turnip-v").split('.'))))


def provenance(release):
    body = release["body"]
    recipe = set(re.findall(r'https://github.com/Droid-Deck/Drivers/commit/([0-9a-f]{40})(?![0-9a-f])', body))
    # Release notes also link historic Mesa commits: restrict resolution to the Mesa header.
    header = next((line for line in body.splitlines() if line.startswith("**Mesa:**")), "")
    mesa = set(re.findall(r'https://gitlab.freedesktop.org/mesa/mesa/-/commit/([0-9a-f]{40})(?![0-9a-f])', header))
    if len(recipe) != 1 or len(mesa) != 1:
        raise RuntimeError("Official recipe/Mesa provenance missing or ambiguous")
    tag = release["tag_name"]
    required = {tag + suffix for suffix in (".zip", ".zip.sha256", "-source.tar.xz")}
    if not required <= {a["name"] for a in release["assets"]}:
        raise RuntimeError("Official corresponding source/bundle missing")
    return {"official_repository": OFFICIAL, "official_release_id": release["id"], "tag": tag,
            "recipe_commit": recipe.pop(), "mesa_commit": mesa.pop()}


def source_identity(plan):
    # Excludes source.patch, tests and release metadata: only the driver delta matters.
    payload = {k: plan[k] for k in ("official_repository", "official_release_id", "tag", "recipe_commit", "mesa_commit")}
    payload["overlay"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (ROOT / "process_heap_usage.py", ROOT / "process-heap-usage.patch", ROOT / "driver.py", ROOT / "build.sh")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def checkout(repository, commit, target):
    run("git", "init", "-q", target)
    run("git", "-C", target, "fetch", "--depth=1", repository, commit)
    run("git", "-C", target, "checkout", "--detach", "FETCH_HEAD")
    if run("git", "-C", target, "rev-parse", "HEAD") != commit:
        raise RuntimeError("Source identity mismatch")


def inspect_recipe(recipe, mesa):
    """Execute the official patch series on both throwaway Mesa trees, then inspect its result.

    This detects equivalent patches regardless of name or whether they are Python or diffs.
    A partial implementation or target disagreement is a failure, never a successful skip.
    """
    results = []
    for target in ("android", "linux"):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary) / "mesa"
            run("git", "clone", "-q", "--shared", mesa, tree)
            run(sys.executable, recipe / "scripts/apply_patches.py", target, tree)
            results.append(equivalent((tree / DEVICE).read_text()))
    if any(results) and not all(results):
        raise RuntimeError("partial upstream fix: Android/Linux disagree")
    return all(results)


def prepare_recipe(recipe, plan):
    if run("git", "-C", recipe, "rev-parse", "HEAD") != plan["recipe_commit"]:
        raise RuntimeError("Recipe differs from official plan")
    if run("git", "-C", recipe, "status", "--porcelain"):
        raise RuntimeError("Recipe must start clean")
    # Only the injected series entry changes compilation; the notice below links its source.
    target = recipe / "patches/scripts"
    for name in ("process_heap_usage.py", "process-heap-usage.patch"):
        if (target / name).exists():
            raise RuntimeError("Recipe filename collision; review upstream drift")
        shutil.copy2(ROOT / name, target / name)
    packaging = recipe / "scripts/package_bundle.py"
    text = packaging.read_text()
    old = "https://github.com/Droid-Deck/Drivers-CI/releases/tag"
    notice = "published with these drivers at {CI_URL}/{tag}"
    if text.count(old) != 1 or text.count(notice) != 1:
        raise RuntimeError("Source notice upstream drift")
    packaging.write_text(text.replace(old, "https://github.com/chuross/droiddeck-patch/releases").replace(
        notice, "included in the APK assets/turnip-process-heap-usage/ directory; APK releases: {CI_URL}"))
    series = recipe / "patches/series.json"
    entries = json.loads(series.read_text())
    entries.append({"file": "scripts/process_heap_usage.py", "targets": ["android", "linux"],
                    "summary": "Report process-wide heapUsage (temporary droiddeck-patch overlay)"})
    series.write_text(json.dumps(entries, indent=2) + "\n")
    run("git", "-C", recipe, "add", "patches", "scripts/package_bundle.py")
    run("git", "-C", recipe, "-c", "user.name=DroidDeck Patch builder", "-c", "user.email=droiddeck-patch@users.noreply.github.com",
        "commit", "-m", "Temporarily report process-wide Turnip heapUsage")
    return run("git", "-C", recipe, "rev-parse", "HEAD")


def official_ndk(recipe):
    workflow = recipe / ".github/workflows/release.yml"
    if not workflow.is_file():
        raise RuntimeError("Official NDK recipe workflow drift")
    versions = re.findall(r"(?m)^\s+ndk-version:\s*(r\d+[a-z])\s*$", workflow.read_text())
    if len(versions) != 1:
        raise RuntimeError("Official NDK version missing or ambiguous")
    return versions[0]


def plan_build(repository, work):
    work.mkdir(parents=True, exist_ok=False)
    plan = provenance(official_release(releases(OFFICIAL)))
    plan["identity"] = source_identity(plan)
    recipe, mesa = work / "drivers", work / "mesa"
    checkout(RECIPE, plan["recipe_commit"], recipe)
    checkout(MESA, plan["mesa_commit"], mesa)
    plan["ndk_version"] = official_ndk(recipe)
    unnecessary = inspect_recipe(recipe, mesa)
    plan["status"] = "overlay unnecessary: equivalent upstream fix detected" if unnecessary else "patch required"
    # Check every target with the added patch BEFORE authorizing a build.
    if not unnecessary:
        plan["patched_recipe_commit"] = prepare_recipe(recipe, plan)
        if not inspect_recipe(recipe, mesa):
            raise RuntimeError("Patched recipe lacks process-wide usage")
    plan["build"] = not unnecessary
    (work / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("build=" + str(plan["build"]).lower() + "\n")
            output.write("ndk_version=" + plan["ndk_version"] + "\n")
    print(plan["status"] + "; official source remains available: " + OFFICIAL + "/" + plan["tag"])
    return plan


def package(work):
    plan = json.loads((work / "plan.json").read_text())
    recipe, mesa = work / "drivers", work / "mesa"
    if run("git", "-C", recipe, "rev-parse", "HEAD") != plan["patched_recipe_commit"] or run("git", "-C", recipe, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Patched recipe changed before packaging")
    run(sys.executable, recipe / "scripts/package_bundle.py", "--version", plan["tag"].removeprefix("DD-Turnip-v"),
        "--mesa-commit", plan["mesa_commit"], "--mesa-version", (mesa / "VERSION").read_text().strip(),
        "--mesa-src", mesa, "--recipe", plan["patched_recipe_commit"], "--out", recipe / "out", "--dist", work / "dist")
    verify_source(work / "dist" / (plan["tag"] + "-source.tar.xz"), plan)


def verify_source(archive, plan):
    import tarfile
    prefix = plan["tag"] + "-source/drivers/patches/"
    with tarfile.open(archive) as packed:
        for name in ("process_heap_usage.py", "process-heap-usage.patch"):
            member = packed.extractfile(prefix + "scripts/" + name)
            if member is None or member.read() != (ROOT / name).read_bytes():
                raise RuntimeError("Corresponding Source omits the applied overlay")
        entries = json.load(packed.extractfile(prefix + "series.json"))
        if not any(e["file"] == "scripts/process_heap_usage.py" and set(e["targets"]) == {"android", "linux"} for e in entries):
            raise RuntimeError("Corresponding Source lacks both target registrations")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "package"))
    parser.add_argument("--repository", default="chuross/droiddeck-patch")
    parser.add_argument("--work", type=Path, default=Path("build/turnip-process-heap-usage"))
    args = parser.parse_args()
    if args.action == "plan":
        plan_build(args.repository, args.work.resolve())
    else:
        package(args.work.resolve())
