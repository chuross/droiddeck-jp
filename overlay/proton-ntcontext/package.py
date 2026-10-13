"""Replace validated ARM64X DLLs in the public DroidDeck-Proton ARM64 build."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import shutil
import struct
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_interface(path):
    data = Path(path).read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("ntdll is not a PE image")
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid PE signature")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x20b:
        raise ValueError("expected PE32+ ntdll")
    sections = []
    for index in range(count):
        header = optional + optional_size + 40 * index
        virtual_size, address, raw_size, raw_offset = struct.unpack_from("<IIII", data, header + 8)
        sections.append((address, raw_size, raw_offset))

    def offset(rva, size=1):
        for address, raw_size, raw in sections:
            if address <= rva and rva + size <= address + raw_size:
                result = raw + rva - address
                if result + size <= len(data):
                    return result
        raise ValueError("PE RVA outside image")

    directory, length = struct.unpack_from("<II", data, optional + 112)
    export = offset(directory, 40)
    base, function_count, name_count, functions, names, ordinals = struct.unpack_from("<IIIIII", data, export + 16)
    if name_count > 10000 or function_count > 10000:
        raise ValueError("invalid export count")
    exports = {}
    for index in range(name_count):
        name_rva = struct.unpack_from("<I", data, offset(names + 4 * index, 4))[0]
        start = offset(name_rva)
        name = data[start:data.index(b"\0", start)].decode("ascii")
        ordinal = struct.unpack_from("<H", data, offset(ordinals + 2 * index, 2))[0]
        if ordinal >= function_count:
            raise ValueError("invalid export ordinal")
        exports[name] = base + ordinal
    config_rva, config_size = struct.unpack_from("<II", data, optional + 112 + 10 * 8)
    metadata = (struct.unpack_from("<Q", data, offset(config_rva, 208) + 200)[0]
                if config_size >= 208 else 0)
    return {"machine": machine, "exports": exports, "export_base": base,
            "function_count": function_count, "arm64x_metadata": bool(metadata)}


def check_disassembly(text):
    # LLVM may choose the COFF section symbol `.text` instead of the function
    # symbol. Its call relocation still identifies this wrapper unambiguously.
    functions = re.finditer(r"<([^>]+)>:\n(.*?)(?=\n[0-9a-f]+\s+<|\nDisassembly of section|\Z)", text, re.S)
    for function in functions:
        name, body = function.groups()
        named = name in ("NtGetContextThread", "#NtGetContextThread")
        relocated = re.search(r"\bIMAGE_REL_ARM64_BRANCH26\s+syscall_NtGetContextThread\b", body)
        if not named and not relocated:
            continue
        load = re.search(r"\b(?:ldr|ldur)\s+w\d+,\s*\[(x\d+),\s*#(?:0x30|48)\]", body)
        if load and re.search(r"\bcbz\s+" + load[1] + r"\s*,", body[:load.start()]):
            return
    raise ValueError("ARM64EC NULL branch before ContextFlags load was not found")


def base_tree(unpacked):
    trees = [path for path in unpacked.iterdir()
             if path.is_dir() and (path / "compatibilitytool.vdf").is_file()]
    if len(trees) != 1:
        raise ValueError("expected exactly one Proton compatibility tool")
    return trees[0]


def original_ntdll(tree):
    # ARM64 GE releases can use lib-arm64 rather than the x86 lib directory.
    candidates = {path.resolve() for path in tree.glob("files/lib*/wine/aarch64-windows/ntdll.dll")
                  if path.is_file()}
    if len(candidates) != 1:
        raise ValueError("expected exactly one ARM64X ntdll in the Proton build")
    return candidates.pop()


def dxvk_interface(path):
    try:
        interface = pe_interface(path)
    except (ValueError, struct.error, IndexError, UnicodeError) as error:
        raise ValueError("invalid DXVK DXGI PE image") from error
    if interface["machine"] != 0xaa64 or not interface["arm64x_metadata"]:
        raise ValueError("DXVK DXGI architecture mismatch: expected AA64 ARM64X")
    if "CreateDXGIFactory" not in interface["exports"]:
        raise ValueError("DXVK DXGI factory export is missing")
    return interface


def original_dxvk(tree):
    candidates = {path.resolve() for path in tree.glob("files/lib*/wine/dxvk/aarch64-windows/dxgi.dll")
                  if path.is_file()}
    if len(candidates) != 1:
        raise ValueError("expected exactly one ARM64X DXVK DXGI in Proton build")
    original = candidates.pop()
    if not original.is_relative_to(tree.resolve()):
        raise ValueError("DXVK target is outside Proton tree")
    dxvk_interface(original)
    return original


def rebuilt_dxvk(results):
    candidates = {path.resolve() for path in (results / "dxvk").rglob("dxgi.dll") if path.is_file()}
    if len(candidates) != 1:
        raise ValueError("expected exactly one rebuilt DXVK DXGI; missing or ambiguous output")
    rebuilt = candidates.pop()
    dxvk_interface(rebuilt)
    return rebuilt


def write_preserving_mode(path, data):
    mode = path.stat().st_mode & 0o7777
    path.chmod(mode | 0o200)
    try:
        path.write_bytes(data)
    finally:
        path.chmod(mode)

def import_layout(tree):
    importer = runpy.run_path(str(ROOT.parent / "proton-import/droiddeck-proton-import"))
    native = tree / "files/bin-arm64"
    if native.is_dir():
        directory = tree / "files/bin"
        directory.mkdir(exist_ok=True)
        for name in ("wine", "wineserver"):
            target = native / name
            if not importer["arm64_elf"](target):
                raise ValueError("DroidDeck-Proton native " + name + " is not ARM64")
            alias = directory / name
            if not alias.exists() and not alias.is_symlink():
                alias.symlink_to("../bin-arm64/" + name)
    importer["validate"](tree)
    return importer


def package(task, manager, lock_path=None):
    lock = json.loads((lock_path or ROOT / "build.lock.json").read_text())
    if lock["proton_repository"] != "https://github.com/Droid-Deck/DroidDeck-Proton.git":
        raise ValueError("expected DroidDeck-Proton source")
    for name, expected in (("", lock["proton_commit"]), ("wine", lock["wine_commit"]),
                           ("dxvk", lock["dxvk_commit"])):
        actual = subprocess.check_output(
            ["git", "-C", str(task / "proton" / name), "rev-parse", "HEAD"], text=True).strip()
        if actual != expected:
            raise ValueError("source commit differs from lock")
    check_disassembly((task / "results/signal-arm64ec-disassembly.txt").read_text())
    for name, patch in (("wine", "ntgetcontext-null.patch"), ("dxvk", "dxvk-memory-budget.patch")):
        evidence = (task / ("results/" + name + "-patches.diff")).read_text()
        if not evidence.strip():
            raise ValueError("patch evidence is missing")
        subprocess.run(["git", "-C", str(task / "proton" / name), "apply", "--reverse",
                        "--check", str(ROOT / patch)], check=True)
    archive = task / "base.tar.gz"
    if sha256(archive) != lock["base_sha256"]:
        raise ValueError("base archive checksum mismatch")
    staging = task / "unpacked"
    staging.mkdir()
    subprocess.run(["tar", "-xzf", str(archive), "--same-permissions", "-C", str(staging)], check=True)
    tree = base_tree(staging)
    if tree.name != lock["base_directory"]:
        raise ValueError("base archive build name differs from source plan")
    for name in ("proton", "toolmanifest.vdf", "LICENSE"):
        if not (tree / name).is_file():
            raise ValueError("incomplete Proton depot: " + name)
    ntdll = original_ntdll(tree)
    rebuilt = task / "results/ntdll.dll"
    interface = pe_interface(rebuilt)
    if interface != pe_interface(ntdll) or interface["machine"] != 0xaa64 or not interface["arm64x_metadata"] or "NtGetContextThread" not in interface["exports"]:
        raise ValueError("rebuilt ARM64X ntdll interface differs from stock")
    dxvk = original_dxvk(tree)
    rebuilt_graphics = rebuilt_dxvk(task / "results")
    if dxvk_interface(dxvk) != dxvk_interface(rebuilt_graphics):
        raise ValueError("rebuilt DXGI interface differs from stock")
    original_ntdll_hash, original_dxvk_hash = sha256(ntdll), sha256(dxvk)
    if original_ntdll_hash == sha256(rebuilt) or original_dxvk_hash == sha256(rebuilt_graphics):
        raise ValueError("rebuilt DLL is unchanged")
    # Both interfaces are validated before replacing either stock DLL.
    write_preserving_mode(ntdll, rebuilt.read_bytes())
    write_preserving_mode(dxvk, rebuilt_graphics.read_bytes())
    template = (task / "proton/compatibilitytool.vdf.template").read_text()
    if "##BUILD_NAME##" not in template or "##INTERNAL_TOOL_NAME##" not in template:
        raise ValueError("upstream compatibility template changed")
    (tree / "compatibilitytool.vdf").write_text(template.replace("##BUILD_NAME##", lock["build_name"]).replace("##INTERNAL_TOOL_NAME##", lock["build_name"]))
    sdk_digests = json.loads((task / "sdk-digests.json").read_text())
    if not sdk_digests:
        raise ValueError("SDK image digest is missing")
    metadata = dict(lock, manager_commit=manager, sdk_digests=sdk_digests,
                    features=["ntcontext-null-fix", "dxvk-dxgi-memory-budget-cap"],
                    patches=["ntgetcontext-null.patch", "dxvk-memory-budget.patch"],
                    original_ntdll_sha256=original_ntdll_hash, original_dxvk_dxgi_sha256=original_dxvk_hash,
                    rebuilt_ntdll_sha256=sha256(ntdll),
                    rebuilt_dxvk_dxgi_sha256=sha256(dxvk),
                    dxvk_dxgi_architecture="ARM64X",
                    dxvk_dxgi_archive_path=str(dxvk.relative_to(tree)),
                    fix="ARM64EC NtGetContextThread(NULL) returns STATUS_INVALID_PARAMETER")
    (tree / "droiddeck-ntcontext-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    dist = task / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "proton-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(task / "results/signal-arm64ec-disassembly.txt", dist)
    # Include exact patches, build inputs and source locations with the binary.
    source_info = tree / "droiddeck-source"
    source_info.mkdir()
    for name in ("build.lock.json", "apply.py", "build.sh", "build-inside.sh", "build-dxvk.sh", "dxvk-arm64ec.cross", "package.py",
                 "ntgetcontext-null.patch", "dxvk-memory-budget.patch"):
        shutil.copy2(lock_path if name == "build.lock.json" and lock_path else ROOT / name, source_info / name)
    shutil.copy2(task / "proton/LICENSE.proton", tree / "LICENSE.proton")
    shutil.copy2(dist / (lock["build_name"] + "-wine-source.tar.xz"), source_info / "source.tar.xz")
    packaged = staging / lock["build_name"]
    tree.rename(packaged)
    importer = import_layout(packaged)
    archive_name = runpy.run_path(str(ROOT / "asset_name.py"))["archive_name"](lock)
    archive_path = dist / archive_name
    subprocess.run(["tar", "-czf", str(archive_path),
                    "-C", str(staging), lock["build_name"]], check=True)
    # Apply the shipping APK's archive policy before the binary is published.
    with tarfile.open(archive_path, "r:gz") as archive:
        entries, _ = importer["plan"](archive, 32 * 1024 ** 3)
        for member, path in entries:
            if member.issym():
                (staging / str(path)).resolve().relative_to(packaged.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--manager", required=True)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    package(args.task.resolve(), args.manager, args.lock)


