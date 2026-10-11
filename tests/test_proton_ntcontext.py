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


class GEReleaseTest(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "build.lock.json").read_text())
        self.resolve = Mock(return_value=("a" * 40, "b" * 40, self.lock["sdk_image"]))
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
        self.assertEqual(plan["tag"], "GE-Proton11-8-ntcontext-fix")
        self.assertEqual(plan["lock"]["proton_commit"], "a" * 40)
        self.assertEqual(plan["lock"]["wine_commit"], "b" * 40)
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
                     {"tree": [{"path": "wine", "mode": "160000", "type": "commit", "sha": "b" * 40}]},
                     {"content": base64.b64encode(makefile.encode()).decode()}]
        with patch.object(release, "api", side_effect=responses) as api:
            self.assertEqual(release.resolve_source("GE-Proton11-8"),
                             ("a" * 40, "b" * 40, self.lock["sdk_image"]))
        self.assertTrue(api.call_args_list[-1].args[0].endswith("?ref=" + "a" * 40))
        with self.assertRaises(RuntimeError):
            release.arm64_sdk(makefile.replace("arm64)", "other)"))
        with self.assertRaises(RuntimeError):
            release.arm64_sdk(makefile.replace("registry.gitlab.steamos.cloud", "example.org"))

    def test_unexpected_wine_tree_is_rejected(self):
        for tree in ({"tree": []}, {"truncated": True, "tree": []}):
            with patch.object(release, "api", side_effect=[
                    {"object": {"type": "commit", "sha": "a" * 40}}, tree]):
                with self.assertRaisesRegex(RuntimeError, "Wine submodule"):
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
        self.assertIn("GE-Proton11-8-ntcontext-fix", calls[0])
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
