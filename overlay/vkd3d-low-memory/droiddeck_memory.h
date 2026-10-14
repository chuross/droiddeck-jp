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
    size_t limit = indirect ? 8u : 4u;
    return enabled && normal > limit ? limit : normal;
}

#endif
