import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "overlay/proton-ntcontext"
spec = importlib.util.spec_from_file_location("proton_build_config", ROOT / "build_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

# The flag assignments from the pinned DroidDeck-Proton release.
MAKEFILE = '''i386_CFLAGS := -mstackrealign -march=nocona -mtune=core-avx2 -mfpmath=sse
x86_64_CFLAGS := -mcmodel=small -march=nocona -mtune=core-avx2 -mfpmath=sse
aarch64_CFLAGS := -march=armv8.2-a -mtune=cortex-x3
arm64ec_CFLAGS := $(aarch64_CFLAGS)
CFLAGS = -O2 -fwrapv -fno-strict-aliasing
CFLAGS += -ggdb -ffunction-sections -fdata-sections -fno-omit-frame-pointer
CFLAGS += -ffile-prefix-map=$(CCACHE_BASEDIR)=.
$(DST_DIR):
WINE_CFLAGS += -Wno-discarded-qualifiers
DXVK_arm64ec_CFLAGS = -marm64x
DXVK_arm64ec_LDFLAGS = -marm64x
VKD3D_PROTON_arm64ec_CFLAGS = -marm64x
VKD3D_PROTON_arm64ec_LDFLAGS = -marm64x
'''


class ProtonBuildConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.makefile = self.source / "Makefile.in"
        self.makefile.write_text(MAKEFILE)

    def test_pe_compilers_receive_upstream_aliasing_and_signed_wrap_policy(self):
        values = config.flags(self.source)
        env = config.wine_environment(values)
        for key in ("CFLAGS", "CROSSCFLAGS", "aarch64_CFLAGS", "arm64ec_CFLAGS", "i386_CFLAGS", "x86_64_CFLAGS"):
            args = shlex.split(env[key])
            for flag in ("-fno-strict-aliasing", "-fwrapv", "-ffunction-sections", "-fdata-sections"):
                self.assertIn(flag, args)
            self.assertNotIn("-ggdb", args)
        self.assertIn("-march=nocona", shlex.split(env["x86_64_CFLAGS"]))
        self.assertNotIn("-march=armv8.2-a", shlex.split(env["x86_64_CFLAGS"]))
        self.assertIn("-march=armv8.2-a", shlex.split(env["arm64ec_CFLAGS"]))

    def test_upstream_flag_changes_propagate_to_wine_and_graphics(self):
        self.makefile.write_text(MAKEFILE.replace("-fwrapv", "-fwrapv -fno-vectorize"))
        values = config.flags(self.source)
        self.assertIn("-fno-vectorize", config.wine_environment(values)["CROSSCFLAGS"])
        for component in ("dxvk", "vkd3d"):
            cross = config.cross_file(values, ROOT / "dxvk-arm64ec.cross", component)
            self.assertIn("'-fno-vectorize'", cross)
            self.assertIn("'-marm64x'", cross)

    def test_changed_or_missing_upstream_flag_definitions_fail(self):
        for changed in (MAKEFILE.replace("$(DST_DIR):", "other:"),
                        MAKEFILE.replace("DXVK_arm64ec_CFLAGS = -marm64x\n", ""),
                        MAKEFILE + "WINE_CFLAGS += -unexpected\n"):
            with self.subTest(changed=changed):
                self.makefile.write_text(changed)
                with self.assertRaises(ValueError):
                    config.flags(self.source)


if __name__ == "__main__":
    unittest.main()
