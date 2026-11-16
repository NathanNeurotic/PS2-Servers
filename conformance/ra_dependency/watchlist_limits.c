/* Exercise the managed compiler's public failure boundary with real memrefs. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include "rc_client_internal.h"
#include "watchlist.h"
#include "ra_watch.h"

void log_info(const char *fmt, ...) {(void)fmt;}
void log_warn(const char *fmt, ...) {(void)fmt;}
void log_detail(const char *fmt, ...) {(void)fmt;}
void log_trace(const char *fmt, ...) {(void)fmt;}
const rc_client_game_t *rc_client_get_game_info(const rc_client_t *client) { return &client->game->public_; }

static void check(unsigned nodes, unsigned direct, int expected)
{
    rc_client_t client = {0};
    rc_client_game_info_t game = {0};
    char definition[128];
    unsigned char wire[18000];
    unsigned i;
    client.game = &game;
    rc_runtime_init(&game.runtime);
    for (i = 0; i < direct; i++) {
        snprintf(definition, sizeof(definition), "0xX%06X=1", 0x80000 + i * 4);
        assert(rc_runtime_activate_achievement(&game.runtime, i + 1, definition, NULL, 0) == RC_OK);
    }
    for (i = 0; i < nodes; i++) {
        snprintf(definition, sizeof(definition), "I:0xX080000_0xH%06X=1", 0x40 + i);
        assert(rc_runtime_activate_achievement(&game.runtime, direct + i + 1, definition, NULL, 0) == RC_OK);
    }
    assert(watchlist_build(&client) == expected);
    if (expected) {
        assert(watchlist_node_count() == (int)nodes);
        assert(watchlist_snapshot_bytes() == (int)(direct * 4 + nodes * RA_NODE_PAIR_BYTES));
        assert(watchlist_serialize(wire, sizeof(wire)) > 0);
        assert(watchlist_last_error()[0] == '\0');
    } else {
        assert(watchlist_last_error()[0] != '\0');
        assert(watchlist_count() == 0 && watchlist_node_count() == 0);
        assert(watchlist_serialize(wire, sizeof(wire)) == 0);
    }
    rc_runtime_destroy(&game.runtime);
}

int main(void)
{
    int format;
    unsigned fit = (RA_SNAP_MAX_BYTES - RA_NODE_MAX * RA_NODE_PAIR_BYTES) / 4;
    for (format = 0; format < 2; format++) {
        watchlist_set_format(format);
        check(RA_NODE_MAX, 1, 1);
        check(RA_NODE_MAX + 1, 1, 0);
        check(RA_NODE_MAX, 1, 1); /* failed builds must not poison the next set */
        check(RA_NODE_MAX, fit, 1);
        check(RA_NODE_MAX, fit + 1, 0);
        check(0, 1, 1);
    }
    puts("Managed RANL/RAN2 node and complete-snapshot limits fail closed; boundary plans remain usable");
    return 0;
}
