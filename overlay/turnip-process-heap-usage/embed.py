"""Stage verified driver binaries and corresponding source inside the APK, without a Release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from validate_bundle import validate


def stage(work, destination):
    plan = json.loads((work / 'plan.json').read_text())
    destination.mkdir(parents=True, exist_ok=False)
    if not plan['build']:
        if plan['status'] != 'overlay unnecessary: equivalent upstream fix detected':
            raise RuntimeError('Unknown skipped build')
        (destination / 'upstream-fixed.json').write_text(json.dumps(plan, indent=2) + '\n')
        return
    plan, (bundle, checksum, source) = validate(work)
    shutil.copy2(bundle, destination / 'bundle.zip')
    shutil.copy2(source, destination / source.name)
    shutil.copy2(checksum, destination / checksum.name)
    (destination / 'bundle.json').write_text(json.dumps(dict(tag=plan['tag'],
        sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), size=bundle.stat().st_size,
        source=source.name, identity=plan['identity']), indent=2) + '\n')


def verify_apk(apk):
    prefix = 'assets/turnip-process-heap-usage/'
    with zipfile.ZipFile(apk) as archive:
        if prefix + 'bundle.json' not in archive.namelist():
            plan = json.loads(archive.read(prefix + 'upstream-fixed.json'))
            if plan['status'] != 'overlay unnecessary: equivalent upstream fix detected':
                raise RuntimeError('APK omitted patched Turnip without an upstream fix')
            return
        metadata = json.loads(archive.read(prefix + 'bundle.json'))
        bundle = archive.read(prefix + 'bundle.zip')
        if len(bundle) != metadata['size'] or hashlib.sha256(bundle).hexdigest() != metadata['sha256']:
            raise RuntimeError('APK bundled Turnip checksum mismatch')
        if archive.getinfo(prefix + metadata['source']).file_size == 0:
            raise RuntimeError('APK corresponding source is empty')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('build/turnip-process-heap-usage'))
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--verify-apk', type=Path)
    args = parser.parse_args()
    if args.verify_apk:
        verify_apk(args.verify_apk)
    elif args.destination:
        stage(args.work, args.destination)
    else:
        parser.error("--destination or --verify-apk is required")
