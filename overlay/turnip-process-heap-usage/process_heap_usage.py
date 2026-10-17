"""Strict Mesa semantic detection and PR Drivers#5 patch application (both targets)."""
from pathlib import Path
import re
import subprocess

DEVICE = "src/freedreno/vulkan/tu_device.cc"
PATCH = Path(__file__).resolve().with_name("process-heap-usage.patch")


def equivalent(text):
    # Follow the actual heapUsage expression, rather than the script's name. Accept renames.
    report = re.findall(r'heapUsage\[0\]\s*=\s*p_atomic_read\(&([A-Za-z_]\w*)\);', text)
    for counter in report:
        c = re.escape(counter)
        checks = [bool(re.search(r'static\s+(?:uint64_t|VkDeviceSize)\s+' + c + r'\s*(?:=\s*0)?\s*;', text)),
                  bool(re.search(r'p_atomic_add\(&' + c + r',\s*bo->size\);', text)),
                  bool(re.search(r'p_atomic_add\(&' + c + r',\s*-mem->bo->size\);', text))]
        if all(checks):
            return True
        raise RuntimeError("partial process-wide heap usage match")
    counters = re.findall(r'static\s+(?:uint64_t|VkDeviceSize)\s+([A-Za-z_]\w*)\s*(?:=\s*0)?\s*;', text)
    for counter in counters:
        if re.search(r'p_atomic_add\(&' + re.escape(counter) + r',\s*(?:-mem->bo|bo)->size\);', text):
            raise RuntimeError("partial process-wide heap usage match")
    if 'tu_process_heap_used' in text:
        raise RuntimeError("partial process-wide heap usage match")
    return False


def apply(mesa):
    if equivalent((mesa / DEVICE).read_text()):
        raise RuntimeError("equivalent upstream fix detected; overlay unnecessary")
    command = ["git", "-C", str(mesa), "apply"]
    result = subprocess.run([*command, "--check", str(PATCH)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("process-wide heap usage drift:\n" + result.stderr)
    subprocess.run([*command, str(PATCH)], check=True)
    if not equivalent((mesa / DEVICE).read_text()):
        raise RuntimeError("process-wide heap usage patch did not implement the counter")


if __name__ == '__main__':
    # Drivers executes series scripts with the Mesa checkout as cwd.
    apply(Path.cwd())
