/*
  rc_client glue: login, game loading, events.
*/
#ifndef XERABORA_RA_H
#define XERABORA_RA_H

#include <stddef.h>

#include "rc_client.h"

rc_client_t *ra_create(void);
void ra_destroy(rc_client_t *client);

/* Both return the rcheevos result code: RC_OK on success,
   RC_INVALID_CREDENTIALS / RC_EXPIRED_TOKEN when the server rejected
   the login, another code when the server could not be reached. After a
   password login the token is in rc_client_get_user_info(client)->token. */
int ra_login_with_token(rc_client_t *client, const char *user, const char *token);
int ra_login_with_password(rc_client_t *client, const char *user, const char *password);

/* Loads the game identified by an RA image hash, asking the server.
   A second call with the same hash is a no-op. Returns 1 on success;
   ra_last_load_result() then tells why it failed. */
int ra_load_game(rc_client_t *client, const char *hash);
int ra_last_load_result(void);

/* Hash of the game currently loaded, or "" when none. */
const char *ra_loaded_hash(void);

/* Logs the achievement summary of the loaded game. */
void ra_log_summary(rc_client_t *client);

/* Title and achievement counts of the loaded game: total core, already
   unlocked, and unsupported (pointer chains the console cannot read).
   Returns 0 when no game is loaded. */
int ra_game_summary(rc_client_t *client, char *title, size_t title_size,
                    unsigned *total, unsigned *unlocked, unsigned *unsupported);

#endif
