#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
manager_root="$2"
python3 "$manager_root/overlay/proton-ntcontext/runtime_smoke.py" "$task_root" --guard-preflight
cd "$task_root/proton/wine"
autoreconf -fi
XDG_CACHE_HOME="$task_root/cache" dlls/winevulkan/make_vulkan
tools/make_specfiles
tools/make_requests
mkdir -p "$task_root/wine-build"
cd "$task_root/wine-build"
python3 "$manager_root/overlay/proton-ntcontext/build_config.py" "$task_root/proton" \
    --wine-build "$task_root/wine-build" --output "$task_root/results/build-config.json"
make -j"$(nproc)" dlls/ntdll/all
cp dlls/ntdll/aarch64-windows/ntdll.dll "$task_root/results/ntdll.dll"
llvm-objdump -dr dlls/ntdll/arm64ec-windows/signal_arm64ec.o > "$task_root/results/signal-arm64ec-disassembly.txt"
cp dlls/ntdll/arm64ec-windows/signal_arm64ec.o "$task_root/results/signal_arm64ec.o"
llvm-readobj --file-headers --coff-exports --coff-load-config "$task_root/results/ntdll.dll" > "$task_root/results/ntdll-headers.txt"
bash "$manager_root/overlay/proton-ntcontext/build-dxvk.sh" "$task_root" "$manager_root"
bash "$manager_root/overlay/vkd3d-low-memory/build.sh" "$task_root" "$manager_root"
python3 "$manager_root/overlay/proton-ntcontext/runtime_smoke.py" "$task_root"

