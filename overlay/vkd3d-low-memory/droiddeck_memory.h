#ifndef DROIDDECK_MEMORY_H
#define DROIDDECK_MEMORY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Opt in per process; never impose allocation failures or touch live resources. */
static inline bool droiddeck_memory_enabled(const char *value)
{
    return value && !strcmp(value, "1");
}

static inline size_t droiddeck_scratch_limit(bool enabled, bool indirect, size_t normal)
{
    return enabled ? (indirect ? 8u : 4u) : normal;
}

static inline size_t droiddeck_command_pool_limit(bool enabled, size_t normal)
{
    return enabled ? 1u : normal;
}

static inline size_t droiddeck_command_buffer_limit(bool enabled)
{
    return enabled ? 64u : 256u;
}

/* Release driver backing memory only at an already-authorized allocator reset.
 * Keep normal resets inexpensive; every 60th reset requests driver reclamation. */
static inline bool droiddeck_release_pool_memory(bool enabled, uint32_t *resets)
{
    if (!enabled)
        return false;
    if (++*resets < 60u)
        return false;
    *resets = 0;
    return true;
}

#endif
