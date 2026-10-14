"""Preserve the pinned Proton release's graphics patches before custom patches."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def targets(patch):
    names = re.findall(r"(?m)^\+\+\+ b/([^\s]+)", patch.read_text())
    if not names or any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
        raise ValueError("unexpected upstream patch targets")
    return names


def plan(source):
    script = (source / "patches/protonprep-valve-staging.sh").read_text()
    result = []
    for component in ("dxvk", "vkd3d-proton"):
        blocks = re.findall(r"(?ms)^\s*pushd " + component + r"\s*\n(.*?)^\s*popd\s*$", script)
        if len(blocks) != 1:
            raise ValueError("upstream preparation changed: " + component)
        patches = []
        for line in blocks[0].splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "echo ")) or line in ("git reset --hard HEAD", "git clean -xdf"):
                continue
            args = shlex.split(line)
            if len(args) == 2 and args[0] == "apply_patch":
                patches.append((source / component / args[1]).resolve())
            elif len(args) == 4 and args[:3] == ["patch", "-Np1", "<"]:
                patches.append((source / component / args[3]).resolve())
            elif len(args) == 2 and args[0] == "apply_all_in_dir":
                patches.extend(sorted((source / component / args[1]).resolve().glob("*.patch")))
            else:
                raise ValueError("unsupported upstream preparation command: " + line)
        if not patches:
            raise ValueError("upstream graphics patches are missing: " + component)
        for patch in patches:
            patch.relative_to((source / "patches").resolve())
            targets(patch)
            result.append({"component": component, "path": str(patch.relative_to(source.resolve())),
                           "sha256": digest(patch)})
    return result


def prepare(source, output):
    patches = plan(source)
    for component in ("dxvk", "vkd3d-proton"):
        status = subprocess.check_output(["git", "-C", str(source / component), "status", "--porcelain"], text=True)
        if status.strip():
            raise ValueError("upstream preparation requires a clean checkout: " + component)
    files = {}
    for patch in patches:
        path = source / patch["path"]
        directory = source / patch["component"]
        subprocess.run(["git", "-C", str(directory), "apply", "--check", str(path)], check=True)
        subprocess.run(["git", "-C", str(directory), "apply", str(path)], check=True)
        for name in targets(path):
            files[patch["component"] + "/" + name] = digest(directory / name)
    # Later patches can alter the same file; hash only the fully prepared result.
    files = {name: digest(source / name) for name in files}
    output.write_text(json.dumps({"patches": patches, "prepared_files": files}, indent=2) + "\n")
    print("Preserved upstream graphics patches:", len(patches))


def verify(source, evidence, custom):
    record = json.loads(evidence.read_text())
    if record["patches"] != plan(source):
        raise ValueError("upstream patch provenance changed")
    expected_files = {patch["component"] + "/" + name for patch in record["patches"]
                      for name in targets(source / patch["path"])}
    if set(record["prepared_files"]) != expected_files:
        raise ValueError("upstream prepared-file evidence is incomplete")
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        names = set(record["prepared_files"])
        for component, patch in custom.items():
            names.update(component + "/" + name for name in targets(patch))
        for name in names:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
        for component, patch in custom.items():
            subprocess.run(["git", "init", "-q", str(stage / component)], check=True)
            subprocess.run(["git", "-C", str(stage / component), "apply", "--reverse", str(patch)], check=True)
        for name, expected in record["prepared_files"].items():
            if digest(stage / name) != expected:
                raise ValueError("upstream graphics compatibility patch was lost: " + name)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.output)
