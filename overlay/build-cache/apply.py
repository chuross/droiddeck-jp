"""Reuse checksum-verified native source downloads between APK builds."""

BEFORE = '''  curl -fsSL --retry 3 -o "$WORK/$1" "$2"
  echo "$3  $WORK/$1" | shasum -a 256 -c -
'''
AFTER = r'''  local archive_name="$1" url="$2" digest="$3"
  local cache_dir="${DROIDDECK_BUILD_CACHE:-$HOME/.cache/droiddeck-build}"
  local cache_file="$cache_dir/$digest-$archive_name"
  mkdir -p "$cache_dir"
  if ! echo "$digest  $cache_file" | shasum -a 256 -c - >/dev/null 2>&1; then
    local temporary="$cache_file.tmp.$$" alternative="${url/https:\/\/www.samba.org\/ftp\//https:\/\/download.samba.org\/pub\/}"
    local candidates=("$url") candidate verified=0
    if [[ "$alternative" != "$url" ]]; then
      candidates+=("$alternative")
      candidates+=("https://ftp.osuosl.org/pub/blfs/conglomeration/talloc/${url##*/}")
    else
      candidates+=("$url" "$url")
    fi
    for candidate in "${candidates[@]}"; do
      echo "Fetching $archive_name from $candidate" >&2
      if curl -fsSL --connect-timeout 10 --max-time 30 -o "$temporary" "$candidate" &&
          echo "$digest  $temporary" | shasum -a 256 -c -; then
        verified=1
        break
      fi
      rm -f "$temporary"
    done
    if [[ "$verified" != 1 ]]; then
      echo "No verified source available for $archive_name" >&2
      return 1
    fi
    mv "$temporary" "$cache_file"
  fi
  cp "$cache_file" "$WORK/$archive_name"
  echo "$digest  $WORK/$archive_name" | shasum -a 256 -c -
'''


def check(source):
    path = source / 'tools/proot/build.sh'
    if path.read_text().count(BEFORE) != 1:
        raise RuntimeError('Proot source fetch changed; review build cache integration')


def apply(source):
    check(source)
    path = source / 'tools/proot/build.sh'
    path.write_text(path.read_text().replace(BEFORE, AFTER))
