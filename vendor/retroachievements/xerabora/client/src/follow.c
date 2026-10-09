#include "follow.h"

#include <stdio.h>
#include <string.h>

#include "log.h"
#include "sound.h"
#include "webui.h"

/* Two calls per poll, profile and recent unlocks. Twenty seconds keeps
   the account under a few requests a minute; an unlock shows within
   that. The console being live drops it to once a minute. */
#define FOLLOW_INTERVAL 20
#define FOLLOW_INTERVAL_IDLE 60
#define FOLLOW_BACKOFF 120
#define FOLLOW_SUMMARY_INTERVAL 60

static struct follow_state g;
static time_t g_last_try;
static time_t g_last_summary;

void follow_set_enabled(int on)
{
    g.on = on ? 1 : 0;
    if (!g.on)
        follow_reset();
    else if (g.started == 0)
        g.started = time(NULL);
}

int follow_enabled(void)
{
    return g.on;
}

void follow_reset(void)
{
    int on = g.on;

    memset(&g, 0, sizeof(g));
    g.on = on;
    g.started = on ? time(NULL) : 0;
    g_last_try = 0;
}

const struct follow_state *follow_get(void)
{
    return &g;
}

int follow_active(int console_active)
{
    return g.on && g.game_id != 0 && !console_active;
}

static void load_game(unsigned id)
{
    struct raweb_game_progress info;
    int n;

    memset(&info, 0, sizeof(info));
    n = raweb_game_progress(id, &info, g.ach, FOLLOW_MAX_ACH);
    if (n <= 0 && info.title[0] == '\0') {
        log_warn("following: could not load game %u from the Web API", id);
        return;
    }
    g.game = info;
    g.ach_count = n;
    g.game_id = id;
    g.game_since = time(NULL);
    log_info("following %s (%s): %u of %u unlocked", info.title, info.console,
             info.awarded, info.achievements_total);
    webui_mark_dirty();
}

/* A fresh unlock from the recent list: mark it in the set, tell the
   page, play the sound. Anything older than the poll window, or
   already known, is skipped. */
static void note_unlock(const struct raweb_unlock *u)
{
    int i;

    for (i = 0; i < g.session_count; i++)
        if (g.session[i].id == u->achievement_id)
            return;
    for (i = 0; i < g.ach_count; i++) {
        if (g.ach[i].id == u->achievement_id) {
            if (g.ach[i].date_earned[0] != '\0')
                return; /* earned before we started following */
            snprintf(g.ach[i].date_earned, sizeof(g.ach[i].date_earned), "%s", u->date);
            g.ach[i].earned_hardcore = u->hardcore;
            g.game.awarded++;
            break;
        }
    }

    if (g.session_count < FOLLOW_MAX_SESSION) {
        memmove(&g.session[1], &g.session[0], (size_t)g.session_count * sizeof(g.session[0]));
        g.session_count++;
    } else {
        memmove(&g.session[1], &g.session[0], (size_t)(FOLLOW_MAX_SESSION - 1) * sizeof(g.session[0]));
    }
    g.session[0].id = u->achievement_id;
    snprintf(g.session[0].title, sizeof(g.session[0].title), "%s", u->title);
    snprintf(g.session[0].badge, sizeof(g.session[0].badge), "%s", u->badge);
    g.session[0].points = u->points;
    g.session[0].hardcore = u->hardcore;
    g.session[0].at = time(NULL);

    log_info("unlocked elsewhere: %s (+%u) in %s", u->title, u->points, u->game_title);
    sound_play(SOUND_ACHIEVEMENT);
    webui_note_unlock(u->achievement_id, u->title, u->badge, u->points);
}

void follow_tick(int console_active)
{
    struct raweb_profile me;
    struct raweb_unlock recent[16];
    time_t now = time(NULL);
    int interval, n, i;

    if (!g.on || !raweb_have_credentials())
        return;

    interval = console_active ? FOLLOW_INTERVAL_IDLE : FOLLOW_INTERVAL;
    if (g.failures > 0)
        interval = FOLLOW_BACKOFF;
    if (now - g_last_try < interval)
        return;
    g_last_try = now;

    if (!raweb_profile(&me)) {
        g.failures++;
        if (g.failures == 1)
            log_warn("following: the Web API did not answer; retrying every %d s", FOLLOW_BACKOFF);
        return;
    }
    if (g.failures > 0)
        log_info("following: the Web API is back");
    g.failures = 0;
    g.polled = now;

    if (strcmp(me.rich_presence, g.rich_presence) != 0) {
        snprintf(g.rich_presence, sizeof(g.rich_presence), "%s", me.rich_presence);
        g.rp_changed = now;
        webui_mark_dirty();
    }

    if (me.last_game_id != 0 && me.last_game_id != g.game_id)
        load_game(me.last_game_id);

    /* The summary is slow on the server side; once a minute is plenty
       for "online" and the line's timestamp. */
    if (now - g_last_summary >= FOLLOW_SUMMARY_INTERVAL) {
        struct raweb_summary sum;

        g_last_summary = now;
        if (raweb_summary(&sum)) {
            if (sum.online != g.online ||
                strcmp(sum.rich_presence_date, g.rich_presence_date) != 0)
                webui_mark_dirty();
            g.online = sum.online;
            snprintf(g.rich_presence_date, sizeof(g.rich_presence_date), "%s", sum.rich_presence_date);
        }
    }

    /* Two poll windows back, so a slow answer does not lose one. */
    n = raweb_recent_unlocks(2 * FOLLOW_INTERVAL / 60 + 2, recent, (int)(sizeof(recent) / sizeof(recent[0])));
    for (i = n - 1; i >= 0; i--) {
        if (g.game_id != 0 && recent[i].game_id != g.game_id)
            continue;
        note_unlock(&recent[i]);
    }
}
