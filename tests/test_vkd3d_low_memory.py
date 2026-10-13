import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "overlay/vkd3d-low-memory"
spec = importlib.util.spec_from_file_location("vkd3d_memory_apply", ROOT / "apply.py")
APPLY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APPLY)


def function(text, name):
    index = text.index(name + "(")
    start = text.rfind("\n\n", 0, index) + 2
    return text[start:text.index("\n}\n", index) + 3]


@unittest.skipUnless(shutil.which("cc"), "C compiler unavailable")
class MemoryPolicyTest(unittest.TestCase):
    def compile(self, code):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test.c"
            binary = root / "test"
            source.write_text('#include <assert.h>\n#include "droiddeck_memory.h"\n' + code)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(ROOT), str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_unset_and_off_preserve_normal_limits_and_enabled_mode_bounds_only_caches(self):
        self.compile(r'''
int main(void) {
    assert(!droiddeck_memory_enabled(NULL));
    assert(!droiddeck_memory_enabled("0"));
    assert(!droiddeck_memory_enabled("true"));
    assert(!droiddeck_memory_enabled("1garbage"));
    assert(droiddeck_memory_enabled("1"));
    assert(droiddeck_scratch_limit(false, false, 32) == 32);
    assert(droiddeck_scratch_limit(false, true, 128) == 128);
    assert(droiddeck_scratch_limit(true, false, 32) == 4);
    assert(droiddeck_scratch_limit(true, true, 128) == 8);
    assert(droiddeck_command_pool_limit(false, 8) == 8);
    assert(droiddeck_command_pool_limit(true, 8) == 1);
    assert(droiddeck_command_buffer_limit(false) == 256);
    assert(droiddeck_command_buffer_limit(true) == 64);
    uint32_t resets = 0;
    for (int i = 0; i < 180; i++) assert(!droiddeck_release_pool_memory(false, &resets));
    assert(resets == 0);
    for (int i = 1; i <= 180; i++)
        assert(droiddeck_release_pool_memory(true, &resets) == (i % 60 == 0));
    assert(resets == 0);
}
''')

    def test_changed_source_fails_without_installing_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            with self.assertRaises(subprocess.CalledProcessError):
                APPLY.apply(source)
            self.assertFalse((source / APPLY.HEADER).exists())


