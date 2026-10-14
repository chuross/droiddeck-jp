import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RAM = runpy.run_path(str(ROOT / "overlay/proton-ntcontext/droiddeck_ram.py"))


class ProtonRamPolicyTest(unittest.TestCase):
    def test_off_and_session_override_preserve_the_original_environment(self):
        for env in ({}, {"DROIDDECK_SESSION_LOW_MEMORY": "0", "DROIDDECK_LOW_MEMORY": "1"},
                    {"DROIDDECK_LOW_MEMORY": "0", "FEX_DYNAMICL1CACHEINCREASECOUNTHEURISTIC": "250"}):
            original = dict(env)
            self.assertIs(RAM["configure_ram"](env), env)
            self.assertEqual(env, original)

    def test_ram_mode_controls_lookup_retention_without_memory_quotas(self):
        env = {"DROIDDECK_SESSION_LOW_MEMORY": "1", "DXVK_CONFIG": "user options", "FEX_DISKCACHE": "1"}
        RAM["configure_ram"](env)
        self.assertEqual(env["FEX_DYNAMICL1CACHEINCREASECOUNTHEURISTIC"], str((1 << 64) - 1))
        self.assertEqual(env["DXVK_CONFIG"], "user options")
        self.assertEqual(env["FEX_DISKCACHE"], "1")
        self.assertNotIn("VKD3D_VRAM_SIZE", env)


@unittest.skipUnless(os.environ.get("DROIDDECK_FEX_SOURCE") and shutil.which("c++"), "requires pinned FEX source and C++")
class FexResidentMemoryTest(unittest.TestCase):
    def test_original_growth_function_changes_resident_pages_not_only_virtual_size(self):
        source = Path(os.environ["DROIDDECK_FEX_SOURCE"])
        report = RAM["inspect_source"](source)
        self.assertEqual(report["lookup_header_sha256"], "0c5825c7153e98ae3bb7bee95dac07e7a8253720196813fa0eb68174ef973384")
        header = (source / "FEXCore/Source/Interface/Core/LookupCache.h").read_text()
        start = header.index("  void UpdateDynamicL1Stats(")
        end = header.index("\n  GuestToHostMap* Shared", start)
        actual = header[start:end]
        code = r'''
#include <cassert>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string_view>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>
namespace FEXCore {
namespace Core {
struct Frame { struct { uint64_t L1Mask; } State; };
struct InternalThreadState { Frame* CurrentFrame; };
}
namespace Allocator {
void VirtualDontNeed(void* address, size_t length, bool) {
    assert(madvise(address, length, MADV_DONTNEED) == 0);
}
}
}
struct Cache {
    struct LookupCacheEntry { uintptr_t HostCode, GuestCode; };
    static constexpr size_t MIN_L1_ENTRIES = 8 * 1024;
    static constexpr size_t MAX_L1_ENTRIES = 1 * 1024 * 1024;
    static constexpr size_t MAX_L1_SIZE = MAX_L1_ENTRIES * sizeof(LookupCacheEntry);
    static constexpr std::chrono::seconds SamplePeriod {1};
    size_t CurrentL1Entries = MIN_L1_ENTRIES;
    uintptr_t L1Pointer;
    uint64_t L1PointerMask = MIN_L1_ENTRIES - 1, L2L3CacheHits = 0;
    std::chrono::time_point<std::chrono::system_clock> LastPeriod;
    uint64_t growth;
    explicit Cache(uint64_t threshold) : growth(threshold) {
        void* allocation = mmap(nullptr, MAX_L1_SIZE, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
        assert(allocation != MAP_FAILED);
        // Match original LookupCache's explicit THP disable; virtual reserve is
        // deliberately identical in both cases, so mincore measures real pages.
        assert(madvise(allocation, MAX_L1_SIZE, MADV_NOHUGEPAGE) == 0);
        L1Pointer = reinterpret_cast<uintptr_t>(allocation);
    }
    ~Cache() { munmap(reinterpret_cast<void*>(L1Pointer), MAX_L1_SIZE); }
    uint64_t DynamicL1CacheIncreaseCountHeuristic() { return growth; }
    uint64_t DynamicL1CacheDecreaseCountHeuristic() { return 50; }
    uint64_t GetScaledL1PointerMask() { return L1PointerMask * sizeof(LookupCacheEntry); }
    void AddL1Entry(uint64_t guest, uint64_t host) {
        reinterpret_cast<LookupCacheEntry*>(L1Pointer)[guest & L1PointerMask] = {host, guest};
    }
    void populate() {
        auto* words = reinterpret_cast<volatile uint64_t*>(L1Pointer);
        for (size_t i = 0; i < CurrentL1Entries * 2; ++i) words[i] = i;
    }
    size_t resident_bytes() {
        size_t page_size = sysconf(_SC_PAGESIZE);
        std::vector<unsigned char> residency(MAX_L1_SIZE / page_size);
        assert(mincore(reinterpret_cast<void*>(L1Pointer), MAX_L1_SIZE, residency.data()) == 0);
        size_t pages = 0;
        for (auto state : residency) pages += state & 1;
        return pages * page_size;
    }
'''
        code += actual
        code += r'''
};
int main() {
    std::string_view configured = "18446744073709551615";
    uint64_t threshold = 0;
    auto parsed = std::from_chars(configured.data(), configured.data() + configured.size(), threshold);
    assert(parsed.ec == std::errc() && parsed.ptr == configured.data() + configured.size());
    assert(threshold == UINT64_MAX);
    Cache stock(250), low(threshold);
    FEXCore::Core::Frame frame {};
    FEXCore::Core::InternalThreadState thread {&frame};
    for (int round = 0; round < 8; ++round) {
        for (Cache* cache : {&stock, &low}) {
            cache->LastPeriod = std::chrono::system_clock::now() - std::chrono::seconds(2);
            cache->L2L3CacheHits = 999;
            cache->UpdateDynamicL1Stats(&thread, 1, 2);
            cache->populate();
        }
    }
    assert(stock.CurrentL1Entries == Cache::MAX_L1_ENTRIES);
    assert(low.CurrentL1Entries == Cache::MIN_L1_ENTRIES);
    assert(stock.resident_bytes() == 16 * 1024 * 1024);
    assert(low.resident_bytes() == 128 * 1024);
    std::cout << "lookup resident bytes: stock=" << stock.resident_bytes()
              << " low=" << low.resident_bytes() << std::endl;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.cpp").write_text(code)
            subprocess.run(["c++", "-std=c++20", "-O2", "-Wall", "-Werror", str(root / "test.cpp"), "-o", str(root / "test")], check=True)
            subprocess.run([str(root / "test")], check=True)
