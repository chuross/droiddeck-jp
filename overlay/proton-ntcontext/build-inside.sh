#!/usr/bin/env bash
set -euo pipefail
task_root="$1"
mapfile -t objects < <(find "$task_root/proton-build" -path '*/dlls/ntdll/arm64ec-windows/signal_arm64ec.o' -type f)
test "${#objects[@]}" -eq 1
llvm-objdump -dr "${objects[0]}" > "$task_root/results/signal-arm64ec-disassembly.txt"
cp "${objects[0]}" "$task_root/results/signal_arm64ec.o"
