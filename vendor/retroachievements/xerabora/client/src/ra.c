#include "ra.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "console.h"
#include "rc_client_internal.h"
#include "http.h"
#include "log.h"
#include "platform.h"
#include "sound.h"
#include "webui.h"
#include "version.h"
#include "watchlist.h"

static char g_user_agent[96];

/* ---- Server transport ---------------------------------------------- */

static void server_call(const rc_api_request_t *request, rc_client_server_callback_t callback,
                        void *callback_data, rc_client_t *client)
{
    struct http_response r;
    rc_api_server_response_t resp;

    (void)client;
    memset(&resp, 0, sizeof(resp));

    if (log_trace_enabled()) {
        /* The post body carries the token; only the request type is shown. */
        const char *amp = request->post_data ? strchr(request->post_data, '&') : NULL;

        log_trace("-> %s %.*s", request->url,
                  amp ? (int)(amp - request->post_data) : 0,
                  request->post_data ? request->post_data : "");
    }

    if (http_request(request->url, request->post_data, request->content_type, g_user_agent, &r) != 0) {
        log_trace("<- transport failure");
        resp.http_status_code = -1;
        resp.body = "";
        resp.body_length = 0;
        callback(&resp, callback_data);
        free(r.body);
        return;
    }

    log_trace("<- HTTP %d, %u bytes", r.status, (unsigned)r.length);
    /* Short bodies are shown, except the login reply, which carries the token. */
    if (log_trace_enabled() && r.length > 0 && r.length < 400 && strstr(r.body, "\"Token\"") == NULL)
        log_trace("   %s", r.body);

    resp.body = r.body;
    resp.body_length = r.length;
    resp.http_status_code = r.status;
    callback(&resp, callback_data);
    free(r.body);
}

/* ---- Events ---------------------------------------------------------- */

static void on_log(const char *message, const rc_client_t *client)
{
    (void)client;
    log_trace("rc: %s", message);
}

static void on_event(const rc_client_event_t *event, rc_client_t *client)
{
    (void)client;

    switch (event->type) {
        case RC_CLIENT_EVENT_ACHIEVEMENT_TRIGGERED:
            if (event->achievement->id >= WEBUI_WARNING_ACH_ID) {
                log_warn("%s: %s", event->achievement->title,
                         event->achievement->description);
                break;
            }
            log_info("");
            log_info("Achievement unlocked: %s (%u points)",
                     event->achievement->title, event->achievement->points);
            log_info("    %s", event->achievement->description);
            log_info("");
            sound_play(SOUND_ACHIEVEMENT);
            console_notify_unlock(event->achievement->id, event->achievement->points);
            webui_note_unlock(event->achievement->id, event->achievement->title,
                              event->achievement->badge_name, event->achievement->points);
            webui_write_obs(client);
            break;
        case RC_CLIENT_EVENT_GAME_COMPLETED:
            log_info("All achievements of this game unlocked");
            break;
        case RC_CLIENT_EVENT_LEADERBOARD_STARTED:
            log_info("leaderboard attempt: %s", event->leaderboard->title);
            break;
        case RC_CLIENT_EVENT_LEADERBOARD_FAILED:
            log_info("leaderboard attempt dropped: %s", event->leaderboard->title);
            break;
        case RC_CLIENT_EVENT_LEADERBOARD_SUBMITTED:
            log_info("leaderboard result %s: %s (softcore, not posted to the site)",
                     event->leaderboard->tracker_value, event->leaderboard->title);
            break;
        case RC_CLIENT_EVENT_SERVER_ERROR:
            log_error("server: %s", event->server_error->error_message);
            break;
        case RC_CLIENT_EVENT_DISCONNECTED:
            log_warn("connection to the server lost, unlocks are queued and retried");
            sound_play(SOUND_DISCONNECT);
            break;
        case RC_CLIENT_EVENT_RECONNECTED:
            log_info("connection restored, queued unlocks sent");
            break;
        default:
            break;
    }
}

/* ---- Client ---------------------------------------------------------- */

rc_client_t *ra_create(void)
{
    rc_client_t *client = rc_client_create(watchlist_read_memory, server_call);
    char clause[64];

    if (client == NULL)
        return NULL;

    /* "xerabora/1.0.0 rcheevos/12.4": RetroAchievements identifies the
       client by this string and flags unknown ones. */
    clause[0] = '\0';
    rc_client_get_user_agent_clause(client, clause, sizeof(clause));
    snprintf(g_user_agent, sizeof(g_user_agent), "%s/%s %s", XERABORA_NAME, XERABORA_VERSION, clause);

    /* Memory arrives in snapshots; rcheevos may read only inside
       do_frame, when a complete snapshot is in place. */
    rc_client_set_allow_background_memory_reads(client, 0);

    /* Softcore. The console side can write to game memory (cheat
       engine), so claiming hardcore would be dishonest. */
    rc_client_set_hardcore_enabled(client, 0);
    /* Softcore normally deactivates leaderboards. Keep them running for
       the live trackers; rc_client still refuses to submit entries in
       softcore, so nothing reaches the site. No public setter in v12.4. */
    client->state.allow_leaderboards_in_softcore = 1;

    rc_client_set_event_handler(client, on_event);
    if (log_trace_enabled())
        rc_client_enable_logging(client, RC_CLIENT_LOG_LEVEL_VERBOSE, on_log);

    return client;
}

