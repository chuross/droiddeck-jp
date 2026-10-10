import importlib.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import tarfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare", ROOT / "tools/prepare.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
fonts = builder.PATCHES["japanese-fonts"]
offline = builder.PATCHES["offline-mode"]
logs = builder.PATCHES["proton-logs"]
imports = builder.PATCHES["proton-import"]


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        builder.git(self.source, "init", "-b", "main")
        builder.git(self.source, "config", "user.name", "Test")
        builder.git(self.source, "config", "user.email", "test@example.invalid")
        (self.source / "base").write_text("base")
        self.commit("base")
        self.base = builder.git(self.source, "rev-parse", "HEAD").stdout.strip()

    def commit(self, message):
        builder.git(self.source, "add", ".")
        builder.git(self.source, "commit", "-m", message)
        return builder.git(self.source, "rev-parse", "HEAD").stdout.strip()

    def font_archive(self, missing=False):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as packed:
            files = {name: name.encode() for name in fonts.IPAMONA_FILES}
            if missing:
                del files[fonts.IPAMONA_FILES[-1]]
            files["doc/COPYING.font.ja"] = b"original license fixture"
            files["../driver-source.c"] = b"original printer driver fixture"
            for name, content in files.items():
                member = tarfile.TarInfo(fonts.IPAMONA_PACKAGE + "/fonts/" + name)
                member.size = len(content)
                packed.addfile(member, io.BytesIO(content))
        return output.getvalue()

    def bundle(self, archive, checksum, script_text=None):
        script = self.source / "tools/build_local.sh"
        script.parent.mkdir(exist_ok=True)
        script.write_text(script_text or 'linuxfs_replaced=1\n./gradlew "${gradle_task}" --console=plain\n')
        with patch.object(fonts.urllib.request, "urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(archive)), \
             patch.object(fonts, "IPAMONA_SHA256", checksum):
            fonts.bundle_fonts(self.source)
        return script.read_text()

    def test_bundle_fonts_and_complete_distribution_are_installed_before_packaging(self):
        archive = self.font_archive()
        script = self.bundle(archive, hashlib.sha256(archive).hexdigest())
        target = self.source / "tools/linuxfs/overlay/usr/local/share/fonts/droiddeck-jp"
        for name in fonts.IPAMONA_FILES:
            self.assertEqual((target / name).read_bytes(), name.encode())
        self.assertEqual((target / (fonts.IPAMONA_PACKAGE + ".tar.gz")).read_bytes(), archive)
        self.assertLess(script.index("install -m644"), script.index("./gradlew"))
        subprocess.run(["bash", "-n", str(self.source / "tools/build_local.sh")], check=True)

    def test_packaging_does_not_depend_on_unrelated_linuxfs_state_variable(self):
        archive = self.font_archive()
        text = self.bundle(archive, hashlib.sha256(archive).hexdigest(),
                           '  ./gradlew "${gradle_task}" --console=plain\n')
        self.assertIn('install -m644', text)
        subprocess.run(["bash", "-n", str(self.source / "tools/build_local.sh")], check=True)

    def test_ambiguous_packaging_hook_is_rejected(self):
        archive = self.font_archive()
        with self.assertRaisesRegex(RuntimeError, "Gradle packaging changed"):
            self.bundle(archive, hashlib.sha256(archive).hexdigest(),
                        './gradlew "${gradle_task}"\n./gradlew "${gradle_task}"\n')

    def test_bad_archive_checksum_prevents_bundle(self):
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            self.bundle(self.font_archive(), fonts.IPAMONA_SHA256)
        self.assertFalse((self.source / "tools/linuxfs/overlay").exists())

    def test_missing_font_prevents_bundle(self):
        archive = self.font_archive(missing=True)
        with self.assertRaises(KeyError):
            self.bundle(archive, hashlib.sha256(archive).hexdigest())
        self.assertFalse((self.source / "tools/linuxfs/overlay").exists())

    def test_existing_runtime_receives_helpers_fonts_and_original_distribution(self):
        file = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionFiles.kt"
        file.parent.mkdir(parents=True)
        file.write_text('        val files = arrayOf(\n        )\n')
        builder.stage_overlay_assets(self.source)
        text = file.read_text()
        self.assertIn('"usr/local/bin/droiddeck-jp-fonts" to "usr/local/bin/droiddeck-jp-fonts"', text)
        for helper in tuple(helper for module in builder.PATCHES.values() for helper in getattr(module, "HELPERS", ())):
            self.assertEqual(text.count('"usr/local/bin/' + helper + '" to'), 1)
        for name in (*fonts.IPAMONA_FILES, fonts.IPAMONA_PACKAGE + ".tar.gz"):
            path = "usr/local/share/fonts/droiddeck-jp/" + name
            self.assertIn('"%s" to "%s"' % (path, path), text)

    def test_changed_runtime_staging_fails_instead_of_shipping_unapplied_fix(self):
        file = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionFiles.kt"
        file.parent.mkdir(parents=True)
        file.write_text('changed staging implementation')
        with self.assertRaisesRegex(RuntimeError, "SessionFiles staging changed"):
            builder.stage_overlay_assets(self.source)

    def test_download_falls_back_after_error_and_invalid_content(self):
        archive = self.font_archive()
        with patch.object(fonts, "IPAMONA_SHA256", hashlib.sha256(archive).hexdigest()), \
             patch.object(fonts.urllib.request, "urlopen", side_effect=[
                 fonts.urllib.error.URLError("mirror unavailable"),
                 io.BytesIO(b"<html>not a font archive</html>"), io.BytesIO(archive)]) as download:
            self.assertEqual(fonts.download_ipamona(), archive)
            self.assertEqual(download.call_count, 3)

    def test_overlay_keeps_upstream_language_and_font_support(self):
        target = self.source / "tools/linuxfs/overlay/usr/local/bin"
        target.mkdir(parents=True)
        preserved = {
            "droiddeck-session": 'upstream app-selected Steam language\n',
            "droiddeck-steam-language": 'upstream language helper\n',
            "droiddeck-fonts": 'upstream Android font support\n',
            "steam-compatibility": 'upstream compatibility tools\n',
        }
        for name, text in preserved.items():
            (target / name).write_text(text)
        (target / "droiddeck-game-env").write_text(
            'def main():\n'
            '    launch, launch_env = select_sync(command, env, sync)\n'
            '    if launch[0] != command[0]:\n'
            '            record_stock(sync, launch, launch_env)\n'
            '            os.execvpe(launch[0], launch, launch_env)\n'
            '    os.execvpe(launch[0], launch, launch_env)\n')
        staging = self.source / "app/src/main/java/com/droiddeck/launcher/session/SessionFiles.kt"
        staging.parent.mkdir(parents=True)
        staging.write_text('        val files = arrayOf(\n        )\n')
        (self.source / "app/build.gradle").write_text('applicationId "com.droiddeck.launcher"\n')
        proot = self.source / "tools/proot/build.sh"
        proot.parent.mkdir(parents=True)
        proot.write_text(builder.PATCHES["build-cache"].BEFORE)
        with patch.object(fonts, "bundle_fonts"), patch.object(offline, "check"), patch.object(offline, "apply"), \
             patch.object(imports, "check"), patch.object(imports, "apply"):
            builder.overlay(self.source)
        for name, text in preserved.items():
            self.assertEqual((target / name).read_text(), text)
        self.assertEqual((target / "droiddeck-jp-fonts").read_bytes(),
                         (ROOT / "overlay/japanese-fonts/droiddeck-jp-fonts").read_bytes())
        self.assertTrue((target / "droiddeck-jp-fonts").stat().st_mode & 0o111)
        self.assertEqual((target / "droiddeck-game-env").read_text().count('jp_fonts(launch, launch_env)'), 2)
        self.assertIn('applicationId "com.droiddeck.launcher.japanese"',
                      (self.source / "app/build.gradle").read_text())


    def test_changed_proton_launch_hook_stops_preparation_without_partial_patch(self):
        script = self.source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-game-env"
        script.parent.mkdir(parents=True)
        text = 'def main():\n    upstream_launch_changed()\n'
        script.write_text(text)
        with self.assertRaisesRegex(RuntimeError, "session Proton log integration"):
            logs.patch_proton_logs(self.source)
        self.assertEqual(script.read_text(), text)

    def test_changed_steam_controls_stop_before_installing_assets(self):
        with patch.object(fonts, "bundle_fonts") as bundled:
            with self.assertRaisesRegex(RuntimeError, "Steam connection integration changed"):
                builder.overlay(self.source)
        bundled.assert_not_called()
        self.assertFalse((self.source / "tools/linuxfs/overlay").exists())


if __name__ == "__main__":
    unittest.main()


