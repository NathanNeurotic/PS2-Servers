#include "watchlist.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "log.h"
#include "protocol.h"

/* Internal rcheevos headers: the memref pool of the loaded game is the
   only place that knows which addresses the achievement set reads, and
   rcheevos has no public accessor for it. */
#include "rc_client_internal.h"
#include "rc_internal.h"
#include "rc_runtime_types.h"

static unsigned int g_watch[RA_WATCH_MAX];
static unsigned int g_offset[RA_WATCH_MAX]; /* byte offset of each value in a snapshot */
static int g_count = 0;
static int g_bytes = 0;

static unsigned char g_values[RA_SNAP_MAX_BYTES];
static int g_have_values = 0;
static int g_have_nodes = 0;

/* The memref behind each entry, so a pointer chain can name its parent
   by index instead of matching addresses a second time. */
static const rc_memref_t *g_direct_of[RA_WATCH_MAX];

/* Pointer chains for the console, parents always before their children.
   g_node_of[i] is the memref node i serves. */
static struct ra_node g_nodes[RA_NODE_MAX];
static const rc_memref_t *g_node_of[RA_NODE_MAX];
static int g_node_count = 0;
static int g_node_full = 0;

/* Snapshot values are little-endian on the wire, whatever this PC is. */
static unsigned int load_le32(const unsigned char *p)
{
    return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
           ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}

/* Bytes the console must read for a memref of this size. Matches what
   the console derives from the packed list entry. */
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

/* Chains the console resolves are not counted here. What is left are
   the ones that cannot be compiled into a node: a chain whose offset is
   not a constant, one that hangs off a delta or prior value the console
   keeps no history for, or one over the node or snapshot ceiling. Such
   achievements are NOT disabled. The rcheevos maintainers' guidance (RA
   forum, 28.08.2026): the runtime follows pointers whatever they hold
   and expects a failed read to return 0; since nearly all logic watches
   values change, a permanent 0 does not trigger anything, it just never
   fires. Disabling them would only hide that from the user. So they
   stay active, and their number is reported as "unsupported". */
static int g_indirect = 0;

/* Not every modified memref is a pointer chain. rcheevos also builds
   them for arithmetic between direct reads -- AddSource/SubSource
   chains, combining conditions, prev(x)+prev(y) -- and those the
   console serves fine, since every underlying read is a plain address
   already on the watch list. Only RC_OPERATOR_INDIRECT_READ, anywhere
   up the chain, needs a dereference the console cannot do.

   Transformers: The Game showed the difference: 5 of 76 achievements
   use pointers, all 75 were being disabled. */
static int node_index(const rc_memref_t *m)
{
    int i;

    for (i = 0; i < g_node_count; i++) {
        if (g_node_of[i] == m)
            return i;
    }

    return -1;
}

static int direct_index(const rc_memref_t *m)
{
    int i;

    for (i = 0; i < g_count; i++) {
        if (g_direct_of[i] == m)
            return i;
    }

    return -1;
}

/* PS2 sets often write a pointer step as `I:0xX... & 0x01FFFFFF`,
   cutting the kseg bits off the pointer, and rcheevos keeps that as an
   AND memref over the read. The console masks every chain address with
   0x1FFFFFFF and never reads past 32 MB, so for any address the game can
   hold both sides land on the same place: the mask can be stepped over,
   as long as it keeps every bit of the 32 MB range. A shorter mask
   would change the address and is left alone. */
#define RA_PS2_RAM_MASK 0x01FFFFFFu

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

/* Compiles one pointer chain into a node, its parent first, and returns
   the node index. -1 means the console cannot be asked to follow it.

   Recursion ends because a chain is built from the outside in: a
   parent is always an older memref than the child, so no cycle exists.

   What is turned down, and why: a non-constant offset (the console
   computes address = parent + offset and nothing else); a parent read
   as delta or prior (the console keeps no history of past frames); a
   parent that is arithmetic rather than a pointer read. */
