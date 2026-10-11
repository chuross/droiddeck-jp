#!/usr/bin/env bash
set -euo pipefail
manager_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
task_root="$manager_root/build/proton-ntcontext"
test ! -e "$task_root"
mkdir -p "$task_root"
cp "${1:-$manager_root/overlay/proton-ntcontext/build.lock.json}" "$task_root/build.lock.json"
lock="$task_root/build.lock.json"
read_lock() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$lock" "$1"; }
proton_commit="$(read_lock proton_commit)"
wine_commit="$(read_lock wine_commit)"
dxvk_commit="$(read_lock dxvk_commit)"
git clone --filter=blob:none --no-checkout "$(read_lock proton_repository)" "$task_root/proton"
git -C "$task_root/proton" fetch --depth=1 origin "$proton_commit"
git -C "$task_root/proton" checkout --detach FETCH_HEAD
test "$(git -C "$task_root/proton" rev-parse HEAD)" = "$proton_commit"
git -C "$task_root/proton" submodule update --init --depth=1 wine wine-staging dxvk glslang
git -C "$task_root/proton/dxvk" submodule update --init --recursive --depth=1
test "$(git -C "$task_root/proton/wine" rev-parse HEAD)" = "$wine_commit"
test "$(git -C "$task_root/proton/dxvk" rev-parse HEAD)" = "$dxvk_commit"
# GE reverts this commit to restore the configure files needed by wine-staging.
git -C "$task_root/proton/wine" fetch --depth=2 origin e813ca5771658b00875924ab88d525322e50d39f
git -C "$task_root/proton/wine" config user.name 'DroidDeck JP builder'
git -C "$task_root/proton/wine" config user.email 'droiddeck-jp@users.noreply.github.com'
python3 "$manager_root/overlay/proton-ntcontext/prepare_ge.py" "$task_root/proton"
sdk_image="$(read_lock sdk_image)"
docker pull "$sdk_image"
docker image inspect "$sdk_image" --format '{{json .RepoDigests}}' > "$task_root/sdk-digests.json"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$manager_root:$manager_root" -w "$task_root" \
    "$sdk_image" bash "$manager_root/overlay/proton-ntcontext/build-inside.sh" "$task_root" "$manager_root"
curl -fL --retry 5 "$(read_lock base_archive)" -o "$task_root/base.tar.gz"
printf '%s  %s\n' "$(read_lock base_sha256)" "$task_root/base.tar.gz" | sha256sum --check
manager_commit="${GITHUB_SHA:-$(git -C "$manager_root" rev-parse HEAD)}"
python3 "$manager_root/overlay/proton-ntcontext/package.py" "$task_root" --manager "$manager_commit" --lock "$lock"
# Corresponding prepared Wine source accompanies the rebuilt GPL ntdll and DXVK DLL.
tar --exclude=.git -cJf "$task_root/dist/$(read_lock build_name)-wine-source.tar.xz" \
    -C "$task_root/proton" wine dxvk \
    -C "$manager_root" overlay/proton-ntcontext \
    -C "$task_root" build.lock.json
cd "$task_root/dist"
sha256sum *.tar.* > SHA256SUMS
