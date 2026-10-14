import hashlib
import os
from pathlib import Path
import runpy
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RECIPE = runpy.run_path(str(ROOT / "overlay/proton-ntcontext/stock_ntdll.py"))


class OriginalDllGuardTest(unittest.TestCase):
    def fixture(self):
        data = bytearray(2162688)
        struct.pack_into("<I", data, RECIPE["CODEMAP_LENGTH_OFFSET"], 0x6e374)
        struct.pack_into("<II", data, RECIPE["REDIRECTION_OFFSET"] - 4, 0xf42e0, RECIPE["ORIGINAL_ENTRY"])
        struct.pack_into("<Q", data, RECIPE["SYSCALL_POINTER_OFFSET"], 0x180000000 + RECIPE["ORIGINAL_ENTRY"])
        data[RECIPE["ORIGINAL_ENTRY"]:RECIPE["ORIGINAL_ENTRY"] + 4] = bytes.fromhex("f353bda9")
        return bytes(data)

    def test_only_leaf_guard_and_its_routing_change(self):
        original = self.fixture()
        with patch.dict(RECIPE["patch"].__globals__, STOCK_SHA256=hashlib.sha256(original).hexdigest()):
            candidate = RECIPE["patch"](original)
            report = RECIPE["verify"](original, candidate)
            permitted = {i for start, length in report["changed_ranges"] for i in range(start, start + length)}
            self.assertEqual(len(candidate), len(original))
            self.assertTrue(all(i in permitted for i, (a, b) in enumerate(zip(original, candidate)) if a != b))
            self.assertEqual(len(permitted), 32)
            self.assertEqual(struct.unpack_from("<Q", candidate, RECIPE["SYSCALL_POINTER_OFFSET"])[0],
                             0x180000000 + RECIPE["GUARD_RVA"])
            guard = struct.unpack_from("<4I", candidate, RECIPE["GUARD_RVA"])
            self.assertEqual(guard[0] & 0xff00001f, 0xb5000001)  # cbnz x1
            displacement = (guard[0] >> 5) & 0x7ffff
            if displacement & 0x40000:
                displacement -= 0x80000
            self.assertEqual(RECIPE["GUARD_RVA"] + displacement * 4, RECIPE["ORIGINAL_ENTRY"])
            self.assertEqual(guard[1:], (0x528001a0, 0x72b80000, 0xd65f03c0))
            changed = bytearray(candidate)
            changed[0x100] ^= 1
            with self.assertRaisesRegex(ValueError, "outside"):
                RECIPE["verify"](original, bytes(changed))

    def test_uninspected_or_already_patched_dll_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported original"):
            RECIPE["patch"](self.fixture())

    def test_occupied_padding_or_changed_routing_is_rejected(self):
        for offset in (RECIPE["GUARD_RVA"], RECIPE["CODEMAP_LENGTH_OFFSET"], RECIPE["REDIRECTION_OFFSET"]):
            original = bytearray(self.fixture())
            original[offset] ^= 1
            with patch.dict(RECIPE["patch"].__globals__, STOCK_SHA256=hashlib.sha256(original).hexdigest()):
                with self.assertRaisesRegex(ValueError, "layout"):
                    RECIPE["patch"](original)

    @unittest.skipUnless(os.environ.get("DROIDDECK_STOCK_NTDLL"), "original release DLL not supplied")
    def test_inspected_release_binary(self):
        original = Path(os.environ["DROIDDECK_STOCK_NTDLL"]).read_bytes()
        candidate = RECIPE["patch"](original)
        self.assertEqual(RECIPE["verify"](original, candidate)["patched_sha256"],
                         "12fba0d81f743b76a747b04bd3ad59005eed50ccfd997bff6482a64be370e86e")
