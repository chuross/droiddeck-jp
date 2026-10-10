import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "overlay/proton-ntcontext"


def module(name):
    spec = importlib.util.spec_from_file_location("ntcontext_" + name, ROOT / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


apply = module("apply")
prepare = module("prepare_ge")
package = module("package")
ORIGINAL = '''
NTSTATUS SYSCALL_API NtGetContextThread( HANDLE handle, CONTEXT *context )
{
    ARM64_NT_CONTEXT arm_ctx = { .ContextFlags = ctx_flags_x64_to_arm( context->ContextFlags ) };
    NTSTATUS status = syscall_NtGetContextThread( handle, &arm_ctx );

    if (!status) context_arm_to_x64( (ARM64EC_NT_CONTEXT *)context, &arm_ctx );
    return status;
}
'''


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


if __name__ == '__main__':
    unittest.main()
