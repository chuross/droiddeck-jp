"""Japanese replacement fonts for Proton."""
import hashlib
import io
import re
import tarfile
import urllib.request
import urllib.error
from pathlib import Path

IPAMONA_PACKAGE = "opfc-ModuleHP-1.1.1_withIPAMonaFonts-1.0.8"
IPAMONA_URLS = (
    "https://distcache.FreeBSD.org/local-distfiles/hrs/" + IPAMONA_PACKAGE + ".tar.gz",
    "https://ftp.jaist.ac.jp/pub/Linux/Momonga/5/Everything/SOURCES/" + IPAMONA_PACKAGE + ".tar.gz",
    "https://web.archive.org/web/20190309175311id_/http://www.geocities.jp/ipa_mona/"
    + IPAMONA_PACKAGE + ".tar.gz",
)
# Pinned to the same complete upstream distribution used by Winetricks.
IPAMONA_SHA256 = "ab77beea3b051abf606cd8cd3badf6cb24141ef145c60f508fcfef1e3852bb9d"

IPAMONA_FILES = ("ipag-mona.ttf", "ipagp-mona.ttf", "ipagui-mona.ttf",
                 "ipam-mona.ttf", "ipamp-mona.ttf")



def download_ipamona():
    failures = []
    for url in IPAMONA_URLS:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                archive = response.read(64 * 1024 * 1024 + 1)
            if len(archive) > 64 * 1024 * 1024:
                raise RuntimeError("archive unexpectedly large")
            actual = hashlib.sha256(archive).hexdigest()
            if actual != IPAMONA_SHA256:
                raise RuntimeError("archive checksum mismatch: got %s (%d bytes)" % (actual, len(archive)))
            print("Verified IPAMona archive from " + url)
            return archive
        except (urllib.error.URLError, OSError, RuntimeError) as error:
            failures.append(url + ": " + str(error))
    raise RuntimeError("Could not download verified IPAMona archive:\n" + "\n".join(failures))

def bundle_fonts(source):
    archive = download_ipamona()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as packed:
        for filename in IPAMONA_FILES:
            member = packed.getmember(IPAMONA_PACKAGE + "/fonts/" + filename)
            if not member.isfile() or member.size > 32 * 1024 * 1024:
                raise RuntimeError("Invalid IPAMona font archive member: " + filename)
            files[filename] = packed.extractfile(member).read()
    target = source / "tools/linuxfs/overlay/usr/local/share/fonts/droiddeck-jp"
    target.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (target / filename).write_bytes(content)
    # Preserve the COMPLETE unmodified distribution, including printer driver,
    # source, notices and licenses required by the original IPA license.
    (target / (IPAMONA_PACKAGE + ".tar.gz")).write_bytes(archive)
    # build_local.sh only installs selected overlay directories. Add an explicit
    # copy into linuxfs assets so the fonts and their license reach the APK.
    script = source / "tools/build_local.sh"
    text = script.read_text()
    # Install after all build steps and before Gradle packages the assets.
    lines = text.splitlines(keepends=True)
    candidates = [i for i, line in enumerate(lines) if re.match(r'^\s*\./gradlew\s+"\$\{gradle_task\}"(?:\s|$)', line)]
    if len(candidates) != 1:
        raise RuntimeError("Gradle packaging changed; review bundled font integration")
    lines.insert(candidates[0], '''mkdir -p "${linuxfs_dir}/usr/local/share/fonts/droiddeck-jp"
install -m644 "${repo_root}/tools/linuxfs/overlay/usr/local/share/fonts/droiddeck-jp/"* \\
    "${linuxfs_dir}/usr/local/share/fonts/droiddeck-jp/"
''')
    script.write_text("".join(lines))

def patch_game_fonts(source):
    target = source / "tools/linuxfs/overlay/usr/local/bin"
    # Both Valve and adopted third-party Protons use this boundary.
    script = target / "droiddeck-game-env"
    text = script.read_text()
    hook = "    if launch[0] != command[0]:"
    if text.count(hook) != 1:
        raise RuntimeError("droiddeck-game-env changed; review the integration")
    text = text.replace(hook, """    def jp_fonts(command, environment):
        try:
            fonts = runpy.run_path(str(Path(__file__).resolve().parent / "droiddeck-jp-fonts"))
            fonts["prepare"](command, environment)
        except Exception as error:
            print("droiddeck-game-env: Japanese fonts: %s" % error, file=sys.stderr)
    jp_fonts(launch, launch_env)
""" + hook)
    fallback = "            record_stock(sync, launch, launch_env)"
    if text.count(fallback) != 1:
        raise RuntimeError("sync fallback changed; review the integration")
    text = text.replace(fallback, fallback + "\n            jp_fonts(launch, launch_env)")
    script.write_text(text)

HELPERS = ("droiddeck-jp-fonts",)
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS) + tuple(
    "usr/local/share/fonts/droiddeck-jp/" + name
    for name in (*IPAMONA_FILES, IPAMONA_PACKAGE + ".tar.gz")
)

def apply(source):
    bundle_fonts(source)
    patch_game_fonts(source)
