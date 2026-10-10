import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_cache", ROOT / "overlay/build-cache/apply.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class BuildCacheTest(unittest.TestCase):
    def test_failed_and_corrupt_samba_downloads_use_verified_mirror_then_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            digest = hashlib.sha256(b"verified archive").hexdigest()
            script = root / "test.sh"
            script.write_text('''#!/usr/bin/env bash
set -euo pipefail
curl() {
  local url="${@: -1}" output="${@: -2:1}"
  echo "$url" >> "$CALLS"
  printf '%s\\n' "$*" >> "$OPTIONS"
  case "$url" in
    https://www.samba.org/*) return 28 ;;
    https://download.samba.org/*) printf corrupt > "$output" ;;
    https://ftp.osuosl.org/*) printf 'verified archive' > "$output" ;;
    *) return 1 ;;
  esac
}
fetch() {
''' + cache.AFTER + '''}
fetch talloc.tar.gz https://www.samba.org/ftp/talloc/talloc-2.4.3.tar.gz "$DIGEST"
fetch talloc.tar.gz https://www.samba.org/ftp/talloc/talloc-2.4.3.tar.gz "$DIGEST"
fetch proot.tar.gz https://ftp.osuosl.org/proot.tar.gz "$DIGEST"
''')
            env = dict(os.environ, WORK=str(root / "work"),
                       DROIDDECK_BUILD_CACHE=str(root / "cache"),
                       CALLS=str(root / "calls"), OPTIONS=str(root / "options"), DIGEST=digest)
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (root / "calls").read_text().splitlines()
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[2], "https://ftp.osuosl.org/pub/blfs/conglomeration/talloc/talloc-2.4.3.tar.gz")
            options = (root / "options").read_text().splitlines()
            for attempt in options[:3]:
                self.assertIn("--connect-timeout 5", attempt)
                self.assertIn("--max-time 9", attempt)
            self.assertIn("--max-time 7", options[3])
            self.assertEqual((root / "work/talloc.tar.gz").read_bytes(), b"verified archive")
            self.assertEqual(list((root / "cache").glob("*.tmp.*")), [])