@unittest.skipUnless(os.environ.get("DROIDDECK_VKD3D_SOURCE"), "requires patched VKD3D source")
class SourceIntegrationTest(unittest.TestCase):
    compile = MemoryPolicyTest.compile

    def test_actual_allocator_reset_never_reclaims_recording_or_pending_command_lists(self):
        text = (Path(os.environ["DROIDDECK_VKD3D_SOURCE"]) / "libs/vkd3d/command.c").read_text()
        actual = function(text, "d3d12_command_allocator_Reset")
        code = r'''
typedef int HRESULT;
typedef unsigned ULONG;
typedef int LONG;
#define STDMETHODCALLTYPE
#define S_OK 0
#define E_FAIL -1
#define FAILED(hr) ((hr) < 0)
#define WARN(...) ((void)0)
#define TRACE(...) ((void)0)
#define ERR(...) ((void)0)
#define vkd3d_memory_order_acquire 0
#define VKD3D_SCRATCH_POOL_KIND_COUNT 3
#define VKD3D_QUEUE_TIMELINE_TRACE_STATE_TYPE_COMMAND_ALLOCATOR_RESET 0
struct d3d12_command_list { bool is_recording; };
struct d3d12_device { int queue_timeline_trace; };
struct command_pool { struct { size_t command_buffer_count; } pending; };
struct scratch_pool { unsigned *scratch_buffers; size_t scratch_buffer_count; };
typedef struct d3d12_command_allocator ID3D12CommandAllocator;
struct d3d12_command_allocator {
    struct d3d12_command_list *current_command_list;
    unsigned internal_refcount;
    struct d3d12_device *device;
    struct command_pool primary_pool, fallback_pool;
    struct scratch_pool scratch_pools[3];
    unsigned *query_pools;
    size_t query_pool_count;
    unsigned active_query_pools;
};
static unsigned resets, resources_freed, scratch_returned;
static struct d3d12_command_allocator *impl_from_ID3D12CommandAllocator(ID3D12CommandAllocator *iface) { return iface; }
static unsigned vkd3d_atomic_uint32_load_explicit(unsigned *value, int order) { return *value; }
static void d3d12_command_allocator_free_resources(struct d3d12_command_allocator *a) { resources_freed++; }
static void vkd3d_queue_timeline_trace_register_instantaneous(int *trace, int state, size_t count) {}
static HRESULT d3d12_command_allocator_reset_command_pool(struct d3d12_device *d, struct command_pool *p) {
    resets++; return S_OK;
}
static void d3d12_device_return_scratch_buffer(struct d3d12_device *d, unsigned kind, unsigned *buffer) { scratch_returned++; }
static void d3d12_device_return_query_pool(struct d3d12_device *d, unsigned *pool) {}
'''
        code += actual
        code += r'''
int main(void) {
    struct d3d12_device device = {0};
    struct d3d12_command_list list = {true};
    unsigned scratch = 1;
    struct d3d12_command_allocator allocator = {0};
    allocator.device = &device;
    allocator.current_command_list = &list;
    allocator.internal_refcount = 1;
    allocator.scratch_pools[0].scratch_buffers = &scratch;
    allocator.scratch_pools[0].scratch_buffer_count = 1;
    assert(d3d12_command_allocator_Reset(&allocator) == E_FAIL);
    assert(!resets && !resources_freed && !scratch_returned);
    list.is_recording = false;
    allocator.internal_refcount = 2;
    assert(d3d12_command_allocator_Reset(&allocator) == S_OK);
    assert(!resets && !resources_freed && !scratch_returned);
    assert(allocator.scratch_pools[0].scratch_buffer_count == 1);
    allocator.internal_refcount = 1;
    assert(d3d12_command_allocator_Reset(&allocator) == S_OK);
    assert(resets == 2 && resources_freed == 1 && scratch_returned == 1);
    assert(allocator.scratch_pools[0].scratch_buffer_count == 0);
}
'''
        self.compile(code)

    def test_actual_return_path_frees_only_buffers_returned_beyond_cache_limit(self):
        text = (Path(os.environ["DROIDDECK_VKD3D_SOURCE"]) / "libs/vkd3d/device.c").read_text()
        actual = function(text, "d3d12_device_return_scratch_buffer")
        code = r'''
#include <inttypes.h>
#define WARN(...) ((void)0)
enum vkd3d_scratch_pool_kind { KIND = 0 };
struct vkd3d_scratch_buffer { struct { struct { uint64_t size; } resource; } allocation; unsigned id; };
struct d3d12_device_scratch_pool {
    struct vkd3d_scratch_buffer scratch_buffers[128];
    size_t scratch_buffer_count, scratch_buffer_size;
    uint64_t block_size;
    unsigned high_water_mark;
};
struct d3d12_device { struct d3d12_device_scratch_pool scratch_pools[1]; int mutex; bool droiddeck_low_memory; };
static unsigned destroyed;
static int pthread_mutex_lock(int *mutex) { assert(!*mutex); *mutex = 1; return 0; }
static int pthread_mutex_unlock(int *mutex) { assert(*mutex); *mutex = 0; return 0; }
static void d3d12_device_destroy_scratch_buffer(struct d3d12_device *device, const struct vkd3d_scratch_buffer *scratch) {
    assert(!device->mutex && scratch->id >= 4); destroyed++;
}
'''
        code += actual
        code += r'''
int main(void) {
    for (int enabled = 0; enabled <= 1; enabled++) {
        struct d3d12_device device = {0};
        device.droiddeck_low_memory = enabled;
        device.scratch_pools[0].block_size = 1ull << 20;
        device.scratch_pools[0].scratch_buffer_size = droiddeck_scratch_limit(enabled, false, 32);
        destroyed = 0;
        for (unsigned i = 0; i < 40; i++) {
            struct vkd3d_scratch_buffer buffer = {{{1ull << 20}}, i};
            d3d12_device_return_scratch_buffer(&device, KIND, &buffer);
        }
        unsigned retained = enabled ? 4 : 32;
        assert(device.scratch_pools[0].scratch_buffer_count == retained);
        assert(destroyed == 40 - retained && !device.mutex);
        for (unsigned i = 0; i < retained; i++) assert(device.scratch_pools[0].scratch_buffers[i].id == i);
    }
}
'''
        self.compile(code)

    def test_actual_command_reset_reclaims_periodically_and_keeps_error_propagation(self):
        text = (Path(os.environ["DROIDDECK_VKD3D_SOURCE"]) / "libs/vkd3d/command.c").read_text()
        actual = function(text, "d3d12_command_allocator_reset_command_pool")
        code = r'''
#include <stdlib.h>
#include <string.h>
typedef int HRESULT;
typedef int VkResult;
typedef unsigned VkCommandPoolResetFlags;
typedef unsigned VkCommandBuffer;
#define S_OK 0
#define VK_COMMAND_POOL_RESET_RELEASE_RESOURCES_BIT 1u
#define WARN(...) ((void)0)
#define VK_CALL(call) (vk_procs->call)
struct list { VkCommandBuffer *command_buffers; size_t command_buffers_size, command_buffer_count; };
struct d3d12_command_allocator_command_pool {
    unsigned vk_command_pool;
    struct list recycled, pending;
    uint32_t droiddeck_reset_count;
};
struct vkd3d_vk_device_procs {
    void (*vkFreeCommandBuffers)(unsigned, unsigned, size_t, VkCommandBuffer *);
    VkResult (*vkResetCommandPool)(unsigned, unsigned, unsigned);
};
struct d3d12_device {
    struct vkd3d_vk_device_procs vk_procs;
    unsigned vk_device;
    bool droiddeck_low_memory;
};
static unsigned calls, freed, release_calls, reset_flags;
static VkResult reset_result;
static void free_buffers(unsigned device, unsigned pool, size_t count, VkCommandBuffer *buffers) {
    assert(device == 1 && pool == 2 && buffers); freed += count;
}
static VkResult reset_pool(unsigned device, unsigned pool, unsigned flags) {
    assert(device == 1 && pool == 2); calls++; reset_flags = flags;
    if (flags & VK_COMMAND_POOL_RESET_RELEASE_RESOURCES_BIT) release_calls++;
    return reset_result;
}
static bool vkd3d_array_reserve(void **ptr, size_t *capacity, size_t count, size_t size) {
    if (count <= *capacity) return true;
    *ptr = realloc(*ptr, count * size); assert(*ptr); *capacity = count; return true;
}
static HRESULT hresult_from_vk_result(VkResult result) { return result - 100; }
'''
        code += actual
        code += r'''
int main(void) {
    struct d3d12_device device = {{free_buffers, reset_pool}, 1, true};
    struct d3d12_command_allocator_command_pool pool = {0};
    assert(d3d12_command_allocator_reset_command_pool(&device, &pool) == S_OK);
    assert(calls == 0 && pool.droiddeck_reset_count == 0);
    pool.vk_command_pool = 2;
    pool.pending.command_buffers = calloc(300, sizeof(VkCommandBuffer));
    pool.pending.command_buffers_size = 300;
    pool.pending.command_buffer_count = 300;
    assert(d3d12_command_allocator_reset_command_pool(&device, &pool) == S_OK);
    assert(freed == 236 && pool.recycled.command_buffer_count == 64 && !pool.pending.command_buffer_count);
    for (int i = 1; i < 60; i++)
        assert(d3d12_command_allocator_reset_command_pool(&device, &pool) == S_OK);
    assert(calls == 60 && release_calls == 1 && reset_flags == 1 && pool.droiddeck_reset_count == 0);
    device.droiddeck_low_memory = false;
    pool.pending.command_buffer_count = 300;
    freed = 0;
    assert(d3d12_command_allocator_reset_command_pool(&device, &pool) == S_OK);
    assert(freed == 108 && pool.recycled.command_buffer_count == 256 && reset_flags == 0);
    reset_result = -5;
    assert(d3d12_command_allocator_reset_command_pool(&device, &pool) == -105);
    free(pool.pending.command_buffers); free(pool.recycled.command_buffers);
}
'''
        self.compile(code)

    def test_actual_scratch_pool_init_preserves_required_block_sizes_and_off_mode(self):
        text = (Path(os.environ["DROIDDECK_VKD3D_SOURCE"]) / "libs/vkd3d/device.c").read_text()
        actual = function(text, "vkd3d_scratch_pool_init")
        code = r'''
#define VKD3D_SCRATCH_POOL_KIND_COUNT 3
#define VKD3D_SCRATCH_POOL_KIND_INDIRECT_PREPROCESS 1
#define VKD3D_SCRATCH_BUFFER_SIZE_DEFAULT (1ull << 20)
#define VKD3D_SCRATCH_BUFFER_COUNT_DEFAULT 32
#define VKD3D_SCRATCH_BUFFER_COUNT_INDIRECT_PREPROCESS 128
#define VKD3D_SCRATCH_BUFFER_SIZE_DGCC_PREPROCESS_NV (32ull << 20)
#define VKD3D_SCRATCH_BUFFER_SIZE_PREPROCESS_NV (4ull << 20)
#define VK_DRIVER_ID_NVIDIA_PROPRIETARY 1
#define VKD3D_CONFIG_FLAG_IS_SET(flag) false
#define INFO(...) ((void)0)
struct pool { uint64_t block_size; size_t scratch_buffer_size; };
struct d3d12_device {
    bool droiddeck_low_memory;
    struct pool scratch_pools[3];
    struct { struct { unsigned driverID; } vulkan_1_2_properties;
             struct { bool deviceGeneratedCommands; } device_generated_commands_features; } device_info;
};
static const char *environment;
static bool vkd3d_get_env_var(const char *name, char *out, size_t size) {
    assert(!strcmp(name, "DROIDDECK_VKD3D_LOW_MEMORY"));
    if (!environment || strlen(environment) >= size) return false;
    strcpy(out, environment); return true;
}
'''
        code += actual
        code += r'''
int main(void) {
    struct d3d12_device device = {0};
    const char *values[] = {NULL, "0", "1", "true", "11111111111111"};
    for (int driver = 0; driver <= 1; driver++) {
        device.device_info.vulkan_1_2_properties.driverID = driver;
        for (int value = 0; value < 5; value++) {
            environment = values[value]; vkd3d_scratch_pool_init(&device);
            bool enabled = value == 2;
            assert(device.droiddeck_low_memory == enabled);
            for (int kind = 0; kind < 3; kind++) {
                bool indirect = kind == 1;
                uint64_t block = driver && indirect ? 4ull << 20 : 1ull << 20;
                assert(device.scratch_pools[kind].block_size == block);
                assert(device.scratch_pools[kind].scratch_buffer_size ==
                       (enabled ? (indirect ? 8 : 4) : (indirect ? 128 : 32)));
            }
        }
    }
}
'''
        self.compile(code)


if __name__ == "__main__":
    unittest.main()