void ra_destroy(rc_client_t *client)
{
    rc_client_destroy(client);
}

/* ---- Login ----------------------------------------------------------- */

struct pending
{
    int done;
    int result;
};

static void on_login(int result, const char *error_message, rc_client_t *client, void *ud)
{
    struct pending *w = (struct pending *)ud;

    (void)client;
    w->done = 1;
    w->result = result;
    if (result != RC_OK)
        log_error("login failed: %s", error_message ? error_message : "no details");
}

/* server_call is blocking, so the callback has run by the time
   rc_client_begin_login_* returns. */
static int finish_login(rc_client_t *client, struct pending *w)
{
    if (!w->done)
        return RC_API_FAILURE;
    if (w->result == RC_OK)
        log_info("logged in as %s", rc_client_get_user_info(client)->display_name);
    return w->result;
}

int ra_login_with_token(rc_client_t *client, const char *user, const char *token)
{
    struct pending w = {0, RC_OK};

    rc_client_begin_login_with_token(client, user, token, on_login, &w);
    return finish_login(client, &w);
}

int ra_login_with_password(rc_client_t *client, const char *user, const char *password)
{
    struct pending w = {0, RC_OK};

    rc_client_begin_login_with_password(client, user, password, on_login, &w);
    return finish_login(client, &w);
}

/* ---- Game ------------------------------------------------------------ */

static char g_loaded_hash[33] = "";
static struct pending g_game = {0, RC_NO_GAME_LOADED};

static void on_game_loaded(int result, const char *error_message, rc_client_t *client, void *ud)
{
    (void)ud;
    g_game.done = 1;
    g_game.result = result;

    if (result == RC_OK) {
        const rc_client_game_t *game = rc_client_get_game_info(client);

        log_info("game identified: %s (id %u)", game->title, game->id);
    } else {
        log_warn("game not identified: %s", error_message ? error_message : "no details");
    }
}

int ra_load_game(rc_client_t *client, const char *hash)
{
    int i;

    if (strcmp(g_loaded_hash, hash) == 0 && g_game.result == RC_OK)
        return 1;

    g_game.done = 0;
    g_game.result = RC_NO_GAME_LOADED;
    g_loaded_hash[0] = '\0';
    watchlist_set_have_values(0);

    log_info("asking RetroAchievements about image %s", hash);
    rc_client_begin_load_game(client, hash, on_game_loaded, NULL);

    /* With background memory reads disabled rcheevos defers activation
       until an idle call, because activation reads memory. Without the
       idle loop the load callback never fires. */
    for (i = 0; i < 200 && !g_game.done; i++) {
        rc_client_idle(client);
        platform_sleep_ms(50);
    }

    if (g_game.result != RC_OK)
        return 0;

    snprintf(g_loaded_hash, sizeof(g_loaded_hash), "%s", hash);
    return 1;
}

int ra_last_load_result(void)
{
    return g_game.result;
}

const char *ra_loaded_hash(void)
{
    return g_loaded_hash;
}

int ra_game_summary(rc_client_t *client, char *title, size_t title_size,
                    unsigned *total, unsigned *unlocked, unsigned *unsupported)
{
    const rc_client_game_t *game = rc_client_get_game_info(client);
    rc_client_user_game_summary_t sum;

    if (game == NULL || g_game.result != RC_OK)
        return 0;
    rc_client_get_user_game_summary(client, &sum);
    snprintf(title, title_size, "%s", game->title ? game->title : "");
    *total = sum.num_core_achievements;
    *unlocked = sum.num_unlocked_achievements;
    /* rcheevos' own count (unreadable static addresses) plus the
       pointer readers, which stay active but cannot unlock here. */
    *unsupported = sum.num_unsupported_achievements + (unsigned)watchlist_indirect_count();
    return 1;
}

void ra_log_summary(rc_client_t *client)
{
    rc_client_user_game_summary_t sum;

    rc_client_get_user_game_summary(client, &sum);
    log_info("achievements: %u total, %u unlocked, %u cannot unlock here (pointer chains)",
             sum.num_core_achievements, sum.num_unlocked_achievements,
             sum.num_unsupported_achievements + (unsigned)watchlist_indirect_count());
}
