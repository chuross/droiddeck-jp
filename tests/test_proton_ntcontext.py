import importlib.util
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "overlay/proton-ntcontext"


def module(name):
    spec = importlib.util.spec_from_file_location("ntcontext_" + name, ROOT / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


apply = module("apply")
prepare = module("prepare_ge")
package = module("package")
release = module("release")
ORIGINAL = '''
NTSTATUS SYSCALL_API NtGetContextThread( HANDLE handle, CONTEXT *context )
{
    ARM64_NT_CONTEXT arm_ctx = { .ContextFlags = ctx_flags_x64_to_arm( context->ContextFlags ) };
    NTSTATUS status = syscall_NtGetContextThread( handle, &arm_ctx );

    if (!status) context_arm_to_x64( (ARM64EC_NT_CONTEXT *)context, &arm_ctx );
    return status;
}
'''


# Pinned DXVK QueryVideoMemoryInfo, before the budget patch.
DXVK_QUERY = """\
  HRESULT STDMETHODCALLTYPE DxgiAdapter::QueryVideoMemoryInfo(
          UINT                          NodeIndex,
          DXGI_MEMORY_SEGMENT_GROUP     MemorySegmentGroup,
          DXGI_QUERY_VIDEO_MEMORY_INFO* pVideoMemoryInfo) {
    if (NodeIndex > 0 || !pVideoMemoryInfo)
      return E_INVALIDARG;

    if (MemorySegmentGroup != DXGI_MEMORY_SEGMENT_GROUP_LOCAL
     && MemorySegmentGroup != DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL)
      return E_INVALIDARG;

    DxvkAdapterMemoryInfo memInfo = m_adapter->getMemoryHeapInfo();

    VkMemoryHeapFlags heapFlagMask = VK_MEMORY_HEAP_DEVICE_LOCAL_BIT;
    VkMemoryHeapFlags heapFlags    = 0;

    if (MemorySegmentGroup == DXGI_MEMORY_SEGMENT_GROUP_LOCAL)
      heapFlags |= VK_MEMORY_HEAP_DEVICE_LOCAL_BIT;

    pVideoMemoryInfo->Budget = 0;
    pVideoMemoryInfo->CurrentUsage = 0;
    pVideoMemoryInfo->AvailableForReservation = 0;

    for (uint32_t i = 0; i < memInfo.heapCount; i++) {
      if ((memInfo.heaps[i].heapFlags & heapFlagMask) != heapFlags)
        continue;

      pVideoMemoryInfo->Budget += memInfo.heaps[i].memoryBudget;
      pVideoMemoryInfo->CurrentUsage += memInfo.heaps[i].memoryAllocated;
      pVideoMemoryInfo->AvailableForReservation += memInfo.heaps[i].heapSize / 2;
    }

    // We don't implement reservation, but the observable
    // behaviour should match that of Windows drivers
    uint32_t segmentId = uint32_t(MemorySegmentGroup);

    pVideoMemoryInfo->CurrentReservation = m_memReservation[segmentId];
    return S_OK;
  }


"""


class ContextPatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.source = self.root / "dlls/ntdll/signal_arm64ec.c"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(ORIGINAL)

    def test_patch_returns_invalid_parameter_without_syscall_and_preserves_valid_context(self):
        if not shutil.which("cc"):
            self.skipTest("C compiler unavailable")
        apply.apply(self.root)
        harness = '''#include <assert.h>
#include <stddef.h>
#include <stdint.h>
typedef uint32_t NTSTATUS;
typedef void *HANDLE;
typedef struct { uint32_t ContextFlags, untouched; } CONTEXT;
typedef CONTEXT ARM64EC_NT_CONTEXT;
typedef CONTEXT ARM64_NT_CONTEXT;
#define SYSCALL_API
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xc000000d)
static int calls;
static NTSTATUS response;
static uint32_t ctx_flags_x64_to_arm(uint32_t flags) { return flags + 1; }
static NTSTATUS syscall_NtGetContextThread(HANDLE handle, ARM64_NT_CONTEXT *context) {
    calls++;
    assert(context->ContextFlags == 8);
    assert(context->untouched == 0);
    context->ContextFlags = 42;
    return response;
}
static void context_arm_to_x64(ARM64EC_NT_CONTEXT *target, ARM64_NT_CONTEXT *source) { *target = *source; }
'''
        harness += self.source.read_text()
        harness += '''int main(void) {
    assert(NtGetContextThread(NULL, NULL) == STATUS_INVALID_PARAMETER);
    assert(calls == 0);
    CONTEXT context = {7, 99};
    assert(NtGetContextThread(NULL, &context) == 0);
    assert(calls == 1 && context.ContextFlags == 42);
    context.ContextFlags = 7; context.untouched = 99; response = 5;
    assert(NtGetContextThread(NULL, &context) == 5);
    assert(calls == 2 && context.ContextFlags == 7 && context.untouched == 99);
    return 0;
}
'''
        file = self.root / "test.c"
        file.write_text(harness)
        subprocess.run(["cc", "-Wall", "-Werror", str(file), "-o", str(self.root / "test")], check=True)
        subprocess.run([str(self.root / "test")], check=True)

    def test_changed_source_fails_before_modification(self):
        changed = ORIGINAL.replace("ctx_flags_x64_to_arm", "upstream_changed")
        self.source.write_text(changed)
        with self.assertRaises(subprocess.CalledProcessError):
            apply.apply(self.root)
        self.assertEqual(self.source.read_text(), changed)


class DXVKBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.wine = self.root / "wine"
        self.dxvk = self.root / "dxvk"
        self.context = self.wine / "dlls/ntdll/signal_arm64ec.c"
        self.adapter = self.dxvk / "src/dxgi/dxgi_adapter.cpp"
        self.context.parent.mkdir(parents=True)
        self.adapter.parent.mkdir(parents=True)
        self.context.write_text(ORIGINAL)
        self.adapter.write_text(DXVK_QUERY)
        for source in (self.wine, self.dxvk):
            subprocess.run(["git", "init", "-q", str(source)], check=True)

    def test_dxvk_context_change_fails_before_wine_is_modified(self):
        changed = DXVK_QUERY.replace("uint32_t segmentId = uint32_t(MemorySegmentGroup);", "upstream_changed")
        self.adapter.write_text(changed)
        with self.assertRaises(subprocess.CalledProcessError):
            apply.apply(self.wine, self.dxvk)
        self.assertEqual(self.context.read_text(), ORIGINAL)
        self.assertEqual(self.adapter.read_text(), changed)

    @unittest.skipUnless(shutil.which("c++"), "C++ compiler unavailable")
    def test_actual_query_method_preserves_zero_limits_usage_and_segment_classification(self):
        apply.apply(self.wine, self.dxvk)
        harness = r'''#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
using UINT = unsigned;
using UINT64 = uint64_t;
using HRESULT = int;
using VkDeviceSize = uint64_t;
using VkMemoryHeapFlags = unsigned;
#define STDMETHODCALLTYPE
#define E_INVALIDARG -1
#define S_OK 0
constexpr unsigned VK_MEMORY_HEAP_DEVICE_LOCAL_BIT = 1;
enum DXGI_MEMORY_SEGMENT_GROUP { DXGI_MEMORY_SEGMENT_GROUP_LOCAL, DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL };
struct DXGI_QUERY_VIDEO_MEMORY_INFO { uint64_t Budget, CurrentUsage, CurrentReservation, AvailableForReservation; };
struct DxgiOptions { VkDeviceSize maxDeviceMemory = 0, maxSharedMemory = 0; };
struct DxvkAdapterMemoryInfo {
  unsigned heapCount = 0;
  struct Heap { unsigned heapFlags; uint64_t memoryBudget, memoryAllocated, heapSize; } heaps[3];
};
struct Adapter { DxvkAdapterMemoryInfo info; auto getMemoryHeapInfo() { return info; } };
struct Factory { DxgiOptions options; const DxgiOptions* GetOptions() { return &options; } };
struct DxgiAdapter {
  Adapter* m_adapter; Factory* m_factory; uint64_t m_memReservation[2] = {77, 88};
  HRESULT QueryVideoMemoryInfo(UINT, DXGI_MEMORY_SEGMENT_GROUP, DXGI_QUERY_VIDEO_MEMORY_INFO*);
};
'''
        harness += self.adapter.read_text()
        harness += r'''int main() {
  Adapter adapter; Factory factory; DxgiAdapter dxgi{&adapter, &factory};
  const uint64_t MiB = 1024*1024;
  for (uint64_t mb : {0, 1024, 2048, 3072}) {
    for (bool uma : {false, true}) {
      for (uint64_t size : {0, 512, 8192}) {
        factory.options = {mb*MiB, (mb ? mb/2 : 0)*MiB};
        adapter.info.heapCount = 3;
        adapter.info.heaps[0] = {1, size*MiB, 2700*MiB, size*MiB*4};
        adapter.info.heaps[1] = {1, size*MiB, 100*MiB, size*MiB};
        adapter.info.heaps[2] = {uma ? 1u : 0u, size*MiB, 123*MiB, size*MiB*4};
        for (auto group : {DXGI_MEMORY_SEGMENT_GROUP_LOCAL, DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL}) {
          uint64_t budget=0, usage=0, available=0;
          for (unsigned i=0;i<3;i++) {
            auto heap=adapter.info.heaps[i];
            if ((heap.heapFlags & 1) != (group == DXGI_MEMORY_SEGMENT_GROUP_LOCAL ? 1u : 0u)) continue;
            budget+=heap.memoryBudget; usage+=heap.memoryAllocated; available+=heap.heapSize/2;
          }
          DXGI_QUERY_VIDEO_MEMORY_INFO info{};
          assert(dxgi.QueryVideoMemoryInfo(0, group, &info) == S_OK);
          uint64_t limit=group == DXGI_MEMORY_SEGMENT_GROUP_LOCAL ? factory.options.maxDeviceMemory : factory.options.maxSharedMemory;
          auto result=limit ? std::min(budget,limit) : budget;
          assert(info.Budget==result);
          assert(info.CurrentUsage==usage);
          assert(info.CurrentReservation==dxgi.m_memReservation[group]);
          assert(info.AvailableForReservation==(limit ? std::min(available,result) : available));
        }
      }
    }
  }
  // Independently disabled segment limits and reservation availability below budget.
  adapter.info.heapCount=2;
  adapter.info.heaps[0]={1, 8192*MiB, 2700*MiB, 1024*MiB};
  adapter.info.heaps[1]={0, 8192*MiB, 123*MiB, 1024*MiB};
  for (bool local : {false,true}) {
    factory.options={local ? 2048*MiB : 0, local ? 0 : 2048*MiB};
    for (auto group : {DXGI_MEMORY_SEGMENT_GROUP_LOCAL,DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL}) {
      DXGI_QUERY_VIDEO_MEMORY_INFO info{};
      dxgi.QueryVideoMemoryInfo(0,group,&info);
      assert(info.Budget==((local == (group==DXGI_MEMORY_SEGMENT_GROUP_LOCAL)) ? 2048*MiB : 8192*MiB));
      assert(info.AvailableForReservation==512*MiB);
    }
  }
  DXGI_QUERY_VIDEO_MEMORY_INFO info{1,2,3,4}, before=info;
  assert(dxgi.QueryVideoMemoryInfo(1,DXGI_MEMORY_SEGMENT_GROUP_LOCAL,&info)==E_INVALIDARG);
  assert(!memcmp(&info,&before,sizeof(info)));
  assert(dxgi.QueryVideoMemoryInfo(0,static_cast<DXGI_MEMORY_SEGMENT_GROUP>(2),&info)==E_INVALIDARG);
  assert(!memcmp(&info,&before,sizeof(info)));
  assert(dxgi.QueryVideoMemoryInfo(0,DXGI_MEMORY_SEGMENT_GROUP_LOCAL,nullptr)==E_INVALIDARG);
}
'''
        file = self.root / "query-test.cpp"
        file.write_text(harness)
        binary = self.root / "query-test"
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Werror", str(file), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


class BuildEvidenceTest(unittest.TestCase):
    def test_readonly_release_files_are_updated_with_original_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ntdll.dll'
            path.write_bytes(b'original')
            path.chmod(0o444)
            package.write_preserving_mode(path, b'patched')
            self.assertEqual(path.read_bytes(), b'patched')
            self.assertEqual(path.stat().st_mode & 0o7777, 0o444)

    def test_arm_release_directory_and_library_layout_are_discovered_unambiguously(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tree = root / 'GE-Proton11-7-aarch64'
            dll = tree / 'files/lib-arm64/wine/aarch64-windows/ntdll.dll'
            dll.parent.mkdir(parents=True)
            dll.write_bytes(b'ntdll')
            (tree / 'compatibilitytool.vdf').write_text('GE-Proton11-7')
            self.assertEqual(package.base_tree(root), tree)
            self.assertEqual(package.original_ntdll(tree), dll)
            (tree / 'files/lib').symlink_to('lib-arm64', target_is_directory=True)
            self.assertEqual(package.original_ntdll(tree), dll)
            other = tree / 'files/lib64/wine/aarch64-windows/ntdll.dll'
            other.parent.mkdir(parents=True)
            other.write_bytes(b'unexpected')
            with self.assertRaises(ValueError):
                package.original_ntdll(tree)

    def test_binary_evidence_requires_null_branch_before_flags_load(self):
        function = '0000000000000000 <#NtGetContextThread>:\n  0: cbz x1, 0x90\n  4: ldr w8, [x1, #0x30]\n'
        package.check_disassembly(function)
        with self.assertRaises(ValueError):
            package.check_disassembly(function.replace('cbz x1, 0x90', 'nop'))
        with self.assertRaises(ValueError):
            package.check_disassembly('0000000000000000 <#NtGetContextThread>:\n  0: ldr w8, [x1, #0x30]\n  4: cbz x1, 0x90\n')

    def test_llvm_section_label_uses_syscall_relocation_to_identify_wrapper(self):
        function = ('Disassembly of section .text:\n\n0000000000000000 <.text>:\n'
                    ' 24: b4000333 cbz x19, 0x88 <.text+0x88>\n'
                    ' 28: b9403268 ldr w8, [x19, #0x30]\n'
                    ' 6c: 94000000 bl 0x6c <.text+0x6c>\n'
                    '     000000000000006c: IMAGE_REL_ARM64_BRANCH26 syscall_NtGetContextThread\n')
        package.check_disassembly(function)
        for changed in (function.replace('syscall_NtGetContextThread', 'unrelated'),
                        function.replace('cbz x19', 'cbz x20')):
            with self.assertRaises(ValueError):
                package.check_disassembly(changed)

    def test_preparation_keeps_ge_wine_patch_order_without_other_builds(self):
        source = '''#!/bin/bash
apply_patch() { patch -Np1 < "$1"; }
### (1) PREP SECTION ###
    pushd dxvk
    irrelevant
    popd
    pushd wineopenxr
    apply_patch "../patches/wineopenxr/a.patch"
    popd
### (2) WINE PATCHING ###
    pushd wine
    ../wine-staging/staging/patchinstall.py DESTDIR="." --all --no-autoconf
    apply_patch "../patches/proton/a.patch"
    autoreconf -f
    ./tools/make_requests
    popd
### END WINE PATCHING ###
'''
        result = prepare.wine_preparation(source)
        self.assertNotIn('irrelevant', result)
        self.assertLess(result.index('patches/wineopenxr'), result.index('pushd wine\n'))
        self.assertLess(result.index('patchinstall.py'), result.index('patches/proton'))
        with self.assertRaises(RuntimeError):
            prepare.wine_preparation(source.replace('### (2) WINE PATCHING ###', 'changed'))

    def test_pe_exports_and_arm64x_metadata_are_read_from_actual_headers(self):
        data = bytearray(4096)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 0x80)
        data[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH', data, 0x84, 0xaa64, 1)
        struct.pack_into('<H', data, 0x94, 240)
        optional = 0x98
        struct.pack_into('<H', data, optional, 0x20b)
        struct.pack_into('<II', data, optional + 112, 0x1000, 40)
        struct.pack_into('<II', data, optional + 192, 0x1300, 208)
        struct.pack_into('<IIII', data, optional + 240 + 8, 0x1000, 0x1000, 0x1000, 0x200)
        struct.pack_into('<IIIIII', data, 0x210, 1, 1, 1, 0x1100, 0x1110, 0x1120)
        struct.pack_into('<I', data, 0x310, 0x1200)
        struct.pack_into('<H', data, 0x320, 0)
        data[0x400:0x413] = b'NtGetContextThread\0'
        struct.pack_into('<Q', data, 0x500 + 200, 0x180001500)
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'ntdll.dll'
            file.write_bytes(data)
            result = package.pe_interface(file)
        self.assertEqual(result['machine'], 0xaa64)
        self.assertEqual(result['exports'], {'NtGetContextThread': 1})
        self.assertTrue(result['arm64x_metadata'])


def synthetic_pe(export="CreateDXGIFactory", machine=0xaa64, hybrid=True, marker=b"original"):
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, 240)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20b)
    struct.pack_into("<II", data, optional + 112, 0x1000, 40)
    struct.pack_into("<II", data, optional + 192, 0x1300, 208)
    struct.pack_into("<IIII", data, optional + 240 + 8, 0x1000, 0x1000, 0x1000, 0x200)
    struct.pack_into("<IIIIII", data, 0x210, 1, 1, 1, 0x1100, 0x1110, 0x1120)
    struct.pack_into("<I", data, 0x310, 0x1200)
    struct.pack_into("<H", data, 0x320, 0)
    name = export.encode() + b"\0"
    data[0x400:0x400 + len(name)] = name
    struct.pack_into("<Q", data, 0x500 + 200, 0x180001500 if hybrid else 0)
    return bytes(data) + marker


class DXVKPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        self.tree = self.task / "base/GE-Proton11-7-aarch64"
        self.original = self.tree / "files/lib/wine/dxvk/aarch64-windows/dxgi.dll"
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(synthetic_pe())
        self.original.chmod(0o555)
        self.ntdll = self.tree / "files/lib/wine/aarch64-windows/ntdll.dll"
        self.ntdll.parent.mkdir(parents=True)
        self.ntdll.write_bytes(synthetic_pe(export="NtGetContextThread"))
        self.ntdll.chmod(0o444)
        (self.tree / "compatibilitytool.vdf").write_text("GE-Proton11-7")
        self.results = self.task / "results"
        self.rebuilt = self.results / "dxvk/dxgi.dll"
        self.rebuilt.parent.mkdir(parents=True)
        self.rebuilt.write_bytes(synthetic_pe(marker=b"patched"))
        (self.rebuilt.parent / "patches.diff").write_text((ROOT / "dxvk-memory-budget.patch").read_text())
        (self.results / "ntdll.dll").write_bytes(synthetic_pe(export="NtGetContextThread", marker=b"patched"))
        (self.results / "signal-arm64ec-disassembly.txt").write_text(
            "0000000000000000 <#NtGetContextThread>:\n  0: cbz x1, 0x90\n  4: ldr w8, [x1, #0x30]\n")
        (self.task / "sdk-digests.json").write_text("[]")
        subprocess.run(["tar", "-czf", str(self.task / "base.tar.gz"), "-C", str(self.task / "base"), self.tree.name], check=True)
        self.lock = json.loads((ROOT / "build.lock.json").read_text())
        self.lock["base_sha256"] = package.sha256(self.task / "base.tar.gz")
        self.lock_path = self.task / "lock.json"
        self.lock_path.write_text(json.dumps(self.lock))

    def test_package_replaces_ntdll_and_native_dxvk_and_records_hashes_modes_architecture(self):
        before = package.sha256(self.original)
        package.package(self.task, "manager", self.lock_path)
        tree = self.task / "unpacked" / self.lock["build_name"]
        dxvk = tree / self.original.relative_to(self.tree)
        ntdll = tree / self.ntdll.relative_to(self.tree)
        self.assertEqual(dxvk.read_bytes(), self.rebuilt.read_bytes())
        self.assertEqual(ntdll.read_bytes(), (self.results / "ntdll.dll").read_bytes())
        self.assertEqual(dxvk.stat().st_mode & 0o7777, 0o555)
        self.assertEqual(ntdll.stat().st_mode & 0o7777, 0o444)
        metadata = json.loads((self.task / "dist/proton-build.json").read_text())
        self.assertEqual(metadata["features"], ["ntcontext-null-fix", "dxvk-dxgi-memory-budget-cap"])
        self.assertEqual(metadata["original_dxvk_dxgi_sha256"], before)
        self.assertEqual(metadata["rebuilt_dxvk_dxgi_sha256"], package.sha256(self.rebuilt))
        self.assertEqual(metadata["dxvk_commit"], self.lock["dxvk_commit"])
        self.assertEqual(metadata["dxvk_dxgi_pe_machine"], "0xaa64")
        self.assertTrue(metadata["dxvk_dxgi_arm64x_metadata"])
        self.assertEqual(metadata["dxvk_dxgi_archive_path"], str(self.original.relative_to(self.tree)))
        self.assertTrue((tree / "droiddeck-ntcontext-build.json").is_file())
        self.assertNotIn("droiddeck-dxgi-memory-budget", json.dumps(metadata))

    def test_architecture_mismatch_fails_before_ntdll_replacement(self):
        self.rebuilt.write_bytes(synthetic_pe(machine=0x8664))
        with self.assertRaisesRegex(ValueError, "architecture mismatch"):
            package.package(self.task, "manager", self.lock_path)
        original = self.task / "unpacked" / self.tree.name / self.ntdll.relative_to(self.tree)
        self.assertEqual(original.read_bytes(), self.ntdll.read_bytes())
        self.assertFalse((self.task / "dist").exists())

    def test_pe_machine_and_hybrid_metadata_are_both_required(self):
        for machine, hybrid in ((0xa641, True), (0x8664, True), (0xaa64, False)):
            self.rebuilt.write_bytes(synthetic_pe(machine=machine, hybrid=hybrid))
            with self.assertRaisesRegex(ValueError, "architecture mismatch"):
                package.rebuilt_dxvk(self.results)

    def test_library_alias_is_deduplicated_but_distinct_candidates_fail(self):
        (self.tree / "files/lib-arm64").symlink_to("lib", target_is_directory=True)
        self.assertEqual(package.original_dxvk(self.tree), self.original)
        other = self.tree / "files/lib64/wine/dxvk/aarch64-windows/dxgi.dll"
        other.parent.mkdir(parents=True)
        other.write_bytes(synthetic_pe())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            package.original_dxvk(self.tree)

    def test_ambiguous_rebuilt_outputs_fail(self):
        other = self.rebuilt.parent / "other/dxgi.dll"
        other.parent.mkdir()
        other.write_bytes(synthetic_pe())
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            package.rebuilt_dxvk(self.results)

    def test_original_archive_checksum_is_verified_before_extraction(self):
        self.lock["base_sha256"] = "0" * 64
        self.lock_path.write_text(json.dumps(self.lock))
        with self.assertRaisesRegex(ValueError, "checksum"):
            package.package(self.task, "manager", self.lock_path)
        self.assertFalse((self.task / "unpacked").exists())

    def test_unchanged_rebuilt_hash_is_rejected(self):
        self.rebuilt.write_bytes(self.original.read_bytes())
        with self.assertRaisesRegex(ValueError, "unchanged"):
            package.package(self.task, "manager", self.lock_path)

    def test_changed_exports_are_rejected(self):
        self.rebuilt.write_bytes(synthetic_pe(export="CreateDXGIFactory1"))
        with self.assertRaises(ValueError):
            package.package(self.task, "manager", self.lock_path)

    def test_patch_evidence_is_required(self):
        (self.rebuilt.parent / "patches.diff").write_text("unpatched source")
        with self.assertRaisesRegex(ValueError, "evidence"):
            package.package(self.task, "manager", self.lock_path)


