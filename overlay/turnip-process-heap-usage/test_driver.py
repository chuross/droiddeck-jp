import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import hashlib

from test_overlays import fixture_from_patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import driver
import process_heap_usage as heap
import re
import validate_bundle as release
import embed
sys.path.remove(str(HERE))
spec = importlib.util.spec_from_file_location('turnip_source_apply', HERE / 'apply.py')
source = importlib.util.module_from_spec(spec); spec.loader.exec_module(source)


def official(tag='DD-Turnip-v0.2.0', **kwargs):
    return dict(id=1, tag_name=tag, published_at='2026-10-15T00:00:00Z',
                body='**Mesa:** 26.3.0-devel @ [mesa](https://gitlab.freedesktop.org/mesa/mesa/-/commit/' + 'a'*40 + ')\n'
                     '**Recipe:** [recipe](https://github.com/Droid-Deck/Drivers/commit/' + 'b'*40 + ')',
                assets=[{'name':tag+s} for s in ('.zip','.zip.sha256','-source.tar.xz')], **kwargs)


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args], text=True).strip()


class TurnipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def test_pr5_counter_preserves_local_limit_and_budget(self):
        fixture_from_patch(heap.PATCH, self.work)
        git(self.work,'init','-q')
        before = (self.work / heap.DEVICE).read_text()
        heap.apply(self.work)
        after = (self.work / heap.DEVICE).read_text()
        self.assertTrue(heap.equivalent(after))
        self.assertIn('p_atomic_add(&device->physical_device->heap.used, -mem->bo->size)', after)
        self.assertIn('p_atomic_add(&tu_process_heap_used, bo->size)', after)
        self.assertIn('p_atomic_add(&tu_process_heap_used, -mem->bo->size)', after)
        self.assertIn('heapUsage[0] = p_atomic_read(&tu_process_heap_used)', after)
        git(self.work, 'apply', '--reverse', str(heap.PATCH))
        self.assertEqual((self.work / heap.DEVICE).read_text(), before)
        # The entire delta is four counter-related hunks, with no budget or limit edits.
        removed = [line for line in heap.PATCH.read_text().splitlines() if line.startswith('-') and not line.startswith('---')]
        self.assertEqual([line[1:].strip() for line in removed], ['memory_budget_props->heapUsage[0] = p_atomic_read(&physical_device->heap.used);'])

    def test_semantic_detection_renames_counter_and_rejects_partial_and_drift(self):
        fixture_from_patch(heap.PATCH, self.work); git(self.work,'init','-q'); heap.apply(self.work)
        path = self.work / heap.DEVICE
        full = path.read_text().replace('tu_process_heap_used','all_instances_allocated')
        self.assertTrue(heap.equivalent(full))
        with self.assertRaisesRegex(RuntimeError, 'unnecessary'): heap.apply(self.work)
        with self.assertRaisesRegex(RuntimeError, 'partial'):
            heap.equivalent(full.replace('p_atomic_add(&all_instances_allocated, -mem->bo->size);',''))
        git(self.work,'apply','--reverse',str(heap.PATCH))
        path.write_text(path.read_text().replace('tu_get_budget_memory(', 'changed_budget('))
        with self.assertRaisesRegex(RuntimeError,'drift'): heap.apply(self.work)

    def test_official_provenance_is_dynamic_strict_and_stable_only(self):
        latest = official('DD-Turnip-v0.10.0')
        selected = driver.official_release([official(), latest, official('DD-Turnip-v1.0.0', prerelease=True), official('DD-Turnip-v2.0.0', draft=True)])
        self.assertEqual(selected['tag_name'], latest['tag_name'])
        plan = driver.provenance(selected)
        self.assertEqual(plan['recipe_commit'], 'b'*40)
        self.assertEqual(plan['mesa_commit'], 'a'*40)
        bad = official(); bad['body'] = 'unknown'
        with self.assertRaisesRegex(RuntimeError,'provenance'): driver.provenance(bad)
        bad = official(); bad['assets'].pop()
        with self.assertRaisesRegex(RuntimeError,'source'): driver.provenance(bad)
        changed = dict(plan, mesa_commit='c'*40)
        self.assertNotEqual(driver.source_identity(plan), driver.source_identity(changed))

    def recipe(self):
        recipe = self.work / 'recipe'; recipe.mkdir(); git(recipe,'init','-q')
        (recipe/'patches/scripts').mkdir(parents=True)
        (recipe/'patches/series.json').write_text('[]\n')
        (recipe/'scripts').mkdir()
        (recipe/'scripts/package_bundle.py').write_text('CI_URL = "https://github.com/Droid-Deck/Drivers-CI/releases/tag"\n# published with these drivers at {CI_URL}/{tag}\n')
        git(recipe,'add','.')
        git(recipe,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','official recipe')
        return recipe

    def test_local_recipe_commit_registers_both_targets_and_source_archive_contains_patch(self):
        recipe = self.recipe(); plan = driver.provenance(official()); plan['recipe_commit'] = git(recipe,'rev-parse','HEAD')
        commit = driver.prepare_recipe(recipe, plan)
        self.assertNotEqual(commit, plan['recipe_commit'])
        self.assertEqual(git(recipe,'status','--porcelain'),'')
        entries = json.loads((recipe/'patches/series.json').read_text())
        self.assertEqual(entries[-1]['targets'], ['android','linux'])
        files = git(recipe,'diff','--name-only',plan['recipe_commit'],commit).splitlines()
        self.assertEqual(set(files), {'patches/scripts/process_heap_usage.py','patches/scripts/process-heap-usage.patch','patches/series.json','scripts/package_bundle.py'})
        archive = self.work/'source.tar.xz'
        data = subprocess.check_output(['git','-C',str(recipe),'archive','--prefix='+plan['tag']+'-source/drivers/',commit])
        with tarfile.open(fileobj=io.BytesIO(data)) as original, tarfile.open(archive, 'w:xz') as packed:
            for member in original:
                packed.addfile(member, original.extractfile(member) if member.isfile() else None)
        driver.verify_source(archive, plan)
        with tarfile.open(archive, 'w:xz'): pass
        with self.assertRaises(KeyError): driver.verify_source(archive, plan)

    def test_upstream_series_is_executed_for_both_targets_regardless_of_filename(self):
        mesa = self.work/'mesa';mesa.mkdir(); recipe = self.work/'recipe';recipe.mkdir()
        fixture_from_patch(heap.PATCH, mesa); git(mesa,'init','-q'); heap.apply(mesa)
        text = (mesa/heap.DEVICE).read_text()
        calls=[]
        def run(*args):
            calls.append(args)
            if args[0]=='git':
                target = Path(args[-1]); (target / heap.DEVICE).parent.mkdir(parents=True)
                (target/heap.DEVICE).write_text(text)
            return ''
        with patch.object(driver, 'run', side_effect=run):
            self.assertTrue(driver.inspect_recipe(recipe, mesa))
        targets=[args[-2] for args in calls if args[0]==sys.executable]
        self.assertEqual(targets,['android','linux'])
        # Same meaning in only one target must fail rather than disable the overlay.
        with patch.object(driver,'run',side_effect=run), patch.object(driver,'equivalent',side_effect=[True,False]):
            with self.assertRaisesRegex(RuntimeError,'disagree'): driver.inspect_recipe(recipe,mesa)

    def test_bundled_hooks_remove_cleanly_and_keep_official_source(self):
        fixture_from_patch(source.PATCH, self.work); git(self.work,'init','-q')
        path = self.work/source.OWNED_PATHS[0]; before=path.read_text()
        source.apply(self.work); after=path.read_text()
        self.assertIn('BundledTurnip.merge(context',after)
        self.assertIn('BundledTurnip.download(context',after)
        self.assertNotIn('chuross/droiddeck-patch',after)
        git(self.work,'apply','--reverse',str(source.PATCH))
        self.assertEqual(path.read_text(),before)
        for patchfile in source.PATCHES:
            self.assertNotIn('-        Source("DroidDeck", "Droid-Deck/Drivers-CI")',patchfile.read_text())

    def test_driver_build_cannot_create_component_release(self):
        self.assertFalse(hasattr(release,'publish'))
        self.assertFalse((ROOT/'.github/workflows/turnip.yml').exists())
        action=(HERE/'action.yml').read_text()
        self.assertIn('using: composite',action)
        self.assertNotIn('contents: write',action)
        self.assertNotIn('gh release',action)
        self.assertNotIn('release.py',action)
        self.assertIn('embed.py',action)
        for name in ('apk.yml','nightly.yml'):
            text=(ROOT/'.github/workflows'/name).read_text()
            self.assertNotIn('needs: turnip',text)
            self.assertNotIn('turnip.yml',text)
            self.assertIn('uses: ./source/overlay/turnip-process-heap-usage',text)
            # Removing the dedicated action registration, validation hook and path trigger leaves
            # no job dependency or configuration that needs the removed overlay/workflow.
            removed = re.sub(r"      - name: Bundle temporary Turnip heap usage fix\n        uses: ./source/overlay/turnip-process-heap-usage\n(?:        if: steps.plan.outputs.build == 'true'\n)?", '', text)
            removed = '\n'.join(line for line in removed.split('\n') if 'turnip-process-heap-usage' not in line)
            self.assertNotIn('turnip', removed.lower())
        self.assertNotIn('turnip.yml',(ROOT/'.github/workflows/proton.yml').read_text())
        for path in HERE.glob('*'):
            if path.is_file() and path.name != Path(__file__).name:
                self.assertNotIn('background-memory',path.read_text())


class CounterExecutionTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler unavailable')
    def test_two_instances_allocate_free_and_report_with_unchanged_local_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            target = work / heap.DEVICE; target.parent.mkdir(parents=True)
            original = (HERE / 'testdata/tu_device.cc').read_text()
            target.write_text(original); git(work, 'init', '-q'); heap.apply(work)
            text = target.read_text()
            def function(name):
                start=text.index(name); begin=text.rfind('\n\n',0,start)+2
                brace=text.index('{',start); depth=1; end=brace+1
                while depth:
                    depth+=(text[end]=='{')-(text[end]=='}');end+=1
                return text[begin:end]
            report = next(line.strip() for line in text.splitlines() if 'heapUsage[0] =' in line)
            harness = r'''
#include <cstdint>
#include <cassert>
using VkResult = int;
constexpr int VK_SUCCESS = 0, VK_ERROR_OUT_OF_DEVICE_MEMORY = -2;
#define p_atomic_add(p,v) __atomic_fetch_add(p,v,__ATOMIC_SEQ_CST)
#define p_atomic_add_return(p,v) __atomic_add_fetch(p,v,__ATOMIC_SEQ_CST)
#define p_atomic_read(p) __atomic_load_n(p,__ATOMIC_SEQ_CST)
struct tu_memory_heap { uint64_t used=0, size=100; };
struct tu_physical_device { tu_memory_heap heap; };
struct tu_device { tu_physical_device *physical_device; };
struct tu_bo { uint64_t size; };
struct tu_device_memory { tu_bo *bo; bool lazy=false; int lazy_vma=0, lazy_mutex=0; };
void tu_bo_finish(tu_device*,tu_bo*) {}
int vk_errorf(tu_device*,int result,const char*) { return result; }
void tu_sparse_vma_finish(tu_device*,int*) {}
void mtx_destroy(int*) {}
static uint64_t tu_process_heap_used;
'''
            harness += function('tu_add_to_heap(')+'\n'+function('_tu_destroy_memory(')
            harness += '\nuint64_t usage() { struct { uint64_t heapUsage[1]; } budget, *memory_budget_props=&budget; '+report+' return budget.heapUsage[0]; }\n'
            harness += r'''
int main() {
 tu_physical_device first, second;
 tu_device vkd3d{&first}, dxvk{&second};
 tu_bo a{60}, b{50}, rejected{50};
 assert(tu_add_to_heap(&vkd3d,&a)==VK_SUCCESS);
 assert(tu_add_to_heap(&dxvk,&b)==VK_SUCCESS);
 assert(first.heap.used==60 && second.heap.used==50);
 assert(usage()==110);
 assert(tu_add_to_heap(&vkd3d,&rejected)==VK_ERROR_OUT_OF_DEVICE_MEMORY);
 assert(first.heap.used==60 && usage()==110);
 tu_device_memory ma{&a}, mb{&b};
 _tu_destroy_memory(&vkd3d,&ma);
 assert(usage()==50 && first.heap.used==0);
 _tu_destroy_memory(&dxvk,&mb);
 assert(usage()==0 && second.heap.used==0);
}
'''
            (work/'counter.cc').write_text(harness)
            subprocess.run(['g++','-std=c++17','-Wall','-Werror',str(work/'counter.cc'),'-o',str(work/'counter')],check=True,capture_output=True)
            subprocess.run([str(work/'counter')],check=True)


class BuildPlanTest(unittest.TestCase):
    def test_equivalent_upstream_is_unnecessary_not_a_patched_build(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(driver,'releases',side_effect=[[official()],[]]), \
             patch.object(driver,'official_ndk',return_value='r26d'), patch.object(driver,'checkout'), patch.object(driver,'inspect_recipe',return_value=True), \
             patch.object(driver,'prepare_recipe') as prepare:
            plan=driver.plan_build('chuross/droiddeck-patch',Path(temporary)/'plan')
            self.assertFalse(plan['build'])
            self.assertIn('overlay unnecessary',plan['status'])
            self.assertEqual(plan['official_repository'],'Droid-Deck/Drivers-CI')
            prepare.assert_not_called()

    def test_build_does_not_depend_on_a_component_release(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(driver,'releases',return_value=[official()]) as releases, \
             patch.object(driver,'official_ndk',return_value='r26d'), patch.object(driver,'checkout'), patch.object(driver,'inspect_recipe',side_effect=[False,True]), \
             patch.object(driver,'prepare_recipe',return_value='c'*40):
            plan=driver.plan_build('chuross/droiddeck-patch',Path(temporary)/'plan')
            self.assertTrue(plan['build'])
            releases.assert_called_once_with('Droid-Deck/Drivers-CI')

    def test_upstream_fixed_plan_embeds_no_temporary_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary)
            (work/'plan.json').write_text(json.dumps(dict(build=False,status='overlay unnecessary: equivalent upstream fix detected')))
            embed.stage(work,work/'assets')
            self.assertEqual([p.name for p in (work/'assets').iterdir()],['upstream-fixed.json'])


class ReleaseValidationTest(unittest.TestCase):
    def test_corresponding_source_bundle_and_plan_are_checked_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary); dist=work/'dist';dist.mkdir()
            plan=driver.provenance(official())
            plan.update(build=True,status='patch required',patched_recipe_commit='c'*40)
            plan['identity']=driver.source_identity(plan)
            (work/'plan.json').write_text(json.dumps(plan))
            tag=plan['tag']
            with tarfile.open(dist/(tag+'-source.tar.xz'),'w:xz') as packed:
                entries=[{'file':'scripts/process_heap_usage.py','targets':['android','linux']}]
                files={'series.json':json.dumps(entries).encode()}
                files.update({'scripts/'+name:(HERE/name).read_bytes() for name in ('process_heap_usage.py','process-heap-usage.patch')})
                for name, data in files.items():
                    member=tarfile.TarInfo(tag+'-source/drivers/patches/'+name);member.size=len(data)
                    packed.addfile(member,io.BytesIO(data))
            manifest={'tag':tag,'mesa':{'commit':plan['mesa_commit']},'drivers':{}}
            libs={target+'/lib.so':target.encode() for target in ('android','linux')}
            for target in ('android','linux'):
                path=target+'/lib.so'
                manifest['drivers'][target]={'library':path,'sha256':hashlib.sha256(libs[path]).hexdigest()}
            bundle=dist/(tag+'.zip')
            with zipfile.ZipFile(bundle,'w') as packed:
                packed.writestr('manifest.json',json.dumps(manifest))
                for name,data in libs.items():packed.writestr(name,data)
            checksum=dist/(tag+'.zip.sha256')
            checksum.write_text(hashlib.sha256(bundle.read_bytes()).hexdigest()+'  '+bundle.name+'\n')
            verified,assets=release.validate(work)
            self.assertEqual(verified,plan);self.assertEqual(len(assets),3)
            destination=work/'apk-assets'
            embed.stage(work,destination)
            self.assertEqual((destination/'bundle.zip').read_bytes(),bundle.read_bytes())
            self.assertEqual((destination/(tag+'-source.tar.xz')).read_bytes(),assets[2].read_bytes())
            metadata=json.loads((destination/'bundle.json').read_text())
            self.assertEqual(metadata['sha256'],hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertEqual(metadata['size'],bundle.stat().st_size)
            apk=work/'app.apk'
            with zipfile.ZipFile(apk,'w') as packed:
                for path in destination.iterdir():
                    packed.write(path,'assets/turnip-process-heap-usage/'+path.name)
            embed.verify_apk(apk)
            with zipfile.ZipFile(apk,'a') as packed:
                packed.writestr('assets/turnip-process-heap-usage/bundle.json',json.dumps(dict(metadata,sha256='0'*64)))
            with self.assertRaisesRegex(RuntimeError,'checksum'):embed.verify_apk(apk)
            checksum.write_text('tampered')
            with self.assertRaisesRegex(RuntimeError,'checksum'):release.validate(work)
            with self.assertRaisesRegex(RuntimeError,'checksum'):embed.stage(work,work/'tampered-assets')
            plan['tag']='0.4.0';(work/'plan.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(RuntimeError,'DD-Turnip'):release.validate(work)
