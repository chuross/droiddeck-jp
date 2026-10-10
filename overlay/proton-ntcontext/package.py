"""Replace only the rebuilt ARM64X PE ntdll in a checksummed GE release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

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
        raise ValueError("expected exactly one ARM64X ntdll in the GE release")
    return candidates.pop()


def write_preserving_mode(path, data):
    mode = path.stat().st_mode & 0o7777
    path.chmod(mode | 0o200)
    try:
        path.write_bytes(data)
    finally:
        path.chmod(mode)


def package(task, manager):
    lock = json.loads((ROOT / "build.lock.json").read_text())
    archive = task / "base.tar.gz"
    if sha256(archive) != lock["base_sha256"]:
        raise ValueError("base GE archive checksum mismatch")
    check_disassembly((task / "results/signal-arm64ec-disassembly.txt").read_text())
    unpacked = task / "unpacked"
    unpacked.mkdir()
    # Only this exact official release digest is extracted with tar.
    subprocess.run(["tar", "-xzf", str(archive), "-C", str(unpacked)], check=True)
    tree = base_tree(unpacked)
    original = original_ntdll(tree)
    rebuilt = task / "results/ntdll.dll"
    before, after = pe_interface(original), pe_interface(rebuilt)
    if before != after or not after["arm64x_metadata"] or "NtGetContextThread" not in after["exports"]:
        raise ValueError("rebuilt ARM64X ntdll does not match the original PE interface")
    original_hash, rebuilt_hash = sha256(original), sha256(rebuilt)
    if original_hash == rebuilt_hash:
        raise ValueError("rebuilt ntdll is unchanged")
    write_preserving_mode(original, rebuilt.read_bytes())
    name = lock["build_name"]
    compat = tree / "compatibilitytool.vdf"
    text = compat.read_text()
    if lock["base_name"] not in text:
        raise ValueError("base compatibility manifest changed")
    write_preserving_mode(compat, text.replace(lock["base_name"], name).encode())
    metadata = dict(lock, manager_commit=manager, sdk_digests=json.loads((task / "sdk-digests.json").read_text()),
                    original_ntdll_sha256=original_hash,
                    rebuilt_ntdll_sha256=rebuilt_hash, pe_export_count=len(after["exports"]),
                    fix="ARM64EC NtGetContextThread(NULL) returns STATUS_INVALID_PARAMETER")
    (tree / "droiddeck-ntcontext-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    renamed = unpacked / name
    tree.rename(renamed)
    dist = task / "dist"
    dist.mkdir()
    (dist / "proton-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(task / "results/signal-arm64ec-disassembly.txt", dist)
    subprocess.run(["tar", "-czf", str(dist / (name + "-aarch64.tar.gz")), "-C", str(unpacked), name], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--manager", required=True)
    args = parser.parse_args()
    package(args.task.resolve(), args.manager)
