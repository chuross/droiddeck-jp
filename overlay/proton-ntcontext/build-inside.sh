#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
manager_root="$2"
cd "$task_root/proton/wine"
autoreconf -fi
XDG_CACHE_HOME="$task_root/cache" dlls/winevulkan/make_vulkan
tools/make_specfiles
tools/make_requests
mkdir -p "$task_root/wine-build"
cd "$task_root/wine-build"
export CFLAGS='-O2 -fwrapv -fno-strict-aliasing -fno-omit-frame-pointer -march=armv8.2-a -mtune=cortex-x3'
"$task_root/proton/wine/configure" --host=aarch64-linux-gnu --enable-archs=arm64ec,aarch64,i386,x86_64 --with-mingw=clang --disable-tests --without-x --without-wayland --without-ffmpeg
make -j"$(nproc)" dlls/ntdll/all
cp dlls/ntdll/aarch64-windows/ntdll.dll "$task_root/results/ntdll.dll"
llvm-objdump -dr dlls/ntdll/arm64ec-windows/signal_arm64ec.o > "$task_root/results/signal-arm64ec-disassembly.txt"
cp dlls/ntdll/arm64ec-windows/signal_arm64ec.o "$task_root/results/signal_arm64ec.o"
llvm-readobj --file-headers --coff-exports --coff-load-config "$task_root/results/ntdll.dll" > "$task_root/results/ntdll-headers.txt"
bash "$manager_root/overlay/proton-ntcontext/build-dxvk.sh" "$task_root" "$manager_root"
