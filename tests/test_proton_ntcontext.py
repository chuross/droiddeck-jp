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


class ExperimentalPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.task = Path(self.temp.name)
        self.lock = json.loads((ROOT / "build.lock.json").read_text())
        self.lock_path = self.task / "build.lock.json"
        self.lock_path.write_text(json.dumps(self.lock))
        self.tree = self.task / "base" / self.lock["base_name"]
        self.tree.mkdir(parents=True)
        for name in ("proton", "toolmanifest.vdf", "LICENSE"):
            (self.tree / name).write_text("upstream")
        (self.tree / "compatibilitytool.vdf").write_text(self.lock["build_name"])
        (self.tree / "proton").chmod(0o755)
        binaries = self.tree / "files/bin-arm64"
        binaries.mkdir(parents=True)
        header = b"\x7fELF\x02\x01" + b"\0" * 12 + (183).to_bytes(2, "little")
        for name in ("wine", "wineserver"):
            (binaries / name).write_bytes(header)
            (binaries / name).chmod(0o755)
        for directory, name, export in (("aarch64-windows", "ntdll.dll", "NtGetContextThread"),
                                         ("dxvk/aarch64-windows", "dxgi.dll", "CreateDXGIFactory")):
            dll = self.tree / "files/lib/wine" / directory / name
            dll.parent.mkdir(parents=True)
            dll.write_bytes(synthetic_pe(export=export))
        results = self.task / "results"
        results.mkdir()
        (results / "signal-arm64ec-disassembly.txt").write_text(
            "0000000000000000 <#NtGetContextThread>:\n  0: cbz x1, 0x90\n  4: ldr w8, [x1, #0x30]\n")
        for name in ("wine", "dxvk"):
            (results / (name + "-patches.diff")).write_text("patch")
        (self.task / "sdk-digests.json").write_text('["sdk@sha256:digest"]')
        (self.task / "proton").mkdir()
        (self.task / "proton/LICENSE.proton").write_text("Valve license")
        (self.task / "dist").mkdir()
        (self.task / "dist" / (self.lock["build_name"] + "-wine-source.tar.xz")).write_bytes(b"source")
        self.lock["base_version"] = "1790000000 experimental-11.0-20261001-arm64"
        (self.tree / "version").write_text(self.lock["base_version"] + "\n")
        (self.task / "proton/compatibilitytool.vdf.template").write_text('"##BUILD_NAME##" "##INTERNAL_TOOL_NAME##"')
        (results / "ntdll.dll").write_bytes(synthetic_pe(export="NtGetContextThread", marker=b"patched"))
        (results / "dxvk").mkdir()
        (results / "dxvk/dxgi.dll").write_bytes(synthetic_pe(marker=b"patched"))
        subprocess.run(["tar", "-cJf", str(self.task / "base.tar.xz"), "-C", str(self.task / "base"), self.lock["base_name"]], check=True)
        self.lock["base_sha256"] = package.sha256(self.task / "base.tar.xz")
        self.lock_path.write_text(json.dumps(self.lock))
        self.heads = [self.lock[key] + "\n" for key in ("proton_commit", "wine_commit", "dxvk_commit")]

    def invoke(self):
        actual_run = subprocess.run
        with patch.object(package.subprocess, "check_output", side_effect=self.heads), \
                patch.object(package.subprocess, "run", wraps=subprocess.run) as run:
            def execute(args, **kwargs):
                if "apply" in args:
                    return subprocess.CompletedProcess(args, 0)
                return actual_run(args, **kwargs)
            run.side_effect = execute
            package.package(self.task, "manager", self.lock_path)

    def test_stock_dlls_are_replaced_with_source_and_validated_arm64x(self):
        importer = __import__("runpy").run_path(str(ROOT.parent / "proton-import/droiddeck-proton-import"))
        with self.assertRaises(importer["ImportProblem"]) as failure:
            importer["validate"](self.tree)
        self.assertEqual(failure.exception.code, "not_arm64")
        self.invoke()
        packaged = self.task / "unpacked" / self.lock["build_name"]
        metadata = json.loads((packaged / "droiddeck-ntcontext-build.json").read_text())
        self.assertEqual(metadata["proton_commit"], self.lock["proton_commit"])
        self.assertEqual(metadata["dxvk_dxgi_architecture"], "ARM64X")
        self.assertTrue((packaged / "droiddeck-source/source.tar.xz").is_file())
        self.assertEqual((packaged / "LICENSE.proton").read_text(), "Valve license")
        self.assertTrue((self.task / "dist" / release.asset_names(self.lock)[0]).is_file())
        self.assertEqual((packaged / "files/bin/wine").read_bytes(),
                         (packaged / "files/bin-arm64/wine").read_bytes())
        importer["validate"](packaged)

    def test_base_checksum_mismatch_prevents_extraction(self):
        self.lock["base_sha256"] = "0" * 64
        self.lock_path.write_text(json.dumps(self.lock))
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.invoke()
        self.assertFalse((self.task / "unpacked").exists())

    def test_mismatched_dll_exports_prevent_replacement(self):
        (self.task / "results/ntdll.dll").write_bytes(synthetic_pe(export="Wrong"))
        with self.assertRaisesRegex(ValueError, "interface"):
            self.invoke()
        stock = self.task / "unpacked" / self.lock["base_name"]
        self.assertEqual(package.original_ntdll(stock).read_bytes(), package.original_ntdll(self.tree).read_bytes())

    def test_source_mismatch_prevents_packaging(self):
        self.heads[1] = "0" * 40
        with self.assertRaisesRegex(ValueError, "source commit"):
            self.invoke()
        self.assertFalse((self.task / "unpacked").exists())

    def test_wrong_dxvk_architecture_prevents_packaging(self):
        (self.task / "results/dxvk/dxgi.dll").write_bytes(synthetic_pe(machine=0x8664))
        with self.assertRaisesRegex(ValueError, "architecture mismatch"):
            self.invoke()
        self.assertEqual(package.original_ntdll(self.task / "unpacked" / self.lock["base_name"]).read_bytes(),
                         package.original_ntdll(self.tree).read_bytes())

    def test_missing_patch_evidence_prevents_packaging(self):
        (self.task / "results/wine-patches.diff").write_text("")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.invoke()


