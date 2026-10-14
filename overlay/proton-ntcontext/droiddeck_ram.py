"""Proton RAM policy for FEX lookup tables; preserve translated code and live heaps."""
import hashlib
import json
from pathlib import Path
import re

FEX_COMMIT = "14c92681f4d62cf84d901460e0358de09c8847a7"
NO_GROWTH_THRESHOLD = str((1 << 64) - 1)


def configure_ram(env):
    mode = env.get("DROIDDECK_SESSION_LOW_MEMORY", env.get("DROIDDECK_LOW_MEMORY", "0"))
    if mode != "1":
        return env
    # L1 misses still use FEX's shared translated-code map. This prevents growth
    # of the per-thread lookup table, not generation/reuse of translated code.
    # The upstream uint64 option is parsed with from_chars; use its full range
    # rather than an arbitrary workload-dependent hit-rate threshold.
    env["FEX_DYNAMICL1CACHE"] = "1"
    env["FEX_DISABLEL2CACHE"] = "1"
    env["FEX_DYNAMICL1CACHEINCREASECOUNTHEURISTIC"] = NO_GROWTH_THRESHOLD
    return env


def inspect_source(source):
    header = (source / "FEXCore/Source/Interface/Core/LookupCache.h").read_text()
    config = json.loads((source / "FEXCore/Source/Interface/Config/Config.json.in").read_text())
    # The JSON groups differ between versions; locate the named options without
    # assuming group names. Reject changed cache geometry or option types.
    def find(node, name):
        if isinstance(node, dict):
            if name in node:
                return node[name]
            for value in node.values():
                result = find(value, name)
                if result is not None:
                    return result
        return None
    if (not re.search(r"MIN_L1_ENTRIES\s*=\s*8\s*\*\s*1024", header)
            or not re.search(r"MAX_L1_ENTRIES\s*=\s*1\s*\*\s*1024\s*\*\s*1024", header)
            or (find(config, "DynamicL1CacheIncreaseCountHeuristic") or {}).get("Type") != "uint64"
            or (find(config, "DynamicL1Cache") or {}).get("Default") != "true"
            or (find(config, "DisableL2Cache") or {}).get("Default") != "true"):
        raise ValueError("FEX RAM policy requires review of the upstream lookup cache")
    return {"method": "minimum-per-thread-FEX-lookup-table", "fex_commit": FEX_COMMIT,
            "lookup_header_sha256": hashlib.sha256(header.encode()).hexdigest(),
            "normal_maximum_lookup_bytes_per_thread": 16 * 1024 * 1024,
            "low_memory_lookup_bytes_per_thread": 128 * 1024,
            "translated_code_cache": "preserved", "live_game_allocations": "unchanged"}


if __name__ == "__main__":
    import argparse
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "-C", str(args.source), "rev-parse", "HEAD"], text=True).strip()
    if commit != FEX_COMMIT:
        raise ValueError("FEX source changed; review the original DLL RAM policy")
    args.output.write_text(json.dumps(inspect_source(args.source), indent=2) + "\n")
