/* Owned direct and pointer-chain watch derivation through pinned rcheevos APIs.
 * No desktop-client source or private rcheevos structure is vendored. */
#include "rc_runtime.h"
#include "rc_runtime_types.h"
#include "rc_internal.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define RA_WATCH_MAGIC   0x4C574152u /* "RAWL" */
#define RA_WATCH_VERSION 1u
#define RA_WATCH_MAX     4096u

#define RA_SNAP_HEAD_BYTES  191u
#define RA_SNAP_CHUNK_BYTES (1472u - RA_SNAP_HEAD_BYTES)
#define RA_SNAP_PARTS       9u
#define RA_SNAP_MAX_BYTES   (RA_SNAP_CHUNK_BYTES * RA_SNAP_PARTS) /* 11529 */

#define RA_WATCH_PACK(addr, size) (((uint32_t)(size) << 28) | ((uint32_t)(addr) & 0x0FFFFFFFu))

#define RA_NODE_MAGIC      0x4C4E4152u /* "RANL" */
#define RA_NODE_MAX        128u
#define RA_NODE_PAIR_BYTES 8u

#define RA_NODE_PACK(parent, from_node, size) \
    (((uint32_t)(size) << 13) | ((uint32_t)(from_node) << 12) | ((uint32_t)(parent) & 0x0FFFu))

#define RA_PS2_RAM_MASK 0x01FFFFFFu

typedef struct {
    uint32_t w;
    uint32_t offset;
} ra_watch_node_t;

typedef struct {
    const rc_memref_t **direct_of;
    uint32_t *watch;
    uint32_t count;
    uint32_t direct_bytes;

    ra_watch_node_t nodes[RA_NODE_MAX];
    const rc_memref_t *node_of[RA_NODE_MAX];
    uint32_t node_count;
} watchlist_builder_t;

static int memsize_bytes(uint8_t size)
{
    switch (size) {
        case RC_MEMSIZE_8_BITS:
        case RC_MEMSIZE_BIT_0:
        case RC_MEMSIZE_BIT_1:
        case RC_MEMSIZE_BIT_2:
        case RC_MEMSIZE_BIT_3:
        case RC_MEMSIZE_BIT_4:
        case RC_MEMSIZE_BIT_5:
        case RC_MEMSIZE_BIT_6:
        case RC_MEMSIZE_BIT_7:
        case RC_MEMSIZE_LOW:
        case RC_MEMSIZE_HIGH:
        case RC_MEMSIZE_BITCOUNT:
            return 1;
        case RC_MEMSIZE_16_BITS:
        case RC_MEMSIZE_16_BITS_BE:
            return 2;
        default:
            return 4;
    }
}

static int direct_index(const watchlist_builder_t *b, const rc_memref_t *m)
{
    uint32_t i;
    for (i = 0; i < b->count; ++i) {
        if (b->direct_of[i] == m)
            return (int)i;
    }
    return -1;
}

static int node_index(const watchlist_builder_t *b, const rc_memref_t *m)
{
    uint32_t i;
    for (i = 0; i < b->node_count; ++i) {
        if (b->node_of[i] == m)
            return (int)i;
    }
    return -1;
}

static const rc_memref_t *unwrap_mask(const rc_memref_t *m)
{
    while (m != NULL && m->value.memref_type == RC_MEMREF_TYPE_MODIFIED_MEMREF) {
        const rc_modified_memref_t *mm = (const rc_modified_memref_t *)m;

        if (mm->modifier_type != RC_OPERATOR_AND || mm->modifier.type != RC_OPERAND_CONST)
            break;
        if ((mm->modifier.value.num & RA_PS2_RAM_MASK) != RA_PS2_RAM_MASK)
            break;
        if (!rc_operand_is_memref(&mm->parent) || mm->parent.type != RC_OPERAND_ADDRESS)
            break;
        m = mm->parent.value.memref;
    }
    return m;
}

