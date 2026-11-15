/*
MIT License

Copyright (c) 2026 xerabora contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
/* Owned direct and pointer-chain watch derivation through pinned rcheevos APIs.
 * Indexed expression compilation adapts xeRAbora v2 watchlist.c (MIT).
 * This uses rcheevos memref structures; it does not run the desktop service. */
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
#define RA_WATCH_SIZE(word) ((word) >> 28)

#define RA_NODE_MAGIC      0x324E4152u /* "RAN2" */
#define RA_NODE_MAX        128u
#define RA_NODE_PAIR_BYTES 8u

#define RA_NODE_SIZE(w) (((w) >> 13) & 7)
#define RA_NODE_PACK(parent, from_node, size, shift, width) \
    (((uint32_t)(width) << 21) | ((uint32_t)(shift) << 16) | ((uint32_t)(size) << 13) | \
     ((uint32_t)(from_node) << 12) | ((uint32_t)(parent) & 0x0FFFu))

#define RA_PS2_RAM_MASK 0x01FFFFFFu

typedef struct {
    uint32_t w;
    uint32_t offset;
    uint32_t scale;
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

/* Indexed-node compiler adapted from hacan359/xeRAbora v2.0.0 watchlist.c.
 * Keep complete-set qualification and caller-provided console RAM limits here. */
#define TERM_CONST  0
#define TERM_DIRECT 1
#define TERM_NODE   2

struct term
{
    int kind;
    int index;      /* entry or node the bits come from */
    int stored;     /* bytes the console reads there */
    unsigned shift; /* first bit used */
    unsigned width; /* bits used, 0 for all of them */
    uint32_t scale;
    uint32_t add;
};

static int compile_node(watchlist_builder_t *ctx, const rc_memref_t *m);

/* Which bits of the stored value an operand of this size uses. */
static int term_cut(struct term *t, uint8_t size)
{
    unsigned shift = 0, width;

    switch (size) {
        case RC_MEMSIZE_8_BITS:  width = 8; break;
        case RC_MEMSIZE_16_BITS: width = 16; break;
        case RC_MEMSIZE_24_BITS: width = 24; break;
        case RC_MEMSIZE_32_BITS: width = 32; break;
        case RC_MEMSIZE_LOW:     width = 4; break;
        case RC_MEMSIZE_HIGH:    shift = 4; width = 4; break;
        case RC_MEMSIZE_BIT_0:   shift = 0; width = 1; break;
        case RC_MEMSIZE_BIT_1:   shift = 1; width = 1; break;
        case RC_MEMSIZE_BIT_2:   shift = 2; width = 1; break;
        case RC_MEMSIZE_BIT_3:   shift = 3; width = 1; break;
        case RC_MEMSIZE_BIT_4:   shift = 4; width = 1; break;
        case RC_MEMSIZE_BIT_5:   shift = 5; width = 1; break;
        case RC_MEMSIZE_BIT_6:   shift = 6; width = 1; break;
        case RC_MEMSIZE_BIT_7:   shift = 7; width = 1; break;
        default:
            return 0; /* bit counts, big-endian and float reads */
    }

    t->shift = shift;
    t->width = (shift == 0 && width >= (unsigned)t->stored * 8) ? 0 : width;
    return 1;
}

/* Describes the operand a chain step hangs off. Returns 0 for what the
   console cannot compute: a delta or prior read,
   two memory values combined, division, a mask with holes. */
static int resolve_operand(watchlist_builder_t *ctx, const rc_operand_t *op, struct term *t)
{
    const rc_memref_t *m;
    const rc_modified_memref_t *mm;
    struct term a, b;

    memset(t, 0, sizeof(*t));
    t->scale = 1;

    if (op->type == RC_OPERAND_CONST) {
        t->kind = TERM_CONST;
        t->add = op->value.num;
        return 1;
    }

    if (!rc_operand_is_memref(op) || op->type != RC_OPERAND_ADDRESS)
        return 0;

    m = op->value.memref;
    if (m == NULL)
        return 0;

    /* A plain address, or a chain off a constant that went into the
       list as an entry. */
    if (m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF || direct_index(ctx, m) >= 0) {
        t->kind = TERM_DIRECT;
        t->index = direct_index(ctx, m);
        if (t->index < 0)
            return 0;
        t->stored = (int)RA_WATCH_SIZE(ctx->watch[t->index]);
        return term_cut(t, op->size);
    }

    mm = (const rc_modified_memref_t *)m;

    if (mm->modifier_type == RC_OPERATOR_INDIRECT_READ) {
        t->kind = TERM_NODE;
        t->index = compile_node(ctx, m);
        if (t->index < 0)
            return 0;
        t->stored = (int)RA_NODE_SIZE(ctx->nodes[t->index].w);
        return term_cut(t, op->size);
    }

    /* Arithmetic on the way to the address. */
    if (mm->memref.value.size != RC_MEMSIZE_32_BITS)
        return 0;
    if (!resolve_operand(ctx, &mm->parent, &a) || !resolve_operand(ctx, &mm->modifier, &b))
        return 0;
    if (a.kind != TERM_CONST && b.kind != TERM_CONST)
        return 0;

    switch (mm->modifier_type) {
        case RC_OPERATOR_MULT:
            if (b.kind != TERM_CONST) {
                struct term c = a;

                a = b;
                b = c;
            }
            a.scale *= b.add;
            a.add *= b.add;
            break;

        case RC_OPERATOR_ADD:
            if (b.kind != TERM_CONST) {
                struct term c = a;

                a = b;
                b = c;
            }
            a.add += b.add;
            break;

        case RC_OPERATOR_SUB:
            if (b.kind == TERM_CONST) {
                a.add -= b.add;
            } else {
                b.scale = 0u - b.scale;
                b.add = a.add - b.add;
                a = b;
            }
            break;

        case RC_OPERATOR_AND: {
            uint32_t mask = b.add;
            unsigned bits = 0, have;

            if (b.kind != TERM_CONST || a.kind == TERM_CONST || a.scale != 1 || a.add != 0)
                return 0;
            if ((mask & RA_PS2_RAM_MASK) == RA_PS2_RAM_MASK)
                break;
            if (mask == 0 || (mask & (mask + 1)) != 0)
                return 0;
            while (mask != 0) {
                bits++;
                mask >>= 1;
            }
            have = a.width != 0 ? a.width : (unsigned)a.stored * 8 - a.shift;
            if (bits < have)
                a.width = bits;
            break;
        }

        default:
            return 0;
    }

    *t = a;
    return 1;
}

/* Compiles one chain step into a node, whatever it hangs off first, and
   returns the node index. -1 means the console cannot be asked to
   follow it. */
static int compile_node(watchlist_builder_t *ctx, const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;
    struct term t;
    int size, at;

    if (m == NULL || m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return -1;

    at = node_index(ctx, m);
    if (at >= 0)
        return at;

    mm = (const rc_modified_memref_t *)m;
    if (mm->modifier_type != RC_OPERATOR_INDIRECT_READ)
        return -1;
    if (mm->modifier.type != RC_OPERAND_CONST)
        return -1;

    /* A constant base is a fixed address: an entry, not a node. */
    if (!resolve_operand(ctx, &mm->parent, &t) || t.kind == TERM_CONST)
        return -1;
    if (t.index > 0x0FFF)
        return -1;

    if (ctx->node_count >= RA_NODE_MAX) {
        return -1;
    }

    size = memsize_bytes(rc_memref_shared_size(mm->memref.value.size));
    at = ctx->node_count++;
    ctx->nodes[at].w = RA_NODE_PACK((unsigned int)t.index, t.kind == TERM_NODE ? 1u : 0u,
                                 (unsigned int)size, t.shift, t.width);
    ctx->nodes[at].offset = t.add + mm->modifier.value.num;
    ctx->nodes[at].scale = t.scale;
    ctx->node_of[at] = m;
    return at;
}

static void write_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static int build_watchlist(
    const char *const *definitions,
    uint32_t num_definitions,
    uint32_t memory_low,
    uint32_t memory_high,
    uint8_t *output,
    uint32_t capacity, int legacy)
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

            if (addr < memory_low || addr >= memory_high ||
                (uint32_t)size_bytes > memory_high - addr || addr >= 0x10000000u) {
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

    /* A constant-based indirect read is a direct entry in the v2 plan. */
    for (mml = &pool->modified_memrefs; mml != NULL; mml = mml->next) {
        uint16_t k;
        for (k = 0; k < mml->count; ++k) {
            const rc_modified_memref_t *mm = &mml->items[k];
            uint32_t addr, size;
            if (mm->modifier_type != RC_OPERATOR_INDIRECT_READ ||
                mm->parent.type != RC_OPERAND_CONST || mm->modifier.type != RC_OPERAND_CONST)
                continue;
            addr = mm->parent.value.num + mm->modifier.value.num;
            size = (uint32_t)memsize_bytes(rc_memref_shared_size(mm->memref.value.size));
            if (b.count >= RA_WATCH_MAX || addr < memory_low || addr >= memory_high ||
                size > memory_high - addr || addr >= 0x10000000u) {
                free((void *)b.direct_of);
                free((void *)b.watch);
                rc_runtime_destroy(runtime);
                return -3;
            }
            b.watch[b.count] = RA_WATCH_PACK(addr, size);
            b.direct_of[b.count] = &mm->memref;
            b.direct_bytes += size;
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
            const rc_modified_memref_t *mm = &mml->items[k];
            if (mm->modifier_type == RC_OPERATOR_INDIRECT_READ &&
                direct_index(&b, &mm->memref) < 0 && compile_node(&b, &mm->memref) < 0) {
                free((void *)b.direct_of);
                free((void *)b.watch);
                rc_runtime_destroy(runtime);
                return -5;
            }
        }
    }

    if (b.direct_bytes + b.node_count * RA_NODE_PAIR_BYTES > RA_SNAP_MAX_BYTES) {
        free((void *)b.direct_of);
        free((void *)b.watch);
        rc_runtime_destroy(runtime);
        return -2;
    }

    if (legacy) {
        for (i = 0; i < b.node_count; ++i) {
            if ((b.nodes[i].w >> 16) != 0 || b.nodes[i].scale != 1) {
                free((void *)b.direct_of);
                free((void *)b.watch);
                rc_runtime_destroy(runtime);
                return -5;
            }
        }
    }

    wire_size = 16 + b.count * 4;
    if (b.node_count > 0)
        wire_size += 8 + b.node_count * (legacy ? 8 : 12);

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
        write_le32(output + off, legacy ? 0x4C4E4152u : RA_NODE_MAGIC); off += 4;
        write_le32(output + off, b.node_count); off += 4;
        for (i = 0; i < b.node_count; ++i) {
            write_le32(output + off, b.nodes[i].w); off += 4;
            write_le32(output + off, b.nodes[i].offset); off += 4;
            if (!legacy) {
                write_le32(output + off, b.nodes[i].scale); off += 4;
            }
        }
    }

    free((void *)b.direct_of);
    free((void *)b.watch);
    rc_runtime_destroy(runtime);
    return (int)wire_size;
}

/* Keep legacy callers and their original output-capacity contract. */
RC_EXPORT int RC_CCONV ps2servers_build_watchlist(
    const char *const *definitions, uint32_t num_definitions,
    uint32_t memory_low, uint32_t memory_high, uint8_t *output, uint32_t capacity)
{
    return build_watchlist(definitions, num_definitions, memory_low, memory_high, output, capacity, 1);
}

RC_EXPORT int RC_CCONV ps2servers_build_watchlist_v2(
    const char *const *definitions, uint32_t num_definitions,
    uint32_t memory_low, uint32_t memory_high, uint8_t *output, uint32_t capacity)
{
    return build_watchlist(definitions, num_definitions, memory_low, memory_high, output, capacity, 0);
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