static int compile_node(const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;
    const rc_memref_t *pm;
    int parent, from_node, size, at;

    if (m == NULL || m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return -1;

    at = node_index(m);
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
        parent = compile_node(pm);
        from_node = 1;
    } else {
        parent = direct_index(pm);
        from_node = 0;
    }

    if (parent < 0 || parent > 0x0FFF)
        return -1;

    if (g_node_count >= RA_NODE_MAX) {
        /* Once, not once per chain: a big set has hundreds. */
        if (!g_node_full) {
            g_node_full = 1;
            log_warn("more than %d pointer chains in this set; the rest stay unsupported", RA_NODE_MAX);
        }
        return -1;
    }

    size = memsize_bytes(rc_memref_shared_size(mm->memref.value.size));
    at = g_node_count++;
    g_nodes[at].w = RA_NODE_PACK((unsigned int)parent, (unsigned int)from_node, (unsigned int)size);
    g_nodes[at].offset = mm->modifier.value.num;
    g_node_of[at] = m;
    return at;
}

/* Every pointer chain in the set, in dependency order. */
static void build_nodes(rc_client_t *client)
{
    rc_memrefs_t *pool = client->game->runtime.memrefs;
    rc_modified_memref_list_t *ml;

    g_node_count = 0;
    g_node_full = 0;

    for (ml = &pool->modified_memrefs; ml != NULL; ml = ml->next) {
        uint16_t k;

        for (k = 0; k < ml->count; k++)
            compile_node(&ml->items[k].memref);
    }

    /* Each node costs eight bytes in every snapshot. Over the ceiling
       the whole set would be refused, which would cost the game its
       telemetry as well, so the chains go and the direct reads stay. */
    if (g_bytes + g_node_count * RA_NODE_PAIR_BYTES > RA_SNAP_MAX_BYTES) {
        log_warn("%d pointer chains do not fit in a snapshot with %d bytes of direct reads; dropping them",
                 g_node_count, g_bytes);
        g_node_count = 0;
    }

    if (g_node_count > 0)
        log_info("%d pointer chain%s the console resolves every frame",
                 g_node_count, g_node_count == 1 ? "" : "s");
}

/* An achievement is unsupported when it reads through a chain that did
   not compile. A chain that did is served like any other address. */