static int compile_node(watchlist_builder_t *b, const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;
    const rc_memref_t *pm;
    int parent, from_node, size, at;

    if (m == NULL || m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return -1;

    at = node_index(b, m);
    if (at >= 0)
        return at;

    mm = (const rc_modified_memref_t *)m;
    if (mm->modifier_type != RC_OPERATOR_INDIRECT_READ)
        return -1;
    if (mm->modifier.type != RC_OPERAND_CONST)
        return -1;
    if (!rc_operand_is_memref(&mm->parent) || mm->parent.type != RC_OPERAND_ADDRESS)
        return -1;

    pm = unwrap_mask(mm->parent.value.memref);
    if (pm != NULL && pm->value.memref_type == RC_MEMREF_TYPE_MODIFIED_MEMREF) {
        parent = compile_node(b, pm);
        from_node = 1;
    } else {
        parent = direct_index(b, pm);
        from_node = 0;
    }

    if (parent < 0 || parent > 0x0FFF)
        return -1;

    if (b->node_count >= RA_NODE_MAX)
        return -1;

    size = memsize_bytes(rc_memref_shared_size(mm->memref.value.size));
    at = (int)b->node_count++;
    b->nodes[at].w = RA_NODE_PACK((unsigned int)parent, (unsigned int)from_node, (unsigned int)size);
    b->nodes[at].offset = mm->modifier.value.num;
    b->node_of[at] = m;
    return at;
}

static void write_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

RC_EXPORT int RC_CCONV ps2servers_build_watchlist(
    const char *const *definitions,
    uint32_t num_definitions,
    uint32_t memory_low,
    uint32_t memory_high,
    uint8_t *output,
    uint32_t capacity)
{
    rc_runtime_t *runtime;
    rc_memrefs_t *pool;
    rc_memref_list_t *ml;
    rc_modified_memref_list_t *mml;
    watchlist_builder_t b;
    uint32_t i, wire_size, off;

    if (!definitions || num_definitions == 0 || !output || capacity < 20)
        return -1;

    runtime = rc_runtime_alloc();
    if (!runtime)
        return -1;

    for (i = 0; i < num_definitions; ++i) {
        if (!definitions[i]) {
            rc_runtime_destroy(runtime);
            return -1;
        }
        if (rc_runtime_activate_achievement(runtime, i + 1, definitions[i], NULL, 0) != RC_OK) {
            rc_runtime_destroy(runtime);
            return -1;
        }
    }

    pool = runtime->memrefs;
    if (!pool) {
        rc_runtime_destroy(runtime);
        return -4;
    }

    memset(&b, 0, sizeof(b));
    b.direct_of = (const rc_memref_t **)calloc(RA_WATCH_MAX, sizeof(rc_memref_t *));
    b.watch = (uint32_t *)calloc(RA_WATCH_MAX, sizeof(uint32_t));
    if (!b.direct_of || !b.watch) {
        free((void *)b.direct_of);
        free((void *)b.watch);
        rc_runtime_destroy(runtime);
        return -1;
    }

    for (ml = &pool->memrefs; ml != NULL; ml = ml->next) {
        uint16_t k;
        for (k = 0; k < ml->count; ++k) {
            rc_memref_t *m = &ml->items[k];
            int size_bytes = memsize_bytes(rc_memref_shared_size(m->value.size));
            uint32_t addr = m->address;

            if (b.count >= RA_WATCH_MAX) {
                free((void *)b.direct_of);
                free((void *)b.watch);
                rc_runtime_destroy(runtime);
                return -2;
            }

            if (addr < memory_low || addr + (uint32_t)size_bytes > memory_high) {
                free((void *)b.direct_of);
                free((void *)b.watch);
                rc_runtime_destroy(runtime);
                return -3;
            }

            b.watch[b.count] = RA_WATCH_PACK(addr, size_bytes);
            b.direct_of[b.count] = m;
            b.direct_bytes += (uint32_t)size_bytes;
            b.count++;
        }
    }

    if (b.count == 0) {
        free((void *)b.direct_of);
        free((void *)b.watch);
        rc_runtime_destroy(runtime);
        return -4;
    }

    for (mml = &pool->modified_memrefs; mml != NULL; mml = mml->next) {
        uint16_t k;
        for (k = 0; k < mml->count; ++k) {
            compile_node(&b, &mml->items[k].memref);
        }
    }

    if (b.direct_bytes + b.node_count * RA_NODE_PAIR_BYTES > RA_SNAP_MAX_BYTES) {
        b.node_count = 0;
    }

    if (b.direct_bytes > RA_SNAP_MAX_BYTES) {
        free((void *)b.direct_of);
        free((void *)b.watch);
        rc_runtime_destroy(runtime);
        return -2;
    }

    wire_size = 16 + b.count * 4;
    if (b.node_count > 0)
        wire_size += 8 + b.node_count * 8;

    if (wire_size > capacity) {
        free((void *)b.direct_of);
        free((void *)b.watch);
        rc_runtime_destroy(runtime);
        return -2;
    }

    write_le32(output + 0, RA_WATCH_MAGIC);
    write_le32(output + 4, RA_WATCH_VERSION);
    write_le32(output + 8, b.count);
    write_le32(output + 12, b.direct_bytes);
    off = 16;
    for (i = 0; i < b.count; ++i, off += 4) {
        write_le32(output + off, b.watch[i]);
    }

    if (b.node_count > 0) {
        write_le32(output + off, RA_NODE_MAGIC); off += 4;
        write_le32(output + off, b.node_count); off += 4;
        for (i = 0; i < b.node_count; ++i) {
            write_le32(output + off, b.nodes[i].w); off += 4;
            write_le32(output + off, b.nodes[i].offset); off += 4;
        }
    }

    free((void *)b.direct_of);
    free((void *)b.watch);
    rc_runtime_destroy(runtime);
    return (int)wire_size;
}

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
    rc_runtime_do_frame(runtime, ignore_event, collect_read, &probe, 0);
    result = probe.error ? probe.error : (int)probe.count;
    rc_runtime_destroy(runtime);
    return result;
}
