#!/usr/bin/env bash
set -euo pipefail
manager_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
task_root="$manager_root/build/proton-ntcontext"
test ! -e "$task_root"
mkdir -p "$task_root"
cp "${1:-$manager_root/overlay/proton-ntcontext/build.lock.json}" "$task_root/build.lock.json"
lock="$task_root/build.lock.json"
read_lock() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$lock" "$1"; }
git clone --filter=blob:none --no-checkout "$(read_lock proton_repository)" "$task_root/proton"
git -C "$task_root/proton" fetch --depth=1 origin "$(read_lock proton_commit)"
git -C "$task_root/proton" checkout --detach FETCH_HEAD
test "$(git -C "$task_root/proton" rev-parse HEAD)" = "$(read_lock proton_commit)"
git -C "$task_root/proton" submodule update --init --recursive --depth=1
# FEX's upstream build uses git describe without --always and needs tagged ancestry.
if test "$(git -C "$task_root/proton/FEX" rev-parse --is-shallow-repository)" = true; then
    git -C "$task_root/proton/FEX" fetch --unshallow --tags origin
else
    git -C "$task_root/proton/FEX" fetch --tags origin
fi
git -C "$task_root/proton/FEX" describe --abbrev=7
test "$(git -C "$task_root/proton/wine" rev-parse HEAD)" = "$(read_lock wine_commit)"
test "$(git -C "$task_root/proton/dxvk" rev-parse HEAD)" = "$(read_lock dxvk_commit)"
python3 "$manager_root/overlay/proton-ntcontext/apply.py" --wine-source "$task_root/proton/wine" --dxvk-source "$task_root/proton/dxvk"
mkdir -p "$task_root/results" "$task_root/proton-build"
git -C "$task_root/proton/wine" diff --binary > "$task_root/results/wine-patches.diff"
git -C "$task_root/proton/dxvk" diff --binary > "$task_root/results/dxvk-patches.diff"
sdk_image="$(read_lock sdk_image)"
docker pull "$sdk_image"
docker image inspect "$sdk_image" --format '{{json .RepoDigests}}' > "$task_root/sdk-digests.json"
cd "$task_root/proton-build"
"$task_root/proton/configure.sh" --target-arch=arm64 --build-name="$(read_lock build_name)" --proton-sdk-image="$sdk_image" --container-engine=docker
make -j"$(nproc)" redist
# Inspect the object produced by the same full build as the shipped ntdll.
docker run --rm --user "$(id -u):$(id -g)" -v "$manager_root:$manager_root" -w "$task_root" "$sdk_image" bash "$manager_root/overlay/proton-ntcontext/build-inside.sh" "$task_root"
manager_commit="${GITHUB_SHA:-$(git -C "$manager_root" rev-parse HEAD)}"
mkdir -p "$task_root/dist"
# Keep all component sources and build inputs in CI, including downloaded contrib sources.
tar --exclude=.git -cJf "$task_root/dist/$(read_lock build_name)-wine-source.tar.xz" -C "$task_root" proton build.lock.json -C "$manager_root" overlay/proton-ntcontext
python3 "$manager_root/overlay/proton-ntcontext/package.py" "$task_root" --manager "$manager_commit" --lock "$lock"
cd "$task_root/dist"
sha256sum *.tar.* > SHA256SUMS
