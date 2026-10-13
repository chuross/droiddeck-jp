#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
manager_root="$2"
source="$task_root/proton/vkd3d-proton"
mkdir -p "$task_root/results/vkd3d"
# build-dxvk.sh already built the exact pinned host glslang compiler.
export PATH="$task_root/glslang-build/StandAlone:$PATH"
meson setup "$task_root/vkd3d-build" "$source" \
    --cross-file="$manager_root/overlay/proton-ntcontext/dxvk-arm64ec.cross" \
    --buildtype=plain --prefix="$task_root/vkd3d-install" \
    --bindir=lib/wine/vkd3d-proton/aarch64-windows \
    -Db_ndebug=true -Denable_extended_emulation=true \
    -Denable_tests=false -Denable_extras=false -Denable_trace=false
# d3d12.dll is only a loader. Rebuild the actual D3D12 implementation.
ninja -j"$(nproc)" -C "$task_root/vkd3d-build" libs/d3d12core/d3d12core.dll \
    2>&1 | tee "$task_root/results/vkd3d/build.log"
test -s "$task_root/vkd3d-build/libs/d3d12core/d3d12core.dll"
cp "$task_root/vkd3d-build/libs/d3d12core/d3d12core.dll" "$task_root/results/vkd3d/d3d12core.dll"
llvm-readobj --file-headers --coff-exports --coff-load-config \
    "$task_root/results/vkd3d/d3d12core.dll" > "$task_root/results/vkd3d/d3d12core-headers.txt"
cp "$task_root/vkd3d-build/compile_commands.json" "$task_root/results/vkd3d/"
python3 -c 'import runpy,sys; from pathlib import Path; m=runpy.run_path(sys.argv[1]); print("VKD3D PE:", m["vkd3d_interface"](Path(sys.argv[2])))' \
    "$manager_root/overlay/proton-ntcontext/package.py" "$task_root/results/vkd3d/d3d12core.dll"
