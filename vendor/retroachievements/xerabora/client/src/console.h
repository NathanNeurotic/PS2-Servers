/*
  Requests from the console: discovery, image lookup, watch list chunks.
  Also the table mapping game serials to image hashes, persisted in the
  config directory so a client restart mid-game needs no new image check.
*/
#ifndef XERABORA_CONSOLE_H
#define XERABORA_CONSOLE_H

#include <stddef.h>

#include "platform.h"
#include "rc_client.h"

/* Loads the saved serial-to-hash table. */
void console_load_games(void);

/* Remember that <serial> runs the image with <hash>. */
void console_remember_game(const char *serial, const char *hash);
const char *console_hash_for(const char *serial);

/* Handles a request packet if it is one. Returns 0 when the packet is
   telemetry, 1 for a handled or ignored request, 2 for a discovery
   request (the console has just found this PC). */
int console_serve(sock_t sock, const char *pkt, size_t len,
                  const struct sockaddr_in *from, rc_client_t *client);

/* The set for an image hash, for the game the console is running.
   Returns 1 when its watch list is ready (rc_client holds that game),
   0 when a load is in flight or was just started, -1 when the hash
   failed recently; *reason then says why. Never blocks. */
int console_request_set(rc_client_t *client, const char *hash, const char **reason);

/* True while the worker loads a set. The main loop then leaves rc_client
   and the watch list alone: no frames, no idle, no page state from it. */
int console_ident_busy(void);

/* Picks up a finished load: returns 1 once per job with its hash and
   outcome, 0 when nothing finished. Call from the main loop; the
   worker's results are visible only after this. */
int console_ident_collect(char *hash, size_t hash_size, int *ok, char *reason, size_t reason_size);

/* Tell the console an achievement unlocked, so it can show a notice over
   the game. Goes to the address discovery recorded; returns 0 when no
   console has been discovered yet. */
int console_notify_unlock(unsigned id, unsigned points);
/* Asks the console to leave the game for the loader menu, the way the
   in-game reset combo does. 1 when sent, 0 with no console address. */
int console_send_reset(void);
int console_send_badge_chunk(const unsigned char *px, int idx);

/* Any packet from the console names its address: a client started while
   the game already runs sees no discovery, only telemetry. */
void console_learn(sock_t sock, const struct sockaddr_in *from);

#endif
