#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
manager_root="$2"
source="$task_root/proton/dxvk"
mkdir -p "$task_root/results/dxvk"
# GE uses the pinned glslang submodule as DXVK's native build dependency.
cmake -S "$task_root/proton/glslang" -B "$task_root/glslang-build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_CTEST=OFF
cmake --build "$task_root/glslang-build" --target glslangValidator -j"$(nproc)"
# DXVK tries glslang before glslangValidator; prefer the pinned compiler even
# when the SDK also has a system glslang binary.
ln -sf glslangValidator "$task_root/glslang-build/StandAlone/glslang"
export PATH="$task_root/glslang-build/StandAlone:$PATH"
# GE's dxvk-post-source rule embeds this exact source version before Meson.
version="$(git -C "$source" describe --always --abbrev=15 --dirty=0)"
sed "s/@VCS_TAG@/$version/" "$source/version.h.in" > "$source/version.h.in.tmp"
mv "$source/version.h.in.tmp" "$source/version.h.in"
meson setup "$task_root/dxvk-build" "$source" \
    --cross-file="$manager_root/overlay/proton-ntcontext/dxvk-arm64ec.cross" \
    --buildtype=plain --prefix="$task_root/dxvk-install" \
    --bindir=lib/wine/dxvk/aarch64-windows \
    -Denable_dxgi=true -Denable_d3d8=false -Denable_d3d9=false \
    -Denable_d3d10=false -Denable_d3d11=false
# Build DXGI and its static dependencies, not all Proton graphics DLLs.
ninja -j"$(nproc)" -C "$task_root/dxvk-build" src/dxgi/dxgi.dll 2>&1 | tee "$task_root/results/dxvk/build.log"
test -s "$task_root/dxvk-build/src/dxgi/dxgi.dll"
cp "$task_root/dxvk-build/src/dxgi/dxgi.dll" "$task_root/results/dxvk/dxgi.dll"
llvm-readobj --file-headers --coff-exports --coff-load-config \
    "$task_root/results/dxvk/dxgi.dll" > "$task_root/results/dxvk/dxgi-headers.txt"
git -C "$source" diff --binary > "$task_root/results/dxvk/patches.diff"
cp "$task_root/dxvk-build/compile_commands.json" "$task_root/results/dxvk/"
python3 -c 'import runpy,sys; from pathlib import Path; m=runpy.run_path(sys.argv[1]); print("DXVK PE:", m["dxvk_interface"](Path(sys.argv[2])))' \
    "$manager_root/overlay/proton-ntcontext/package.py" "$task_root/results/dxvk/dxgi.dll"