static int memref_unsupported(const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;

    if (m == NULL || m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return 0;

    mm = (const rc_modified_memref_t *)m;
    if (mm->modifier_type == RC_OPERATOR_INDIRECT_READ)
        return node_index(m) < 0;

    if (rc_operand_is_memref(&mm->parent) && memref_unsupported(mm->parent.value.memref))
        return 1;
    if (rc_operand_is_memref(&mm->modifier) && memref_unsupported(mm->modifier.value.memref))
        return 1;

    return 0;
}

static void count_indirect(rc_client_t *client)
{
    rc_client_game_info_t *game = client->game;
    rc_client_subset_info_t *subset;
    int chained = 0;

    g_indirect = 0;

    for (subset = game->subsets; subset != NULL; subset = subset->next) {
        rc_client_achievement_info_t *a = subset->achievements;
        rc_client_achievement_info_t *a_end = a + subset->public_.num_achievements;

        for (; a < a_end; a++) {
            rc_modified_memref_list_t *ml;
            int bad = 0, good = 0;

            if (a->trigger == NULL)
                continue;

            for (ml = &game->runtime.memrefs->modified_memrefs; ml != NULL; ml = ml->next) {
                uint16_t k;

                for (k = 0; k < ml->count; k++) {
                    rc_memref_t *memref = &ml->items[k].memref;

                    if (!rc_trigger_contains_memref(a->trigger, memref))
                        continue;

                    if (memref_unsupported(memref))
                        bad = 1;
                    else if (node_index(memref) >= 0)
                        good = 1;
                }
            }

            if (bad)
                log_trace("achievement %u \"%s\" reads through a chain the console cannot follow; it cannot unlock here",
                          a->public_.id, a->public_.title);
            else if (good)
                log_trace("achievement %u \"%s\" reads through a pointer the console follows",
                          a->public_.id, a->public_.title);

            g_indirect += bad;
            chained += (!bad && good);
        }
    }

    if (chained > 0) {
        log_info("%d achievement%s read through pointers the console now follows",
                 chained, chained == 1 ? "" : "s");
    }

    if (g_indirect > 0) {
        log_info("%d achievement%s read through chains that do not compile; they stay active but will not unlock",
                 g_indirect, g_indirect == 1 ? "" : "s");
    }
}

/* ---- Survey: the set's needs with no ceiling ----------------------- */

/* A chain the console could follow, ceilings aside: constant offsets
   all the way up and a plain pointer read, masked or not, at every
   level. */
static int chain_compilable(const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;

    if (m == NULL || m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return 1;

    mm = (const rc_modified_memref_t *)m;
    if (mm->modifier_type != RC_OPERATOR_INDIRECT_READ)
        return 0;
    if (mm->modifier.type != RC_OPERAND_CONST)
        return 0;
    if (!rc_operand_is_memref(&mm->parent) || mm->parent.type != RC_OPERAND_ADDRESS)
        return 0;

    return chain_compilable(unwrap_mask(mm->parent.value.memref));
}

/* Memrefs that locked achievements and leaderboards read, as a flat
   set. A chain's link appears in the conditions itself, but marking
   the parents too keeps the count honest if rcheevos ever changes
   how it builds them. */
struct needed
{
    const rc_memref_t **items;
    int count, cap;
};

static int needed_has(const struct needed *n, const rc_memref_t *m)
{
    int i;

    for (i = 0; i < n->count; i++)
        if (n->items[i] == m)
            return 1;
    return 0;
}

static void needed_add(struct needed *n, const rc_memref_t *m)
{
    const rc_modified_memref_t *mm;

    if (m == NULL || needed_has(n, m) || n->count >= n->cap)
        return;
    n->items[n->count++] = m;

    if (m->value.memref_type != RC_MEMREF_TYPE_MODIFIED_MEMREF)
        return;
    mm = (const rc_modified_memref_t *)m;
    if (rc_operand_is_memref(&mm->parent))
        needed_add(n, mm->parent.value.memref);
    if (rc_operand_is_memref(&mm->modifier))
        needed_add(n, mm->modifier.value.memref);
}

static int memref_needed(rc_client_t *client, const rc_memref_t *m)
{
    rc_client_subset_info_t *subset;

    for (subset = client->game->subsets; subset != NULL; subset = subset->next) {
        rc_client_achievement_info_t *a = subset->achievements;
        rc_client_achievement_info_t *a_end = a + subset->public_.num_achievements;
        rc_client_leaderboard_info_t *l = subset->leaderboards;
        rc_client_leaderboard_info_t *l_end = l + subset->public_.num_leaderboards;

        for (; a < a_end; a++) {
            if (a->trigger == NULL || a->public_.state != RC_CLIENT_ACHIEVEMENT_STATE_ACTIVE)
                continue;
            if (rc_trigger_contains_memref(a->trigger, m))
                return 1;
        }
        for (; l < l_end; l++) {
            if (l->lboard == NULL)
                continue;
            if (rc_trigger_contains_memref(&l->lboard->start, m) ||
                rc_trigger_contains_memref(&l->lboard->cancel, m) ||
                rc_trigger_contains_memref(&l->lboard->submit, m) ||
                rc_value_contains_memref(&l->lboard->value, m))
                return 1;
        }
    }

    return 0;
}

void watchlist_survey(rc_client_t *client, struct watch_survey *out)
{
    rc_memrefs_t *pool;
    rc_memref_list_t *ml;
    rc_modified_memref_list_t *mml;
    rc_client_subset_info_t *subset;
    struct needed need = {NULL, 0, 0};
    int total = 0;

    memset(out, 0, sizeof(*out));
    if (client == NULL || client->game == NULL || client->game->runtime.memrefs == NULL)
        return;
    pool = client->game->runtime.memrefs;

    for (subset = client->game->subsets; subset != NULL; subset = subset->next) {
        rc_client_achievement_info_t *a = subset->achievements;
        rc_client_achievement_info_t *a_end = a + subset->public_.num_achievements;
        rc_client_leaderboard_info_t *l = subset->leaderboards;
        rc_client_leaderboard_info_t *l_end = l + subset->public_.num_leaderboards;

        for (; a < a_end; a++) {
            if (a->trigger == NULL)
                continue;
            out->achievements++;
            if (a->public_.state == RC_CLIENT_ACHIEVEMENT_STATE_ACTIVE)
                out->locked++;
        }
        for (; l < l_end; l++)
            if (l->lboard != NULL)
                out->leaderboards++;
    }

    for (ml = &pool->memrefs; ml != NULL; ml = ml->next)
        total += ml->count;
    for (mml = &pool->modified_memrefs; mml != NULL; mml = mml->next)
        total += mml->count;

    need.cap = total;
    need.items = calloc((size_t)(total > 0 ? total : 1), sizeof(*need.items));
    if (need.items == NULL)
        return;

    /* Pass one: everything a locked achievement or a leaderboard names
       in a condition, then the parents of every chain among them. */
    for (ml = &pool->memrefs; ml != NULL; ml = ml->next) {
        uint16_t k;

        for (k = 0; k < ml->count; k++)
            if (memref_needed(client, &ml->items[k]))
                needed_add(&need, &ml->items[k]);
    }
    for (mml = &pool->modified_memrefs; mml != NULL; mml = mml->next) {
        uint16_t k;

        for (k = 0; k < mml->count; k++)
            if (memref_needed(client, &mml->items[k].memref))
                needed_add(&need, &mml->items[k].memref);
    }

    /* Pass two: the totals, with and without the filter. */
    for (ml = &pool->memrefs; ml != NULL; ml = ml->next) {
        uint16_t k;

        for (k = 0; k < ml->count; k++) {
            const rc_memref_t *m = &ml->items[k];
            int b = memsize_bytes(rc_memref_shared_size(m->value.size));

            out->entries++;
            out->bytes += b;
            if (needed_has(&need, m)) {
                out->need_entries++;
                out->need_bytes += b;
            }
        }
    }
    for (mml = &pool->modified_memrefs; mml != NULL; mml = mml->next) {
        uint16_t k;

        for (k = 0; k < mml->count; k++) {
            const rc_modified_memref_t *mm = &mml->items[k];
            int ok;

            if (mm->modifier_type != RC_OPERATOR_INDIRECT_READ)
                continue;
            ok = chain_compilable(&mm->memref);
            out->chains++;
            out->chains_ok += ok;
            if (needed_has(&need, &mm->memref)) {
                out->need_chains++;
                out->need_chains_ok += ok;
            }
        }
    }

    out->snapshot = out->bytes + out->chains_ok * RA_NODE_PAIR_BYTES;
    out->need_snapshot = out->need_bytes + out->need_chains_ok * RA_NODE_PAIR_BYTES;
    free(need.items);
}

static int parts_for(int bytes)
{
    return (bytes + RA_SNAP_CHUNK_BYTES - 1) / RA_SNAP_CHUNK_BYTES;
}

/* One line per view, the ceilings named, so a log from the field can
   be read without the source. */
void watchlist_log_survey(rc_client_t *client)
{
    struct watch_survey s;
    const rc_client_game_t *game = rc_client_get_game_info(client);

    watchlist_survey(client, &s);
    log_info("survey: %s (id %u): %d achievements, %d locked, %d leaderboards",
             game != NULL && game->title != NULL ? game->title : "?", game != NULL ? game->id : 0,
             s.achievements, s.locked, s.leaderboards);
    log_info("survey: whole set: %d addresses (ceiling %d), %d bytes direct, %d chains (%d compile, ceiling %d), snapshot %d bytes (ceiling %d), %d parts of %d",
             s.entries, RA_WATCH_MAX, s.bytes, s.chains, s.chains_ok, RA_NODE_MAX,
             s.snapshot, RA_SNAP_MAX_BYTES, parts_for(s.snapshot), RA_SNAP_CHUNK_BYTES);
    log_info("survey: locked + leaderboards: %d addresses, %d bytes direct, %d chains (%d compile), snapshot %d bytes, %d parts",
             s.need_entries, s.need_bytes, s.need_chains, s.need_chains_ok,
             s.need_snapshot, parts_for(s.need_snapshot));
}

int watchlist_indirect_count(void)
{
    return g_indirect;
}

/* The order of entries here is the order of values in every snapshot;
   the console reads addresses in the order it received them. Pointer
   chains follow as nodes, and their values as (address, value) pairs
   after the direct ones. */
static char g_build_error[96] = "";

const char *watchlist_last_error(void)
{
    return g_build_error;
}

int watchlist_build(rc_client_t *client)
{
    rc_memrefs_t *pool;
    rc_memref_list_t *ml;
    int off = 0, n = 0;

    g_count = 0;
    g_bytes = 0;
    g_have_values = 0;
    g_have_nodes = 0;
    g_node_count = 0;
    g_build_error[0] = '\0';

    if (client == NULL || client->game == NULL) {
        snprintf(g_build_error, sizeof(g_build_error), "No achievement set is loaded");
        return 0;
    }

    pool = client->game->runtime.memrefs;
    if (pool == NULL) {
        log_warn("the achievement set has no memory references");
        snprintf(g_build_error, sizeof(g_build_error), "The set reads no memory");
        return 0;
    }

    for (ml = &pool->memrefs; ml != NULL; ml = ml->next) {
        uint16_t k;

        for (k = 0; k < ml->count; k++) {
            rc_memref_t *m = &ml->items[k];
            int b = memsize_bytes(rc_memref_shared_size(m->value.size));

            if (n >= RA_WATCH_MAX) {
                struct watch_survey sv;

                log_warn("more than %d addresses, the watch list does not fit", RA_WATCH_MAX);
                watchlist_log_survey(client);
                watchlist_survey(client, &sv);
                snprintf(g_build_error, sizeof(g_build_error),
                         "Set too big: %d addresses, the ceiling is %d", sv.entries, RA_WATCH_MAX);
                return 0;
            }
            if (off + b > RA_SNAP_MAX_BYTES) {
                struct watch_survey sv;

                log_warn("snapshot exceeds %d bytes, it does not fit in a packet", RA_SNAP_MAX_BYTES);
                watchlist_log_survey(client);
                watchlist_survey(client, &sv);
                snprintf(g_build_error, sizeof(g_build_error),
                         "Set too big: %d bytes a snapshot, the ceiling is %d", sv.snapshot, RA_SNAP_MAX_BYTES);
                return 0;
            }

            g_watch[n] = RA_WATCH_PACK(m->address, (unsigned int)b);
            g_offset[n] = (unsigned int)off;
            g_direct_of[n] = m;
            off += b;
            n++;
        }
    }

    if (n == 0) {
        log_warn("the achievement set has no direct memory reads");
        snprintf(g_build_error, sizeof(g_build_error), "The set has no direct memory reads");
        return 0;
    }

    g_count = n;
    g_bytes = off;
    log_info("watch list: %d addresses, %d bytes per snapshot", n, off);

    build_nodes(client);
    count_indirect(client);
    return 1;
}

int watchlist_count(void)
{
    return g_count;
}

int watchlist_bytes(void)
{
    return g_bytes;
}

int watchlist_node_count(void)
{
    return g_node_count;
}

int watchlist_snapshot_bytes(void)
{
    return g_bytes + g_node_count * RA_NODE_PAIR_BYTES;
}

int watchlist_serialize(unsigned char *out, size_t cap)
{
    struct ra_watch_file hdr;
    struct ra_node_file nhdr;
    size_t need = sizeof(hdr) + (size_t)g_count * sizeof(unsigned int);
    size_t at;
    int i;

    if (g_node_count > 0)
        need += sizeof(nhdr) + (size_t)g_node_count * sizeof(struct ra_node);

    if (g_count == 0 || need > cap)
        return 0;

    hdr.magic = RA_WATCH_MAGIC;
    hdr.version = RA_WATCH_VERSION;
    hdr.count = (unsigned int)g_count;
    /* Direct values only: this is what every console build ever shipped
       reads it as, and one that follows pointers adds the pairs itself. */
    hdr.bytes = (unsigned int)g_bytes;
    memcpy(out, &hdr, sizeof(hdr));
    at = sizeof(hdr);

    for (i = 0; i < g_count; i++, at += sizeof(unsigned int))
        memcpy(out + at, &g_watch[i], sizeof(unsigned int));

    if (g_node_count > 0) {
        nhdr.magic = RA_NODE_MAGIC;
        nhdr.count = (unsigned int)g_node_count;
        memcpy(out + at, &nhdr, sizeof(nhdr));
        at += sizeof(nhdr);

        for (i = 0; i < g_node_count; i++, at += sizeof(struct ra_node))
            memcpy(out + at, &g_nodes[i], sizeof(struct ra_node));
    }

    return (int)need;
}

unsigned char *watchlist_values(void)
{
    return g_values;
}

void watchlist_set_have_values(int have)
{
    g_have_values = have;
}

void watchlist_set_have_nodes(int have)
{
    g_have_nodes = have;
}

/* Must return exactly num_bytes or nothing. A short read of a direct
   memref makes rcheevos disable every achievement using it for the rest
   of the session.

   Before the first snapshot rcheevos probes each memref once to check
   that the address is readable. Answering "unreadable" would disable
   the achievements, so zeros are returned with a full count. */
uint32_t watchlist_read_memory(uint32_t address, uint8_t *buffer, uint32_t num_bytes,
                               rc_client_t *client)
{
    int i;

    (void)client;

    if (!g_have_values) {
        memset(buffer, 0, num_bytes);
        return num_bytes;
    }

    /* Linear search over a few hundred entries is cheap on a PC, and a
       sorted index would complicate the order mapping to the snapshot. */
    for (i = 0; i < g_count; i++) {
        unsigned int a = RA_WATCH_ADDR(g_watch[i]);
        unsigned int n = RA_WATCH_SIZE(g_watch[i]);

        if (address >= a && address + num_bytes <= a + n) {
            unsigned int off = g_offset[i] + (address - a);

            if (off + num_bytes > (unsigned int)g_bytes)
                return 0;

            memcpy(buffer, &g_values[off], num_bytes);
            return num_bytes;
        }
    }

    /* Not a fixed address: rcheevos has walked a pointer chain and is
       asking for what it landed on. The console walked the same chain in
       the same frame and says where it ended up, so the answer is a
       lookup by that address rather than a second walk here that could
       disagree with it. */
    if (g_have_nodes) {
        const unsigned char *pair = &g_values[g_bytes];

        for (i = 0; i < g_node_count; i++, pair += RA_NODE_PAIR_BYTES) {
            unsigned int a = load_le32(pair);
            unsigned int n = RA_NODE_SIZE(g_nodes[i].w);

            /* Address 0: the chain led nowhere this frame. */
            if (a == 0)
                continue;

            if (address >= a && address + num_bytes <= a + n) {
                unsigned int v = load_le32(pair + 4);
                unsigned char b[4];

                b[0] = (unsigned char)v;
                b[1] = (unsigned char)(v >> 8);
                b[2] = (unsigned char)(v >> 16);
                b[3] = (unsigned char)(v >> 24);
                memcpy(buffer, b + (address - a), num_bytes);
                return num_bytes;
            }
        }
    }

    return 0;
}