class GEReleaseTest(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "build.lock.json").read_text())
        self.resolve = Mock(return_value=("a" * 40, "b" * 40, "d" * 40, self.lock["sdk_image"]))
        self.upstream = self.ge_release()

    def ge_release(self, tag="GE-Proton11-8", **fields):
        result = {"tag_name": tag, "published_at": "2026-10-11T00:00:00Z",
                  "assets": [{"name": tag + "-aarch64.tar.gz", "digest": "sha256:" + "c" * 64,
                              "browser_download_url": "https://github.com/" + release.REPOSITORY
                              + "/releases/download/" + tag + "/" + tag + "-aarch64.tar.gz"}]}
        result.update(fields)
        return result

    def plan(self, event="schedule", existing=None):
        return release.make_plan(self.lock, self.upstream, event, existing, self.resolve)

    def published(self, plan):
        return {"isDraft": False, "body": plan["source_marker"],
                "assets": [{"name": name} for name in release.asset_names(plan["lock"])]}

    def test_only_stable_arm64_releases_are_selected_in_numeric_order(self):
        selected = release.latest_arm_release([
            self.ge_release("GE-Proton11-9"), self.ge_release("GE-Proton11-10"),
            self.ge_release("GE-Proton11-11", assets=[]),
            self.ge_release("GE-Proton11-12", prerelease=True),
            self.ge_release("GE-Proton11-13", draft=True),
            self.ge_release("GE-Proton11-14", published_at=None)])
        self.assertEqual(selected["tag_name"], "GE-Proton11-10")
        with self.assertRaises(RuntimeError):
            release.latest_arm_release([self.ge_release(assets=[])])

    def test_new_ge_gets_exact_commits_checksum_sdk_and_independent_tag(self):
        plan = self.plan()
        self.assertTrue(plan["build"])
        self.assertEqual(plan["tag"], "GE-Proton11-8-droiddeck")
        self.assertEqual(plan["lock"]["proton_commit"], "a" * 40)
        self.assertEqual(plan["lock"]["wine_commit"], "b" * 40)
        self.assertEqual(plan["lock"]["dxvk_commit"], "d" * 40)
        self.assertEqual(plan["lock"]["base_sha256"], "c" * 64)
        self.resolve.assert_called_once_with("GE-Proton11-8")
        self.assertEqual(plan["original_lock"], self.lock)
        self.assertEqual(self.lock["base_name"], "GE-Proton11-7")

    def test_unchanged_complete_release_skips_and_manual_rebuilds(self):
        plan = self.plan()
        self.lock = plan["lock"]
        complete = self.published(plan)
        self.assertFalse(self.plan(existing=complete)["build"])
        for event in ("push", "workflow_dispatch"):
            self.assertTrue(self.plan(event, complete)["build"])

    def test_failed_incomplete_or_changed_build_retries(self):
        complete = self.published(self.plan())
        for changed in (dict(complete, isDraft=True), dict(complete, assets=complete["assets"][:-1]),
                        dict(complete, body="previous source"), None):
            self.assertTrue(self.plan(existing=changed)["build"])

    def test_unrecorded_candidate_retries_even_if_publication_completed(self):
        plan = self.plan()
        self.assertTrue(self.plan(existing=self.published(plan))["build"])

    def test_lock_record_does_not_change_source_identity(self):
        plan = self.plan()
        with patch.object(release, "candidate_lock", return_value=plan["lock"]):
            after = release.make_plan(plan["lock"], self.upstream, "schedule", self.published(plan))
        self.assertFalse(after["build"])

    def test_older_missing_digest_and_wrong_url_fail_without_resolving(self):
        self.upstream = self.ge_release("GE-Proton11-6")
        with self.assertRaisesRegex(RuntimeError, "roll back"):
            self.plan()
        for key, value in (("digest", None), ("digest", "sha256:invalid"),
                           ("browser_download_url", "https://example.org/GE.tar.gz")):
            self.upstream = self.ge_release()
            self.upstream["assets"][0][key] = value
            with self.assertRaises(RuntimeError):
                self.plan()
        self.resolve.assert_not_called()

    def test_resolve_annotated_tag_uses_wine_submodule_and_arm_sdk_at_exact_commit(self):
        makefile = ("else ifeq ($(TARGET_ARCH),arm64)\n"
                    "\tSTEAMRT_IMAGE ?= " + self.lock["sdk_image"] + "\nendif\n")
        responses = [{"object": {"type": "tag", "sha": "d" * 40}},
                     {"object": {"type": "commit", "sha": "a" * 40}},
                     {"tree": [{"path": "wine", "mode": "160000", "type": "commit", "sha": "b" * 40}, {"path": "dxvk", "mode": "160000", "type": "commit", "sha": "d" * 40}]},
                     {"content": base64.b64encode(makefile.encode()).decode()}]
        with patch.object(release, "api", side_effect=responses) as api:
            self.assertEqual(release.resolve_source("GE-Proton11-8"),
                             ("a" * 40, "b" * 40, "d" * 40, self.lock["sdk_image"]))
        self.assertTrue(api.call_args_list[-1].args[0].endswith("?ref=" + "a" * 40))
        with self.assertRaises(RuntimeError):
            release.arm64_sdk(makefile.replace("arm64)", "other)"))
        with self.assertRaises(RuntimeError):
            release.arm64_sdk(makefile.replace("registry.gitlab.steamos.cloud", "example.org"))

    def test_unexpected_wine_tree_is_rejected(self):
        for tree in ({"tree": []}, {"truncated": True, "tree": []}):
            with patch.object(release, "api", side_effect=[
                    {"object": {"type": "commit", "sha": "a" * 40}}, tree]):
                with self.assertRaisesRegex(RuntimeError, "submodule"):
                    release.resolve_source("GE-Proton11-8")

    def prepare_assets(self, directory, plan):
        for name in release.asset_names(plan["lock"]):
            (directory / name).write_text("evidence")
        (directory / "proton-build.json").write_text(json.dumps(
            dict(plan["lock"], manager_commit="e" * 40)))
        (directory / "SHA256SUMS").write_text("".join(
            hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n"
            for name in release.asset_names(plan["lock"])[:2]))

    def test_publish_uploads_source_checksums_and_evidence_before_publication_without_apk_latest(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            with patch.object(release, "existing_release", return_value=None), \
                    patch.object(release, "run") as run:
                release.publish("owner/repo", "e" * 40, plan, directory)
        calls = [call.args for call in run.call_args_list]
        self.assertEqual([args[2] for args in calls], ["create", "upload", "edit"])
        self.assertIn("GE-Proton11-8-droiddeck", calls[0])
        self.assertIn("--draft", calls[0])
        self.assertIn("--latest=false", calls[-1])
        self.assertTrue(any("wine-source.tar.xz" in arg for arg in calls[1]))
        self.assertFalse(any("DroidDeck" in arg for call in calls for arg in call))

    def test_wrong_provenance_or_checksums_prevent_publication(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            with patch.object(release, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "provenance"):
                    release.publish("owner/repo", "f" * 40, plan, directory)
                (directory / release.asset_names(plan["lock"])[0]).write_text("changed")
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    release.publish("owner/repo", "e" * 40, plan, directory)
                run.assert_not_called()

    def test_upload_failure_leaves_release_unpublished(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            with patch.object(release, "existing_release", return_value=None), \
                    patch.object(release, "run", side_effect=[None, RuntimeError("upload failed")]) as run:
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    release.publish("owner/repo", "e" * 40, plan, directory)
                self.assertEqual(run.call_count, 2)

    def test_publish_failure_does_not_record_candidate(self):
        with patch.object(release, "publish", side_effect=RuntimeError("build upload failed")), \
                patch.object(release, "record") as record, \
                patch.object(release.argparse.ArgumentParser, "parse_args",
                             return_value=Mock(action="publish", repository="owner/repo",
                                               manager="e" * 40, plan=Mock(read_text=lambda: json.dumps(self.plan())),
                                               directory=Path("/unused"))):
            with self.assertRaises(RuntimeError):
                release.main()
            record.assert_not_called()

    def test_success_records_candidate_with_blob_lease_and_concurrent_main_does_not(self):
        plan = self.plan()
        remote = {"sha": "blob", "content": base64.b64encode(json.dumps(self.lock).encode()).decode()}
        with patch.object(release, "api", side_effect=[
                {"object": {"sha": "e" * 40}}, remote]), patch.object(release, "run") as run:
            self.assertTrue(release.record("owner/repo", "e" * 40, plan))
            args = run.call_args.args
            self.assertIn("repos/owner/repo/contents/overlay/proton-ntcontext/build.lock.json", args)
        with patch.object(release, "api", return_value={"object": {"sha": "f" * 40}}), \
                patch.object(release, "run") as run:
            self.assertFalse(release.record("owner/repo", "e" * 40, plan))
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
