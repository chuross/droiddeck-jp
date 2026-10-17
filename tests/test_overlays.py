"""Discover overlay-owned suites so deleting an overlay also deletes its tests."""
from pathlib import Path
import unittest
import re

def fixture_from_patch(patch, root):
    """Reconstruct just the unmodified hunk contexts, padded to their original line numbers."""
    name = None
    files = {}
    position = None
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith('--- a/'):
            name = line[6:].strip(); files.setdefault(name, [])
        elif line.startswith('@@ '):
            position = int(re.match(r'@@ -(\d+)', line)[1]) - 1
        elif name and position is not None and line[:1] in (' ', '-') and not line.startswith('---'):
            lines = files[name]
            while len(lines) <= position: lines.append('// fixture padding\n')
            if lines[position] != '// fixture padding\n' and lines[position] != line[1:]:
                raise AssertionError('Conflicting fixture hunk')
            lines[position] = line[1:]; position += 1
    for name, lines in files.items():
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(''.join(lines))




def load_tests(loader, tests, pattern):
    for directory in sorted((Path(__file__).resolve().parents[1] / 'overlay').iterdir()):
        for file in sorted(directory.glob('test_*.py')):
            import importlib.util
            spec = importlib.util.spec_from_file_location(directory.name.replace('-', '_') + '_' + file.stem, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tests.addTests(loader.loadTestsFromModule(module))
    return tests


class OverlayRemovalTest(unittest.TestCase):
    def test_registration_and_directory_removal_keeps_other_overlays_operational(self):
        import importlib.util
        import json
        import shutil
        import subprocess
        import sys
        import tempfile
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('removal_prepare', root / 'tools/prepare.py')
        prepare = importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare)
        removable = {name: module for name, module in prepare.PATCHES.items() if hasattr(module, 'OWNED_PATHS')}
        if not removable:
            self.skipTest('No manifest-owning temporary overlays registered')
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            fixture = work / 'fixture'; fixture.mkdir()
            for module in removable.values(): fixture_from_patch(module.PATCH, fixture)
            baseline = {p.relative_to(fixture).as_posix(): p.read_bytes() for p in fixture.rglob('*') if p.is_file()}
            for removed, owner in removable.items():
                manager = work / ('manager-' + removed)
                shutil.copytree(root, manager, ignore=shutil.ignore_patterns('.git', 'build', '__pycache__'))
                shutil.rmtree(manager / 'overlay' / removed)
                dispatcher = manager / 'tools/prepare.py'
                dispatcher.write_text(dispatcher.read_text().replace('    "' + removed + '",\n', ''))
                # No filename imports or runtime registry needs to survive directory removal.
                source = work / ('source-' + removed); shutil.copytree(fixture, source)
                subprocess.run(['git', 'init', '-q', str(source)], check=True)
                program = '''
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('removed_prepare', Path(sys.argv[1]) / 'tools/prepare.py')
prepare = importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare)
# This fixture covers temporary hooks; existing overlays have their own full integration suite.
prepare.PATCHES = {name: module for name, module in prepare.PATCHES.items() if hasattr(module, 'OWNED_PATHS')}
prepare.stage_overlay_assets = lambda source: None
prepare.patch_app_identity = lambda source: None
prepare.overlay(Path(sys.argv[2]))
'''
                subprocess.run([sys.executable, '-c', program, str(manager), str(source)], check=True, capture_output=True, text=True)
                for name in owner.OWNED_PATHS:
                    if name in baseline: self.assertEqual((source / name).read_bytes(), baseline[name], name)
                    else: self.assertFalse((source / name).exists(), name)
                for name, other in removable.items():
                    if name != removed:
                        for path in other.OWNED_PATHS:
                            self.assertTrue((source / path).exists(), path)
                            self.assertNotEqual((source / path).read_bytes(), baseline.get(path), path)

    def test_full_preparation_after_removal_matches_existing_overlay_configuration(self):
        import hashlib
        import importlib.util
        import os
        import subprocess
        import tempfile
        from unittest.mock import patch
        source = os.environ.get('DROIDDECK_PATCH_SOURCE')
        if not source:
            self.skipTest('Set DROIDDECK_PATCH_SOURCE for full upstream removal checks')
        source = Path(source)
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('full_removal_prepare', root / 'tools/prepare.py')
        prepare = importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
        removable = {name: module for name,module in prepare.PATCHES.items() if hasattr(module,'OWNED_PATHS')}
        if not removable:self.skipTest('No temporary overlays registered')
        existing = {name: module for name,module in prepare.PATCHES.items() if name not in removable}
        fonts = prepare.PATCHES['japanese-fonts']
        archive = (source/'tools/linuxfs/overlay/usr/local/share/fonts/droiddeck-patch'/fonts.IPAMONA_ARCHIVE).read_bytes()
        def snapshot(tree):
            return {p.relative_to(tree).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in tree.rglob('*') if p.is_file() and '.git' not in p.relative_to(tree).parts}
        with tempfile.TemporaryDirectory() as temporary:
            def prepared(name, modules):
                tree=Path(temporary)/name
                subprocess.run(['git','clone','-q','--shared',str(source),str(tree)],check=True,capture_output=True)
                with patch.dict(prepare.PATCHES,modules,clear=True),patch.object(fonts,'download_ipamona',return_value=archive):
                    prepare.overlay(tree)
                return snapshot(tree)
            baseline=prepared('existing',existing)
            for removed in removable:
                remaining={name:module for name,module in prepare.PATCHES.items() if name != removed}
                result=prepared('without-'+removed,remaining)
                changed={name for name in baseline.keys() | result.keys() if baseline.get(name) != result.get(name)}
                expected={path for name,module in removable.items() if name != removed for path in module.OWNED_PATHS}
                self.assertEqual(changed,expected,removed)

    def test_all_declared_patch_variants_have_the_same_owned_footprint(self):
        import importlib.util
        import subprocess
        import tempfile
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('variant_prepare',root/'tools/prepare.py')
        prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
        for name,module in prepare.PATCHES.items():
            if not hasattr(module,'OWNED_PATHS'):continue
            for patch in getattr(module,'PATCHES',(module.PATCH,)):
                with self.subTest(overlay=name,variant=patch.name),tempfile.TemporaryDirectory() as temporary:
                    tree=Path(temporary);fixture_from_patch(patch,tree)
                    before={p.relative_to(tree).as_posix():p.read_bytes() for p in tree.rglob('*') if p.is_file()}
                    subprocess.run(['git','init','-q',str(tree)],check=True)
                    module.apply(tree)
                    changed={p.relative_to(tree).as_posix() for p in tree.rglob('*') if p.is_file() and '.git' not in p.parts
                             and before.get(p.relative_to(tree).as_posix()) != p.read_bytes()}
                    self.assertEqual(changed,set(module.OWNED_PATHS))
