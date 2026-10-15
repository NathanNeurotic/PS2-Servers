/* Owned direct-watch derivation through the pinned public rcheevos API.
 * No desktop-client source or private rcheevos structure is used. */
#include "rc_runtime.h"
#include "rc_runtime_types.h"
#include <stdint.h>

typedef struct {
    uint32_t *entries;
    uint32_t capacity, count;
    int error;
} watch_probe_t;

static uint32_t RC_CCONV collect_read(uint32_t address, uint32_t size, void *data)
{
    watch_probe_t *probe = (watch_probe_t *)data;
    uint32_t entry, i;
    if ((size != 1 && size != 2 && size != 4) || address >= 0x10000000u) {
        probe->error = -4;
        return 0;
    }
    entry = (size << 28) | address;
    for (i = 0; i < probe->count; ++i)
        if (probe->entries[i] == entry)
            return 0;
    if (probe->count == probe->capacity) {
        probe->error = -2;
        return 0;
    }
    probe->entries[probe->count++] = entry;
    return 0;
}

static int has_indirection(const rc_condset_t *set)
{
    for (; set; set = set->next) {
        const rc_condition_t *condition;
        if (set->num_indirect_conditions)
            return 1;
        for (condition = set->conditions; condition; condition = condition->next)
            if (condition->type == RC_CONDITION_ADD_ADDRESS)
                return 1;
    }
    return 0;
}

static void RC_CCONV ignore_event(const rc_runtime_event_t *event)
{
    (void)event;
}

/* Returns a complete direct-read vector, or a negative error. A caller must
 * never use partial output on error. Pointer chains require a different
 * derivation path and are explicitly rejected, not sampled at fake addresses. */
RC_EXPORT int RC_CCONV ps2servers_probe_watch(const char *definition,
                                            uint32_t *entries, uint32_t capacity)
{
    rc_runtime_t *runtime;
    rc_trigger_t *trigger;
    watch_probe_t probe;
    int result;
    if (!definition || !entries || !capacity || capacity > 4096)
        return -1;
    runtime = rc_runtime_alloc();
    if (!runtime)
        return -1;
    result = rc_runtime_activate_achievement(runtime, 1, definition, 0, 0);
    if (result != 0) {
        rc_runtime_destroy(runtime);
        return -1;
    }
    trigger = rc_runtime_get_achievement(runtime, 1);
    if (!trigger || has_indirection(trigger->requirement) || has_indirection(trigger->alternative)) {
        rc_runtime_destroy(runtime);
        return -3;
    }
    probe.entries = entries;
    probe.capacity = capacity;
    probe.count = 0;
    probe.error = 0;
    /* rcheevos updates all direct memrefs before condition evaluation, including
     * alternate and paused groups. Returned values do not select the watches. */
    rc_runtime_do_frame(runtime, ignore_event, collect_read, &probe, 0);
    result = probe.error ? probe.error : (int)probe.count;
    rc_runtime_destroy(runtime);
    return result;
}
