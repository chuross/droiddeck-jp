#!/usr/bin/env bash
set -euo pipefail
manager_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$manager_root/build/turnip-process-heap-usage"
# Use only the exact official sources and the locally committed patched recipe from plan.
python3 - "$work" <<'PY'
import json, subprocess, sys
from pathlib import Path
work = Path(sys.argv[1]); plan = json.loads((work / 'plan.json').read_text())
if not plan['build'] or plan['status'] != 'patch required':
    raise SystemExit('No patched build required')
for path, key in ((work / 'drivers', 'patched_recipe_commit'), (work / 'mesa', 'mesa_commit')):
    actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != plan[key] or subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain'], text=True).strip():
        raise SystemExit('Planned source changed: ' + str(path))
PY
bash "$work/drivers/scripts/build_android.sh" "$work/mesa"
bash "$work/drivers/scripts/build_linux.sh" "$work/mesa"
python3 "$manager_root/overlay/turnip-process-heap-usage/driver.py" package --work "$work"
