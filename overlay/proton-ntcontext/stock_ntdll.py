"""Add a leaf ARM64EC NULL guard to the verified original release DLLs."""
import hashlib
import struct

# Inspected against the released ARM64X image, not a rebuilt DLL. The native
# export and both architectures' original code, relocations and unwind records
# remain intact. Fail closed for every other upstream binary.
STOCK_SHA256 = "6a185abe76c901c9ae64f9500a952a93665efd1ad19ac0eea3e78790541f21b9"
# 11-9.1 differs from 11-7.5 only in the COFF/debug timestamps at
# 0x80 and 0x13ac54. Code, entry thunk, padding and routing are identical.
ADDITIONAL_STOCK_SHA256 = {"4df3958ca291b682ab6e9f6463ee11465339bcca0eac4917200f5f33a9beb954"}
GUARD_PREFIX_RVA = 0xf2374
GUARD_RVA = GUARD_PREFIX_RVA + 4
ORIGINAL_ENTRY = 0xcdb00
CODEMAP_LENGTH_OFFSET = 0x13ac91
REDIRECTION_OFFSET = 0x1804bc
SYSCALL_POINTER_OFFSET = 0x130460


def patch(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest != STOCK_SHA256 and digest not in ADDITIONAL_STOCK_SHA256:
        raise ValueError("unsupported original ntdll; inspect upstream DLL before updating the recipe")
    # The existing ARM64EC code range ends here. Zero-filled executable padding
    # follows it, before the next x64 code range at 0xf3000. Extend only this
    # range by the 4-byte entry descriptor and 16-byte guard. No existing
    # function is moved or overwritten.
    if (data[GUARD_PREFIX_RVA:GUARD_PREFIX_RVA + 20] != bytes(20)
            or struct.unpack_from("<I", data, CODEMAP_LENGTH_OFFSET)[0] != 0x6e374
            or struct.unpack_from("<II", data, REDIRECTION_OFFSET - 4) != (0xf42e0, ORIGINAL_ENTRY)
            or struct.unpack_from("<Q", data, SYSCALL_POINTER_OFFSET)[0] != 0x180000000 + ORIGINAL_ENTRY
            or data[ORIGINAL_ENTRY:ORIGINAL_ENTRY + 4] != bytes.fromhex("f353bda9")):
        raise ValueError("original ARM64EC layout differs from inspected DLL")
    entry_prefix = struct.unpack_from("<i", data, ORIGINAL_ENTRY - 4)[0]
    if entry_prefix != 0x1db01:
        raise ValueError("original ARM64EC entry thunk changed")
    delta = (ORIGINAL_ENTRY - GUARD_RVA) // 4
    if (ORIGINAL_ENTRY - GUARD_RVA) % 4 or not -(1 << 18) <= delta < (1 << 18):
        raise ValueError("ARM64EC guard branch is out of range")
    # cbnz x1, original entry; mov w0, 13; movk w0, 0xc000, lsl 16; ret.
    # This is a leaf with no stack adjustment, so the original unwind table is
    # valid without adding a frame. Non-NULL calls execute the original prologue.
    guard = struct.pack("<4I", 0xb5000001 | ((delta & 0x7ffff) << 5),
                        0x528001a0, 0x72b80000, 0xd65f03c0)
    result = bytearray(data)
    # ARM64EC entry points carry a signed relative entry-thunk reference in
    # their preceding word. FEX uses it to marshal x64 arguments. Reuse the
    # original NtGetContextThread thunk at 0xeb600, with the corrected offset.
    entry_thunk = ORIGINAL_ENTRY + (entry_prefix & ~1)
    struct.pack_into("<i", result, GUARD_PREFIX_RVA, (entry_thunk - GUARD_RVA) | 1)
    result[GUARD_RVA:GUARD_RVA + 16] = guard
    struct.pack_into("<I", result, CODEMAP_LENGTH_OFFSET, 0x6e388)
    struct.pack_into("<I", result, REDIRECTION_OFFSET, GUARD_RVA)
    # Wine recognizes x64 syscall stubs through arm64ec_syscalls as well as
    # CHPE redirections. Route both to the guard, retaining the existing DIR64
    # relocation for this absolute pointer (including rebased DLL loads).
    struct.pack_into("<Q", result, SYSCALL_POINTER_OFFSET, 0x180000000 + GUARD_RVA)
    return bytes(result)


def verify(original, candidate):
    if patch(original) != candidate:
        raise ValueError("ntdll contains changes outside the original DLL NULL guard")
    return {"method": "original-dll-arm64ec-leaf-guard", "original_sha256": hashlib.sha256(original).hexdigest(),
            "patched_sha256": hashlib.sha256(candidate).hexdigest(),
            "guard_rva": GUARD_RVA, "original_entry_rva": ORIGINAL_ENTRY,
            "changed_ranges": [[GUARD_PREFIX_RVA, 20], [CODEMAP_LENGTH_OFFSET, 4], [REDIRECTION_OFFSET, 4],
                               [SYSCALL_POINTER_OFFSET, 8]],
            "maximum_modified_bytes": 36}
