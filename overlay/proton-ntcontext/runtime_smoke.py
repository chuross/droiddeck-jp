"""Inspect stock DLLs, then load the patched DLLs through stock ARM64 Wine."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from package import (base_tree, original_ntdll, original_dxvk, original_vkd3d,
                     pe_interface, sha256, write_preserving_mode)


def windows_path(path):
    return "Z:" + str(path.resolve()).replace("/", "\\")


def run_probe(task, tree, probe, targets, phase):
    prefix = task / ("smoke-prefix-" + phase)
    lib = targets["ntdll"].parents[2]
    binary = tree / "files/bin-arm64"
    if not binary.is_dir():
        binary = tree / "files/bin"
    config = task / "results/fex-smoke-config.json"
    config.write_text('{"Config": {}}\n')
    library_paths = [str(lib / arch) for arch in ("aarch64-linux-gnu", "x86_64-linux-gnu", "i386-linux-gnu")]
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all,+loaddll",
               WINESERVER=str(binary / "wineserver"),
               WINEDLLPATH=str(lib / "vkd3d") + ":" + str(lib / "wine"),
               LD_LIBRARY_PATH=":".join(library_paths) + ":" + os.environ.get("LD_LIBRARY_PATH", ""),
               WINEDLLOVERRIDES="dxgi,d3d12core=n;winemenubuilder.exe=d",
               FEX_DISKCACHE="0", FEX_APP_CONFIG=str(config),
               FEX_APP_CONFIG_LOCATION=str(tree / "files/share/fex-emu"))
    log = task / ("results/runtime-" + phase + ".log")
    try:
        with log.open("w") as output:
            result = subprocess.run([str(binary / "wine"), str(probe), phase,
                                     windows_path(targets["dxgi"]), windows_path(targets["vkd3d"])],
                                    env=env, stdout=output, stderr=subprocess.STDOUT, timeout=120)
        if result.returncode or "DROIDDECK_RUNTIME_OK " + phase not in log.read_text():
            raise RuntimeError(phase + " Wine/DLL runtime check failed; see " + str(log))
    finally:
        subprocess.run([str(binary / "wineserver"), "-k"], env=env, timeout=30, check=False)


def verify(task):
    staging = task / "runtime-reference"
    staging.mkdir()
    subprocess.run(["tar", "-xzf", str(task / "base.tar.gz"), "--same-permissions", "-C", str(staging)], check=True)
    tree = base_tree(staging)
    targets = {"ntdll": original_ntdll(tree), "dxgi": original_dxvk(tree), "vkd3d": original_vkd3d(tree)}
    rebuilt = {"ntdll": task / "results/ntdll.dll", "dxgi": task / "results/dxvk/dxgi.dll",
               "vkd3d": task / "results/vkd3d/d3d12core.dll"}
    original_hashes = {name: sha256(path) for name, path in targets.items()}
    for name, original in targets.items():
        with (task / ("results/stock-" + name + "-headers.txt")).open("w") as output:
            subprocess.run(["llvm-readobj", "--file-headers", "--coff-exports", "--coff-imports",
                            "--coff-load-config", str(original)], stdout=output, check=True)
        if pe_interface(original) != pe_interface(rebuilt[name]):
            raise ValueError(name + " stock/rebuilt exports, imports or architecture differ")
    probe = task / "results/runtime-probe.exe"
    subprocess.run(["x86_64-w64-mingw32-gcc", "-O2", "-Wall", "-Wextra",
                    str(Path(__file__).with_name("runtime-probe.c")), "-o", str(probe)], check=True)
    run_probe(task, tree, probe, targets, "stock")
    for name, target in targets.items():
        write_preserving_mode(target, rebuilt[name].read_bytes())
    run_probe(task, tree, probe, targets, "patched")
    report = {"stock": "passed", "patched": "passed", "original_sha256": original_hashes,
              "dll_sha256": {name: sha256(path) for name, path in rebuilt.items()},
              "scope": "x64/ARM64EC thread context and native graphics DLL loading; GPU rendering untested"}
    (task / "results/runtime-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Stock and patched ARM64EC DLL loading passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    args = parser.parse_args()
    verify(args.task.resolve())
