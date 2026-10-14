#!/usr/bin/env bash
set -euo pipefail
manager_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
task_root="$manager_root/build/proton-ntcontext"
test ! -e "$task_root"
mkdir -p "$task_root"
cp "${1:-$manager_root/overlay/proton-ntcontext/build.lock.json}" "$task_root/build.lock.json"
lock="$task_root/build.lock.json"
read_lock() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$lock" "$1"; }
curl -fL --retry 5 "$(read_lock base_archive)" -o "$task_root/base.tar.gz"
printf '%s  %s\n' "$(read_lock base_sha256)" "$task_root/base.tar.gz" | sha256sum --check
git clone --filter=blob:none --no-checkout "$(read_lock proton_repository)" "$task_root/proton"
git -C "$task_root/proton" fetch --depth=1 origin "$(read_lock proton_commit)"
git -C "$task_root/proton" checkout --detach FETCH_HEAD
test "$(git -C "$task_root/proton" rev-parse HEAD)" = "$(read_lock proton_commit)"
git -C "$task_root/proton" submodule update --init --recursive --depth=1 wine dxvk glslang vkd3d-proton
test "$(git -C "$task_root/proton/wine" rev-parse HEAD)" = "$(read_lock wine_commit)"
test "$(git -C "$task_root/proton/dxvk" rev-parse HEAD)" = "$(read_lock dxvk_commit)"
test "$(git -C "$task_root/proton/vkd3d-proton" rev-parse HEAD)" = "$(read_lock vkd3d_commit)"
mkdir -p "$task_root/results"
git -C "$task_root/proton" submodule update --init --depth=1 FEX
python3 "$manager_root/overlay/proton-ntcontext/droiddeck_ram.py" "$task_root/proton/FEX" \
    --output "$task_root/results/ram-source-audit.json"
DROIDDECK_FEX_SOURCE="$task_root/proton/FEX" \
    python3 -m unittest discover -s "$manager_root/tests" -p 'test_proton_ram.py' -v
python3 "$manager_root/overlay/proton-ntcontext/prepare_upstream.py" "$task_root/proton" \
    --output "$task_root/results/upstream-patches.json"
python3 "$manager_root/overlay/vkd3d-low-memory/apply.py" "$task_root/proton/vkd3d-proton" --check
python3 "$manager_root/overlay/proton-ntcontext/apply.py" --wine-source "$task_root/proton/wine" --dxvk-source "$task_root/proton/dxvk"
python3 "$manager_root/overlay/vkd3d-low-memory/apply.py" "$task_root/proton/vkd3d-proton"
DROIDDECK_VKD3D_SOURCE="$task_root/proton/vkd3d-proton" \
    python3 -m unittest discover -s "$manager_root/tests" -p 'test_vkd3d_low_memory.py' -v
mkdir -p "$task_root/results"
git -C "$task_root/proton/wine" diff --binary > "$task_root/results/wine-patches.diff"
git -C "$task_root/proton/dxvk" diff --binary > "$task_root/results/dxvk-patches.diff"
git -C "$task_root/proton/vkd3d-proton" diff --binary > "$task_root/results/vkd3d-proton-patches.diff"
sdk_image="$(read_lock sdk_image)"
docker pull "$sdk_image"
docker image inspect "$sdk_image" --format '{{json .RepoDigests}}' > "$task_root/sdk-digests.json"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$manager_root:$manager_root" -w "$task_root" "$sdk_image" bash "$manager_root/overlay/proton-ntcontext/build-inside.sh" "$task_root" "$manager_root"
rm -rf "$task_root/runtime-reference" "$task_root/smoke-prefix-stock" "$task_root/smoke-prefix-patched"
manager_commit="${GITHUB_SHA:-$(git -C "$manager_root" rev-parse HEAD)}"
mkdir -p "$task_root/dist"
# Keep all component sources and build inputs in CI, including downloaded contrib sources.
tar --exclude=.git -cJf "$task_root/dist/$(read_lock build_name)-wine-source.tar.xz" -C "$task_root" proton build.lock.json -C "$manager_root" overlay/proton-ntcontext overlay/vkd3d-low-memory
python3 "$manager_root/overlay/proton-ntcontext/package.py" "$task_root" --manager "$manager_commit" --lock "$lock"
cd "$task_root/dist"
sha256sum *.tar.* > SHA256SUMS

