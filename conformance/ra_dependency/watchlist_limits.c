/* Exercise the managed compiler's public failure boundary with real memrefs. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "rc_client_internal.h"
#include "rc_consoles.h"
#include "watchlist.h"
#include "ra_watch.h"

void log_info(const char *fmt, ...) {(void)fmt;}
void log_warn(const char *fmt, ...) {(void)fmt;}
void log_detail(const char *fmt, ...) {(void)fmt;}
void log_trace(const char *fmt, ...) {(void)fmt;}
const rc_client_game_t *rc_client_get_game_info(const rc_client_t *client) { return &client->game->public_; }

static void check(unsigned nodes, unsigned direct, int expected, uint32_t console)
{
    rc_client_t client = {0};
    rc_client_game_info_t game = {0};
    char definition[128];
    unsigned char wire[18000];
    unsigned i;
    client.game = &game;
    game.public_.console_id = console;
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
        assert(watchlist_serialize(wire, sizeof(wire), NULL) > 0);
        assert(watchlist_last_error()[0] == '\0');
    } else {
        assert(watchlist_last_error()[0] != '\0');
        assert(watchlist_count() == 0 && watchlist_node_count() == 0);
        assert(watchlist_serialize(wire, sizeof(wire), NULL) == 0);
    }
    rc_runtime_destroy(&game.runtime);
}

/* xeRAbora 2.1: the image hash closes the file, after any nodes. */
static void check_hash_tail(void)
{
    static const char hash[] = "0123456789abcdef0123456789abcdef";
    rc_client_t client = {0};
    rc_client_game_info_t game = {0};
    unsigned char wire[256];
    int plain, tailed;
    unsigned magic;
    client.game = &game;
    rc_runtime_init(&game.runtime);
    assert(rc_runtime_activate_achievement(&game.runtime, 1, "I:0xX080000_0xH000040=1", NULL, 0) == RC_OK);
    assert(watchlist_build(&client) == 1);
    plain = watchlist_serialize(wire, sizeof(wire), NULL);
    assert(plain > 0);
    tailed = watchlist_serialize(wire, sizeof(wire), hash);
    assert(tailed == plain + (int)sizeof(struct ra_hash_file));
    memcpy(&magic, wire + plain, sizeof(magic));
    assert(magic == RA_HASH_MAGIC);
    assert(memcmp(wire + plain + 4, hash, RA_HASH_LEN) == 0);
    /* Anything but 32 digits is left off, never truncated. */
    assert(watchlist_serialize(wire, sizeof(wire), "abc") == plain);
    rc_runtime_destroy(&game.runtime);
}

/* xeRAbora 2.2: chains through Remember/Recall compile too. */
static void check_recall(void)
{
    rc_client_t client = {0};
    rc_client_game_info_t game = {0};
    client.game = &game;
    rc_runtime_init(&game.runtime);
    assert(rc_runtime_activate_achievement(&game.runtime, 1,
           "K:0xX080000_I:{recall}_0xH000040=1", NULL, 0) == RC_OK);
    assert(watchlist_build(&client) == 1);
    assert(watchlist_node_count() == 1);
    rc_runtime_destroy(&game.runtime);
}

int main(void)
{
    int format;
    for (format = 0; format < 2; format++) {
        /* A RANL console takes the old 128 chains; RAN2 takes 512 for a
           PS2 game and 256 for a PS1 game, as xeRAbora 2.2. */
        unsigned cap = format ? RA_NODE_MAX : RA_NODE_MAX_LEGACY;
        unsigned cap_ps1 = format ? RA_NODE_MAX_PS1 : RA_NODE_MAX_LEGACY;
        unsigned fit = (RA_SNAP_MAX_BYTES - cap * RA_NODE_PAIR_BYTES) / 4;
        watchlist_set_format(format);
        check(cap, 1, 1, RC_CONSOLE_PLAYSTATION_2);
        check(cap + 1, 1, 0, RC_CONSOLE_PLAYSTATION_2);
        check(cap, 1, 1, RC_CONSOLE_PLAYSTATION_2); /* failed builds must not poison the next set */
        check(cap, fit, 1, RC_CONSOLE_PLAYSTATION_2);
        check(cap, fit + 1, 0, RC_CONSOLE_PLAYSTATION_2);
        check(cap_ps1, 1, 1, RC_CONSOLE_PLAYSTATION);
        check(cap_ps1 + 1, 1, 0, RC_CONSOLE_PLAYSTATION);
        check(0, 1, 1, RC_CONSOLE_PLAYSTATION_2);
        check_hash_tail();
        check_recall();
    }
    puts("Managed RANL/RAN2 node and complete-snapshot limits fail closed; boundary plans remain usable");
    return 0;
}
