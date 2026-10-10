#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
manager_root="$2"
cd "$task_root/proton"
bash prepare-ntcontext-wine.sh
python3 "$manager_root/overlay/proton-ntcontext/apply.py" --wine-source "$task_root/proton/wine"
# GE's source preparation generates these headers before configure runs makedep.
# The generator downloads the registry version pinned in this Wine source.
(cd "$task_root/proton/wine" && XDG_CACHE_HOME="$task_root/cache" dlls/winevulkan/make_vulkan)
(cd "$task_root/proton/wine" && tools/make_specfiles)
mkdir -p "$task_root/wine-build" "$task_root/results"
cd "$task_root/wine-build"
export CFLAGS='-O2 -fwrapv -fno-strict-aliasing -fno-omit-frame-pointer -march=armv8.2-a -mtune=cortex-x3'
"$task_root/proton/wine/configure" \
    --host=aarch64-linux-gnu \
    --enable-archs=arm64ec,aarch64,i386,x86_64 \
    --with-mingw=clang --disable-tests --without-x --without-wayland --without-ffmpeg
make -j"$(nproc)" dlls/ntdll/all
test -s dlls/ntdll/aarch64-windows/ntdll.dll
cp dlls/ntdll/aarch64-windows/ntdll.dll "$task_root/results/ntdll.dll"
# Keep evidence that the ARM64EC function was compiled, including its NULL branch.
llvm-objdump -dr dlls/ntdll/arm64ec-windows/signal_arm64ec.o > "$task_root/results/signal-arm64ec-disassembly.txt"
cp dlls/ntdll/arm64ec-windows/signal_arm64ec.o "$task_root/results/signal_arm64ec.o"
llvm-readobj --file-headers --coff-exports --coff-load-config "$task_root/results/ntdll.dll" > "$task_root/results/ntdll-headers.txt"
git -C "$task_root/proton/wine" diff --binary > "$task_root/results/ge-wine-patches.diff"