class UpstreamMatchTest(unittest.TestCase):
    def test_matching_sources_pass_and_changed_wine_is_rejected(self):
        tree = {"tree": [
            {"path": "wine", "mode": "160000", "type": "commit", "sha": "b" * 40},
            {"path": "dxvk", "mode": "160000", "type": "commit", "sha": "d" * 40}]}
        for wine, passes in (("b" * 40, True), ("c" * 40, False)):
            with patch.object(release, "api", side_effect=[
                    {"object": {"type": "commit", "sha": "a" * 40}}, tree]):
                if passes:
                    self.assertEqual(release.check_upstream("plop-11.0-20261001", wine, "d" * 40), "a" * 40)
                else:
                    with self.assertRaisesRegex(RuntimeError, "differs"):
                        release.check_upstream("plop-11.0-20261001", wine, "d" * 40)


class ExperimentalReleaseTest(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "build.lock.json").read_text())
        self.resolve = Mock(return_value=("a" * 40, "b" * 40, "d" * 40, self.lock["sdk_image"]))
        self.upstream = {"tag_name": "plop-11.0-20261001", "published_at": "2026-10-01",
                         "assets": [{"name": "proton-plop-11.0-20261001-arm64.tar.xz",
                                     "digest": "sha256:" + "c" * 64,
                                     "browser_download_url": "https://github.com/loathingKernel/Proton/releases/download/plop-11.0-20261001/proton-plop-11.0-20261001-arm64.tar.xz"}]}
        checker = patch.object(release, "check_upstream", return_value="e" * 40)
        checker.start()
        self.addCleanup(checker.stop)

    def ge_release(self):
        return None

    def plan(self, event="schedule", existing=None):
        return release.make_plan(self.lock, self.upstream, event, existing, self.resolve, target_tag="0.3.2")

    def published(self, plan):
        return {"isDraft": False, "body": plan["source_marker"],
                "assets": [{"name": name} for name in release.asset_names(plan["lock"])]}

    def test_experimental_branch_resolves_exact_source_and_removes_ge_archive(self):
        plan = self.plan()
        self.assertTrue(plan["build"])
        self.assertEqual(plan["tag"], "0.3.2")
        self.assertEqual(plan["lock"]["proton_commit"], "a" * 40)
        self.assertEqual(plan["lock"]["wine_commit"], "b" * 40)
        self.assertEqual(plan["lock"]["dxvk_commit"], "d" * 40)
        self.assertIn("loathingKernel/Proton", plan["lock"]["base_archive"])
        self.resolve.assert_called_once_with("plop-11.0-20261001")
        self.assertEqual(plan["lock"]["base_sha256"], "c" * 64)

    def test_unchanged_complete_release_skips_and_manual_rebuilds(self):
        plan = self.plan()
        self.lock = plan["lock"]
        complete = self.published(plan)
        self.assertFalse(self.plan(existing=complete)["build"])
        self.assertFalse(self.plan("workflow_run", complete)["build"])
        for event in ("push", "workflow_dispatch"):
            self.assertTrue(self.plan(event, complete)["build"])

    def test_failed_incomplete_or_changed_build_retries(self):
        complete = self.published(self.plan())
        for changed in (dict(complete, isDraft=True), dict(complete, assets=complete["assets"][:-1]),
                        dict(complete, body="previous source"), None):
            self.assertTrue(self.plan(existing=changed)["build"])
            self.assertTrue(self.plan("workflow_run", changed)["build"])

    def test_unrecorded_candidate_retries_even_if_publication_completed(self):
        plan = self.plan()
        self.assertTrue(self.plan(existing=self.published(plan))["build"])

    def test_lock_record_does_not_change_source_identity(self):
        plan = self.plan()
        with patch.object(release, "candidate_lock", return_value=plan["lock"]):
            after = release.make_plan(plan["lock"], self.upstream, "schedule", self.published(plan))
        self.assertFalse(after["build"])

    def test_invalid_commit_rejected(self):
        self.resolve.return_value = ("invalid", "b" * 40, "d" * 40, self.lock["sdk_image"])
        with self.assertRaisesRegex(RuntimeError, "commit"):
            self.plan()

    def test_missing_binary_digest_is_rejected(self):
        self.upstream["assets"][0]["digest"] = None
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.plan()
        self.resolve.assert_not_called()

    def test_resolve_branch_uses_wine_submodule_and_arm_sdk_at_exact_commit(self):
        makefile = ("else ifeq ($(TARGET_ARCH),arm64)\n"
                    "\tSTEAMRT_IMAGE ?= " + self.lock["sdk_image"] + "\nendif\n")
        responses = [{"object": {"type": "commit", "sha": "a" * 40}},
                     {"tree": [{"path": "wine", "mode": "160000", "type": "commit", "sha": "b" * 40}, {"path": "dxvk", "mode": "160000", "type": "commit", "sha": "d" * 40}]},
                     {"content": base64.b64encode(makefile.encode()).decode()}]
        with patch.object(release, "api", side_effect=responses) as api:
            self.assertEqual(release.resolve_source("plop-11.0-20261001"),
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
                    release.resolve_source("plop-11.0-20261001")

    def prepare_assets(self, directory, plan):
        for name in release.asset_names(plan["lock"]) + [plan["lock"]["build_name"] + "-wine-source.tar.xz"]:
            (directory / name).write_text("evidence")
        (directory / "proton-build.json").write_text(json.dumps(
            dict(plan["lock"], manager_commit="e" * 40)))
        (directory / "SHA256SUMS").write_text("".join(
            hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n"
            for name in release.asset_names(plan["lock"]) + [plan["lock"]["build_name"] + "-wine-source.tar.xz"]))

    def test_publish_overwrites_proton_on_apk_release_preserving_apk_notes_and_status(self):
        plan = self.plan()
        existing = {"isDraft": False, "body": "- 本家リリース: 0.3.2\n- 本家コミット: commit\nAPK SHA-256: digest\n<!-- jp-source: " + "a" * 64 + " -->\n<!-- proton:start -->\nold description\n<!-- proton:end -->",
                    "assets": [{"name": "DroidDeck-Patch.apk"}, {"name": "SHA256SUMS"},
                               {"name": "GE-Proton11-7-droiddeck-aarch64.tar.gz"},
                               {"name": "GE-Proton11-7-droiddeck-wine-source.tar.xz"},
                               {"name": "Proton-SHA256SUMS"}, {"name": "proton-build.json"},
                               {"name": "signal-arm64ec-disassembly.txt"}]}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            notes = []
            def invoke(*args, **kwargs):
                if "--notes-file" in args:
                    notes.append(Path(args[args.index("--notes-file") + 1]).read_text())
            with patch.object(release, "current_droiddeck_release", return_value={"tag_name": "0.3.2"}), \
                    patch.object(release, "existing_release", return_value=existing), \
                    patch.object(release, "run", side_effect=invoke) as run:
                release.publish("owner/repo", "e" * 40, plan, directory)
        calls = [call.args for call in run.call_args_list]
        self.assertEqual([args[2] for args in calls], ["upload", "edit"] + ["delete-asset"] * 5)
        self.assertTrue(all("0.3.2" in args for args in calls))
        self.assertIn("--clobber", calls[0])
        self.assertEqual(calls[0][6:], (str(directory / release.asset_names(plan["lock"])[0]), "--clobber"))
        self.assertFalse(any(arg in ("--draft", "--latest", "--title", "--draft=false") for call in calls for arg in call))
        self.assertIn("本家リリース: 0.3.2", notes[0])
        self.assertIn("<!-- jp-source: " + "a" * 64 + " -->", notes[0])
        self.assertNotIn("old description", notes[0])
        self.assertNotIn("ProtonGE:", notes[0])
        self.assertIn(plan["source_marker"], notes[0])
        self.assertFalse(any(arg.endswith("/DroidDeck-Patch.apk") or arg.endswith("/SHA256SUMS") for arg in calls[0]))

    def test_current_release_selects_latest_published_apk_and_never_proton_release(self):
        releases = [{"tag_name": "0.3.2", "published_at": "2026-10-08", "assets": [{"name": "DroidDeck-Patch.apk"}]},
                    {"tag_name": "0.3.3", "published_at": "2026-10-09", "assets": [{"name": "DroidDeck-Patch.apk"}]},
                    {"tag_name": "GE-Proton11-8", "published_at": "2026-10-10", "assets": [{"name": "proton.tar.gz"}]},
                    {"tag_name": "0.3.4", "published_at": "2026-10-11", "draft": True, "assets": [{"name": "DroidDeck-Patch.apk"}]}]
        with patch.object(release, "api", return_value=releases):
            self.assertEqual(release.current_droiddeck_release("owner/repo")["tag_name"], "0.3.3")
        with patch.object(release, "api", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "must not create"):
                release.current_droiddeck_release("owner/repo")

    def test_proton_notes_keep_only_three_visible_fields_and_hidden_markers(self):
        body = "- 本家リリース: 0.3.2\n- 本家コミット: commit\nAPK SHA-256: digest\n<!-- jp-source: " + "a" * 64 + " -->\n\n<!-- proton:start -->\nold\n<!-- proton:end -->\nextra"
        marker = "<!-- proton-source: " + "b" * 64 + " -->"
        updated = release.proton_notes(body, marker)
        self.assertEqual([line for line in updated.splitlines() if line and not line.startswith("<!--")],
                         ["- 本家リリース: 0.3.2", "- 本家コミット: commit", "APK SHA-256: digest"])
        self.assertNotIn("old", updated)
        self.assertNotIn("extra", updated)
        self.assertIn(marker, updated)
        with self.assertRaisesRegex(RuntimeError, "three APK fields"):
            release.proton_notes("unexpected", marker)

    def test_missing_or_draft_apk_release_never_creates_release(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            for existing in (None, {"isDraft": True}, {"isDraft": False, "assets": []}):
                with patch.object(release, "current_droiddeck_release", return_value={"tag_name": "0.3.2"}), \
                        patch.object(release, "existing_release", return_value=existing), patch.object(release, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "refusing to create"):
                        release.publish("owner/repo", "e" * 40, plan, directory)
                    run.assert_not_called()

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

    def test_upload_failure_preserves_existing_release_and_skips_cleanup(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.prepare_assets(directory, plan)
            with patch.object(release, "current_droiddeck_release", return_value={"tag_name": "0.3.2"}), \
                    patch.object(release, "existing_release", return_value={"isDraft": False, "body": "- 本家リリース: 0.3.2\n- 本家コミット: commit\nAPK SHA-256: digest", "assets": [{"name": "DroidDeck-Patch.apk"}]}), \
                    patch.object(release, "run", side_effect=RuntimeError("upload failed")) as run:
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    release.publish("owner/repo", "e" * 40, plan, directory)
                self.assertEqual(run.call_count, 1)

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

