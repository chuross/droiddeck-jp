"""Inspect stock DLLs, then load the patched DLLs through stock ARM64 Wine."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from package import (base_tree, original_ntdll, original_dxvk, original_vkd3d,
                     pe_interface, sha256, write_preserving_mode)
from stock_ntdll import patch as patch_stock_ntdll, verify as verify_stock_ntdll
from droiddeck_ram import configure_ram


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
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="err+all,+loaddll",
               WINESERVER=str(binary / "wineserver"),
               WINEDLLPATH=str(lib / "vkd3d") + ":" + str(lib / "wine"),
               # Proton normally installs native graphics DLLs into system32.
               # The headless probe loads them from the depot instead; include
               # these directories for d3d12core's native dxgi dependency.
               WINEPATH=windows_path(targets["dxgi"].parent) + ";" + windows_path(targets["vkd3d"].parent),
               LD_LIBRARY_PATH=":".join(library_paths) + ":" + os.environ.get("LD_LIBRARY_PATH", ""),
               WINEDLLOVERRIDES="dxgi,d3d12core=n;winemenubuilder.exe=d",
               FEX_DISKCACHE="0", FEX_APP_CONFIG=str(config),
               DROIDDECK_VKD3D_LOW_MEMORY="1" if phase == "patched" else "0",
               DROIDDECK_SESSION_LOW_MEMORY="1" if phase == "patched" else "0",
               FEX_APP_CONFIG_LOCATION=str(tree / "files/share/fex-emu"))
    configure_ram(env)
    log = task / ("results/runtime-" + phase + ".log")
    try:
        with log.open("w") as output:
            # Mirror Proton's native graphics installation after Wine creates
            # the prefix. WINEPATH alone does not reproduce system DLL lookup.
            subprocess.run([str(binary / "wine"), "wineboot.exe", "-u"], env=env,
                           stdout=output, stderr=subprocess.STDOUT, timeout=120, check=True)
            system32 = prefix / "drive_c/windows/system32"
            for name in ("dxgi", "vkd3d"):
                installed = system32 / targets[name].name
                installed.unlink(missing_ok=True)
                shutil.copyfile(targets[name], installed)
            result = subprocess.run([str(binary / "wine"), str(probe), phase,
                                     windows_path(targets["dxgi"]), windows_path(targets["vkd3d"])],
                                    env=env, stdout=output, stderr=subprocess.STDOUT, timeout=120)
        if result.returncode or "DROIDDECK_RUNTIME_OK " + phase not in log.read_text():
            print("\n".join(log.read_text().splitlines()[-40:]))
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
    # Ship the original ntdll plus a 16-byte leaf guard, not the standalone
    # Wine rebuild. That rebuild remains source/disassembly evidence only.
    stock_ntdll = targets["ntdll"].read_bytes()
    rebuilt["ntdll"].write_bytes(patch_stock_ntdll(stock_ntdll))
    ntdll_delta = verify_stock_ntdll(stock_ntdll, rebuilt["ntdll"].read_bytes())
    (task / "results/ntdll-stock-delta.json").write_text(json.dumps(ntdll_delta, indent=2) + "\n")
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
    write_preserving_mode(targets["ntdll"], rebuilt["ntdll"].read_bytes())
    run_probe(task, tree, probe, targets, "patched-stock-graphics")
    for name, target in targets.items():
        write_preserving_mode(target, rebuilt[name].read_bytes())
    run_probe(task, tree, probe, targets, "patched")
    report = {"stock": "passed", "patched": "passed", "original_sha256": original_hashes,
              "patched_stock_graphics": "passed",
              "dll_sha256": {name: sha256(path) for name, path in rebuilt.items()},
              "ntdll_stock_delta": ntdll_delta,
              "scope": "x64/ARM64EC thread context and native graphics DLL loading; GPU rendering untested"}
    (task / "results/runtime-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Stock and patched ARM64EC DLL loading passed")


def guard_preflight(task):
    staging = task / "guard-reference"
    staging.mkdir()
    subprocess.run(["tar", "-xzf", str(task / "base.tar.gz"), "-C", str(staging)], check=True)
    tree = base_tree(staging)
    targets = {"ntdll": original_ntdll(tree), "dxgi": original_dxvk(tree), "vkd3d": original_vkd3d(tree)}
    probe = task / "results/guard-runtime-probe.exe"
    subprocess.run(["x86_64-w64-mingw32-gcc", "-O2", "-Wall", "-Wextra",
                    str(Path(__file__).with_name("runtime-probe.c")), "-o", str(probe)], check=True)
    run_probe(task, tree, probe, targets, "guard-stock")
    original = targets["ntdll"].read_bytes()
    candidate = patch_stock_ntdll(original)
    verify_stock_ntdll(original, candidate)
    write_preserving_mode(targets["ntdll"], candidate)
    run_probe(task, tree, probe, targets, "patched-guard-preflight")
    shutil.rmtree(staging)
    for phase in ("guard-stock", "patched-guard-preflight"):
        shutil.rmtree(task / ("smoke-prefix-" + phase))
    print("Original DLL leaf guard runtime preflight passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--guard-preflight", action="store_true")
    args = parser.parse_args()
    (guard_preflight if args.guard_preflight else verify)(args.task.resolve())
