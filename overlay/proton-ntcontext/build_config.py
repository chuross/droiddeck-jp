"""Evaluate the pinned Proton Makefile's flags for the PE rebuilds."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def flags(source):
    text = (source / "Makefile.in").read_text()
    boundary = "$(DST_DIR):\n"
    if text.count(boundary) != 1:
        raise ValueError("Proton global build configuration changed")
    names = ("CFLAGS", "i386_CFLAGS", "x86_64_CFLAGS", "aarch64_CFLAGS", "arm64ec_CFLAGS",
             "WINE_CFLAGS", "DXVK_arm64ec_CFLAGS", "DXVK_arm64ec_LDFLAGS",
             "VKD3D_PROTON_arm64ec_CFLAGS", "VKD3D_PROTON_arm64ec_LDFLAGS")
    component = []
    for name in names[5:]:
        matches = re.findall(r"(?m)^" + name + r"\s*[+:?]?=\s*[^\n]+$", text)
        if len(matches) != 1:
            raise ValueError("Proton component flags changed: " + name)
        component.extend(matches)
    # The upstream aarch64 release removes only -ggdb before compiling.
    # GNU Make evaluates the same global/architecture assignments, including
    # signed-wrap and aliasing policy, rather than duplicating flags here.
    recipe = ("SRCDIR := .\nCCACHE_BASEDIR := .\n" + text.split(boundary)[0]
              + "\n" + "\n".join(component)
              + "\nCFLAGS := $(filter-out -ggdb,$(CFLAGS))\n"
              + "\n".join(f"$(info {name}=$({name}))" for name in names)
              + "\nflags: ; @:\n")
    result = subprocess.run(["make", "--no-print-directory", "-s", "-f", "-", "flags"],
                            input=recipe, text=True, capture_output=True, check=True)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if set(values) != set(names) or any(not values[name].strip() for name in names):
        raise ValueError("incomplete Proton flags")
    return {name: shlex.split(value) for name, value in values.items()}


def wine_environment(values):
    common = values["WINE_CFLAGS"] + values["CFLAGS"]
    native = values["aarch64_CFLAGS"] + common
    result = {"CC": "aarch64-linux-gnu-gcc", "CXX": "aarch64-linux-gnu-g++",
              "CFLAGS": shlex.join(native), "CPPFLAGS": shlex.join(native),
              "CROSSCFLAGS": shlex.join(native), "CROSSLDFLAGS": ""}
    for arch in ("i386", "x86_64", "aarch64", "arm64ec"):
        result[arch + "_CFLAGS"] = shlex.join(values[arch + "_CFLAGS"] + common)
        result[arch + "_LDFLAGS"] = ""
    return result


def cross_file(values, template, component):
    prefix = "DXVK" if component == "dxvk" else "VKD3D_PROTON"
    compile_flags = values["arm64ec_CFLAGS"] + values[prefix + "_arm64ec_CFLAGS"] + values["CFLAGS"]
    link_flags = values[prefix + "_arm64ec_LDFLAGS"]
    text = template.read_text()
    for name, args in (("c_args", compile_flags), ("cpp_args", compile_flags),
                       ("c_link_args", link_flags), ("cpp_link_args", link_flags)):
        text, count = re.subn(r"(?m)^" + name + r" = .*", name + " = " + repr(args), text)
        if count != 1:
            raise ValueError("cross-file template changed: " + name)
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--wine-build", type=Path)
    parser.add_argument("--cross", choices=("dxvk", "vkd3d"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = flags(args.source)
    if args.wine_build:
        env = dict(os.environ, **wine_environment(values))
        args.output.write_text(json.dumps({"flags": values, "wine_environment": wine_environment(values)}, indent=2) + "\n")
        subprocess.run([str(args.source / "wine/configure"), "--host=aarch64-linux-gnu",
                        "--enable-archs=arm64ec,aarch64,i386,x86_64", "--with-mingw=clang",
                        "--disable-tests", "--without-x", "--without-wayland", "--without-ffmpeg"],
                       cwd=args.wine_build, env=env, check=True)
    elif args.cross:
        args.output.write_text(cross_file(values, Path(__file__).with_name("dxvk-arm64ec.cross"), args.cross))
    else:
        parser.error("select --wine-build or --cross")
