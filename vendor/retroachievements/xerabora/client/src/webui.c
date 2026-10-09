#include "webui.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "config.h"
#include "console.h"
#include "follow.h"
#include "log.h"
#include "ra.h"
#include "raweb.h"
#include "ui_page.h"
#include "version.h"

#ifdef _WIN32
#include <shellapi.h>
#include <windows.h>
#endif

/* Session state the page renders. Written by the client as things
   happen, read when a request comes in; both happen on the same thread,
   so no locking. */
static struct
{
    char console_ip[32];
    int connected;
    int logged_in;
    int has_webapi;
    char user[64];
    char serial[16];
    char hash[33];
    char title[128];
    char status[24];
    unsigned long packets;
    unsigned long frames;
    unsigned long gaps;
    unsigned long dupes;
    unsigned long torn;
    unsigned watch_parts;
    unsigned watch_bytes;
    unsigned watch_addresses;
    time_t started;
    time_t ended;   /* when the link was lost; 0 while it is up */

    /* Set when the page's layout is out of date (new game, unlock, status
       change): the next push carries the full state. In between, one small
       delta per snapshot keeps the numbers fresh and the DOM in place. */
    int dirty;

    struct
    {
        unsigned id;
        char title[96];
        char badge[16];
        unsigned points;
        time_t at;
    } unlocks[16];
    int unlock_count;
} g;

/* The page is the only way to end a windowless process gracefully. */
static volatile int g_quit = 0;

static void web_cache_expire(void);

int webui_quit_requested(void)
{
    return g_quit;
}

void webui_set_login(int ok, const char *user)
{
    g.dirty = 1;
    g.logged_in = ok;
    snprintf(g.user, sizeof(g.user), "%s", user != NULL ? user : "");
}

void webui_set_webapi(int ok)
{
    g.dirty = 1;
    g.has_webapi = ok;
}

void webui_mark_dirty(void)
{
    g.dirty = 1;
}

void webui_set_console(const char *ip, int connected)
{
    g.dirty = 1;
    if (ip != NULL)
        snprintf(g.console_ip, sizeof(g.console_ip), "%s", ip);
    if (connected && !g.connected) {
        g.started = time(NULL);
        g.ended = 0;
    } else if (!connected && g.connected) {
        g.ended = time(NULL);
    }
    g.connected = connected;
}

/* The session clock: from the connect to now, or to the moment the
   link was lost. */
static long session_seconds(void)
{
    if (g.started == 0)
        return 0;
    return (long)((g.ended != 0 ? g.ended : time(NULL)) - g.started);
}

void webui_set_game(const char *serial, const char *hash, const char *title)
{
    g.dirty = 1;
    snprintf(g.serial, sizeof(g.serial), "%s", serial != NULL ? serial : "");
    snprintf(g.hash, sizeof(g.hash), "%s", hash != NULL ? hash : "");
    snprintf(g.title, sizeof(g.title), "%s", title != NULL ? title : "");
}

void webui_set_game_title(const char *title)
{
    g.dirty = 1;
    snprintf(g.title, sizeof(g.title), "%s", title != NULL ? title : "");
}

void webui_note_unlock(unsigned id, const char *title, const char *badge, unsigned points)
{
    int i;

    /* Newest first, oldest pushed off the end. */
    for (i = (int)(sizeof(g.unlocks) / sizeof(g.unlocks[0])) - 1; i > 0; i--)
        g.unlocks[i] = g.unlocks[i - 1];

    g.unlocks[0].id = id;
    snprintf(g.unlocks[0].title, sizeof(g.unlocks[0].title), "%s", title != NULL ? title : "");
    snprintf(g.unlocks[0].badge, sizeof(g.unlocks[0].badge), "%s", badge != NULL ? badge : "");
    g.unlocks[0].points = points;
    g.unlocks[0].at = time(NULL);
    if (g.unlock_count < (int)(sizeof(g.unlocks) / sizeof(g.unlocks[0])))
        g.unlock_count++;
    /* The cached Web API payloads now lie about this game: the next
       /game, /library and /me requests must ask the server again. */
    web_cache_expire();
    g.dirty = 1;
}

void webui_note_packet(void)
{
    g.packets++;
}

void webui_set_status(const char *status)
{
    g.dirty = 1;
    snprintf(g.status, sizeof(g.status), "%s", status != NULL ? status : "");
}

void webui_set_stats(unsigned long frames, unsigned long gaps,
                     unsigned long dupes, unsigned long torn,
                     unsigned parts, unsigned bytes, unsigned addresses)
{
    g.frames = frames;
    g.gaps = gaps;
    g.dupes = dupes;
    g.torn = torn;
    g.watch_parts = parts;
    g.watch_bytes = bytes;
    g.watch_addresses = addresses;
}

/* ---- The listener ------------------------------------------------------- */

/* The port that answered, which is not always the one asked
   for: another copy of this client may already be running. */
static int g_port;

int webui_port(void)
{
    return g_port;
}

static int g_lan;
static int g_rebind;

void webui_set_lan(int on)
{
    g_lan = on ? 1 : 0;
}

int webui_lan(void)
{
    return g_lan;
}

/* The address another device would type. A connected UDP socket does
   not send anything; it only picks the interface that routes to the
   console, or to the internet when no console has been seen yet. */
static void lan_address(char *out, size_t size)
{
    struct sockaddr_in to, me;
    socklen_t len = sizeof(me);
    sock_t s;

    out[0] = '\0';
    s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s == SOCK_INVALID)
        return;
    memset(&to, 0, sizeof(to));
    to.sin_family = AF_INET;
    to.sin_port = htons(9);
    to.sin_addr.s_addr = g.console_ip[0] != '\0' ? inet_addr(g.console_ip) : INADDR_NONE;
    if (to.sin_addr.s_addr == INADDR_NONE)
        to.sin_addr.s_addr = inet_addr("1.1.1.1");
    if (connect(s, (struct sockaddr *)&to, sizeof(to)) == 0 &&
        getsockname(s, (struct sockaddr *)&me, &len) == 0)
        snprintf(out, size, "%s", inet_ntoa(me.sin_addr));
    sock_close(s);
}

sock_t webui_start(int port)
{
    struct sockaddr_in addr;
    sock_t s;
    int tries;

    /* Up to five ports from the one asked for. A second copy of the
       client, or anything else sitting on 18280, then costs a number
       rather than the whole interface. */
    for (tries = 0; tries < 1; tries++, port++) {
        s = socket(AF_INET, SOCK_STREAM, 0);
        if (s == SOCK_INVALID)
            return SOCK_INVALID;

#ifndef _WIN32
        /* POSIX only: on Windows SO_REUSEADDR lets a second bind succeed on
           a held port and the two sockets fight for connections, which
           looks like a page that refuses to load. */
        {
            int on = 1;

            setsockopt(s, SOL_SOCKET, SO_REUSEADDR, (const char *)&on, sizeof(on));
        }
#else
        /* Windows lets a wildcard bind succeed next to another copy's
           loopback bind on the same port; the browser then reaches
           whichever copy owns 127.0.0.1. Exclusive use makes the second
           bind fail, so this copy moves to the next port and finds the
           first one the normal way. */
        {
            int on = 1;

            setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char *)&on, sizeof(on));
        }
#endif

        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        /* Loopback unless the user asked for the network: the page
           carries the settings and the off switch, so opening it to
           every interface is a choice, not an accident. */
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = htons((unsigned short)port);

        if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) == 0 && listen(s, 8) == 0) {
            g_port = port;
            if (g_lan) {
                char ip[48];

                lan_address(ip, sizeof(ip));
                if (ip[0] != '\0')
                    log_info("interface open to the network at http://%s:%d/", ip, port);
            }
            return s;
        }

        sock_close(s);
    }

    log_warn("no free port for the interface near %d; running without it", port - 5);
    return SOCK_INVALID;
}

sock_t webui_rebind(sock_t listener)
{
    int port;

    if (!g_rebind)
        return listener;
    g_rebind = 0;
    port = g_port;
    if (listener != SOCK_INVALID)
        sock_close(listener);
    listener = webui_start(port);
    if (listener == SOCK_INVALID)
        log_warn("the interface could not be reopened on port %d", port);
    return listener;
}

/* When set, GET / serves this file from disk instead of the built-in
   page: the edit-refresh loop for working on the interface. */
static char g_ui_file[512];

void webui_set_ui_file(const char *path)
{
    snprintf(g_ui_file, sizeof(g_ui_file), "%s", path != NULL ? path : "");
}

/* The pages that asked for the live stream: the PC, a phone, an OBS
   source. With one slot the browsers took turns stealing the stream
   from each other every few seconds. A fifth page replaces the oldest. */
#define WEBUI_STREAMS 4
static sock_t g_streams[WEBUI_STREAMS] = {SOCK_INVALID, SOCK_INVALID, SOCK_INVALID, SOCK_INVALID};
static int g_stream_next;
static unsigned long g_stream_sent;

/* When a browser last talked to us: any request, or a live stream still
   accepting writes. Once a page has been seen, a long silence means the
   last page was closed. */
static time_t g_page_contact;
static int g_page_seen;
static int stream_count(void);

int webui_page_gone(int grace_seconds)
{
    if (!g_page_seen || stream_count() > 0)
        return 0;
    return time(NULL) - g_page_contact > grace_seconds;
}

static int stream_count(void)
{
    int i, n = 0;

    for (i = 0; i < WEBUI_STREAMS; i++)
        if (g_streams[i] != SOCK_INVALID)
            n++;
    return n;
}

static void stream_add(sock_t c)
{
    int i;

    for (i = 0; i < WEBUI_STREAMS; i++) {
        if (g_streams[i] == SOCK_INVALID) {
            g_streams[i] = c;
            return;
        }
    }
    sock_close(g_streams[g_stream_next]);
    g_streams[g_stream_next] = c;
    g_stream_next = (g_stream_next + 1) % WEBUI_STREAMS;
}

void webui_stop(sock_t listener)
{
    int i;

    for (i = 0; i < WEBUI_STREAMS; i++) {
        if (g_streams[i] != SOCK_INVALID) {
            sock_close(g_streams[i]);
            g_streams[i] = SOCK_INVALID;
        }
    }
    if (listener != SOCK_INVALID)
        sock_close(listener);
}

/* ---- JSON out ----------------------------------------------------------- */

/* Escapes what JSON forbids in a string. Titles arrive from the RA
   server and from game memory, so nothing is assumed about them. */
static void json_str(char **p, char *end, const char *s)
{
    char *o = *p;

    if (o < end)
        *o++ = '"';
    for (; s != NULL && *s != '\0' && o + 8 < end; s++) {
        unsigned char c = (unsigned char)*s;

        switch (c) {
        case '"':  *o++ = '\\'; *o++ = '"'; break;
        case '\\': *o++ = '\\'; *o++ = '\\'; break;
        case '\n': *o++ = '\\'; *o++ = 'n'; break;
        case '\r': *o++ = '\\'; *o++ = 'r'; break;
        case '\t': *o++ = '\\'; *o++ = 't'; break;
        default:
            if (c < 0x20)
                o += snprintf(o, (size_t)(end - o), "\\u%04x", c);
            else
                *o++ = (char)c;
        }
    }
    if (o < end)
        *o++ = '"';
    *p = o;
}

static void json_field(char **p, char *end, const char *key, const char *value)
{
    *p += snprintf(*p, (size_t)(end - *p), "\"%s\":", key);
    json_str(p, end, value);
}

/* The console is the live source when it is connected and rc_client
   holds a game for it. */
static int console_has_game(rc_client_t *client)
{
    return g.connected && client != NULL && rc_client_get_game_info(client) != NULL;
}

/* The whole state as one object. Small enough to build in a buffer;
   the achievement list is the only part that can grow, and it is
   capped by the set size. */
static int build_state(char *buf, size_t size, rc_client_t *client)
{
    char *p = buf, *end = buf + size;
    int i;

    p += snprintf(p, (size_t)(end - p), "{");

    p += snprintf(p, (size_t)(end - p), "\"version\":\"%s\",", XERABORA_VERSION);

    p += snprintf(p, (size_t)(end - p), "\"login\":{\"ok\":%s,\"webapi\":%s,",
                  g.logged_in ? "true" : "false", g.has_webapi ? "true" : "false");
    json_field(&p, end, "user", g.user);
    p += snprintf(p, (size_t)(end - p), "},");

    p += snprintf(p, (size_t)(end - p), "\"console\":{\"connected\":%s,", g.connected ? "true" : "false");
    json_field(&p, end, "ip", g.console_ip);
    p += snprintf(p, (size_t)(end - p), ",");
    json_field(&p, end, "status", g.status);
    p += snprintf(p, (size_t)(end - p),
                  ",\"packets\":%lu,\"frames\":%lu,\"gaps\":%lu,\"dupes\":%lu,\"torn\":%lu"
                  ",\"watch\":{\"parts\":%u,\"bytes\":%u,\"addresses\":%u},\"seconds\":%ld},",
                  g.packets, g.frames, g.gaps, g.dupes, g.torn,
                  g.watch_parts, g.watch_bytes, g.watch_addresses,
                  session_seconds());

    {
        char ip[48] = "", url[80] = "";

        if (g_lan) {
            lan_address(ip, sizeof(ip));
            if (ip[0] != '\0')
                snprintf(url, sizeof(url), "http://%s:%d/", ip, g_port);
        }
        p += snprintf(p, (size_t)(end - p), "\"lan\":{\"on\":%s,", g_lan ? "true" : "false");
        json_field(&p, end, "url", url);
        p += snprintf(p, (size_t)(end - p), "},");
    }

    /* Following: the account's play elsewhere, from the Web API. It
       shows on LIVE only while the console is not the source. */
    {
        const struct follow_state *f = follow_get();
        int active = follow_active(console_has_game(client));
        time_t now = time(NULL);

        p += snprintf(p, (size_t)(end - p), "\"follow\":{\"on\":%s,\"active\":%s,\"game_id\":%u,",
                      f->on ? "true" : "false", active ? "true" : "false", f->game_id);
        json_field(&p, end, "rp", f->rich_presence);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "rp_date", f->rich_presence_date);
        p += snprintf(p, (size_t)(end - p),
                      ",\"online\":%s,\"rp_ago\":%ld,\"since\":%ld,\"polled_ago\":%ld,\"failures\":%d,\"session\":[",
                      f->online ? "true" : "false",
                      f->rp_changed != 0 ? (long)(now - f->rp_changed) : -1L,
                      f->game_since != 0 ? (long)(now - f->game_since) : 0L,
                      f->polled != 0 ? (long)(now - f->polled) : -1L, f->failures);
        for (i = 0; i < f->session_count && end - p > 256; i++) {
            if (i > 0)
                p += snprintf(p, (size_t)(end - p), ",");
            p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", f->session[i].id);
            json_field(&p, end, "title", f->session[i].title);
            p += snprintf(p, (size_t)(end - p), ",");
            json_field(&p, end, "badge", f->session[i].badge);
            p += snprintf(p, (size_t)(end - p), ",\"points\":%u,\"hardcore\":%s,\"ago\":%ld}",
                          f->session[i].points, f->session[i].hardcore ? "true" : "false",
                          (long)(now - f->session[i].at));
        }
        p += snprintf(p, (size_t)(end - p), "]},");

        if (active) {
            p += snprintf(p, (size_t)(end - p), "\"game\":{\"id\":%u,\"serial\":\"\",\"hash\":\"\",", f->game_id);
            json_field(&p, end, "title", f->game.title);
            p += snprintf(p, (size_t)(end - p), ",");
            json_field(&p, end, "console", f->game.console);
            p += snprintf(p, (size_t)(end - p), ",");
            json_field(&p, end, "icon", f->game.image_icon);
            p += snprintf(p, (size_t)(end - p), ",\"source\":\"web\",\"achievements\":[");
            for (i = 0; i < f->ach_count && end - p > 512; i++) {
                const struct raweb_achievement *a = &f->ach[i];
                int done = a->date_earned[0] != '\0';
                unsigned type = strcmp(a->type, "missable") == 0 ? 1
                              : strcmp(a->type, "progression") == 0 ? 2
                              : strcmp(a->type, "win_condition") == 0 ? 3 : 0;

                if (i > 0)
                    p += snprintf(p, (size_t)(end - p), ",");
                p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", a->id);
                json_field(&p, end, "title", a->title);
                p += snprintf(p, (size_t)(end - p), ",");
                json_field(&p, end, "description", a->description);
                p += snprintf(p, (size_t)(end - p), ",");
                json_field(&p, end, "badge", a->badge);
                p += snprintf(p, (size_t)(end - p),
                              ",\"measured\":\"\",\"points\":%u,\"state\":%u,\"percent\":0,\"type\":%u,\"bucket\":%u}",
                              a->points, done ? 2u : 1u, type, done ? 2u : 1u);
            }
            p += snprintf(p, (size_t)(end - p), "],\"tracking\":[]},");
            goto unlocks;
        }
    }

    /* The RA game id lets the page pull the same game's Web API payload
       (types, median times) and lay it over the live list.

       The head has to describe the set the page is about to draw, not the
       last serial the console named: an image check from the menu loads
       another game's set, and while nothing is running the two drift
       apart. So the title and the id come from rc_client, and the serial
       is shown only while the loaded set is the one the console runs. */
    {
        const rc_client_game_t *info = client != NULL ? rc_client_get_game_info(client) : NULL;
        const char *loaded = ra_loaded_hash();
        int running = g.hash[0] != '\0' && strcmp(g.hash, loaded) == 0;

        p += snprintf(p, (size_t)(end - p), "\"game\":{\"id\":%u,",
                      info != NULL ? info->id : 0);
        json_field(&p, end, "serial", running ? g.serial : "");
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "hash", loaded);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "title",
                   info != NULL && info->title[0] != '\0' ? info->title : g.title);
        p += snprintf(p, (size_t)(end - p), ",\"checked_only\":%d",
                      (info != NULL && !running) ? 1 : 0);
    }

    /* Achievements come from rc_client, which is where the live
       progress of a measured achievement lives -- the one thing the
       Web API cannot tell us. */
    p += snprintf(p, (size_t)(end - p), ",\"achievements\":[");
    if (client != NULL) {
        rc_client_achievement_list_t *list =
            rc_client_create_achievement_list(client,
                                              RC_CLIENT_ACHIEVEMENT_CATEGORY_CORE,
                                              RC_CLIENT_ACHIEVEMENT_LIST_GROUPING_PROGRESS);
        int first = 1;

        if (list != NULL) {
            unsigned b, a;

            for (b = 0; b < list->num_buckets; b++) {
                for (a = 0; a < list->buckets[b].num_achievements; a++) {
                    const rc_client_achievement_t *ach = list->buckets[b].achievements[a];

                    if (ach->id >= WEBUI_WARNING_ACH_ID)
                        continue;
                    if (end - p < 512)
                        break;
                    if (!first)
                        p += snprintf(p, (size_t)(end - p), ",");
                    first = 0;

                    p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", ach->id);
                    json_field(&p, end, "title", ach->title);
                    p += snprintf(p, (size_t)(end - p), ",");
                    json_field(&p, end, "description", ach->description);
                    p += snprintf(p, (size_t)(end - p), ",");
                    json_field(&p, end, "badge", ach->badge_name);
                    p += snprintf(p, (size_t)(end - p), ",");
                    json_field(&p, end, "measured", ach->measured_progress);
                    p += snprintf(p, (size_t)(end - p),
                                  ",\"points\":%u,\"state\":%u,\"percent\":%.4f,\"type\":%u,\"bucket\":%u,\"subset\":%u}",
                                  ach->points, ach->state, ach->measured_percent,
                                  (unsigned)ach->type, list->buckets[b].bucket_type,
                                  list->buckets[b].subset_id);
                }
            }
            rc_client_destroy_achievement_list(list);
        }
    }
    p += snprintf(p, (size_t)(end - p), "]");
    /* The subsets of the loaded set, when the server sent more than the
       core one; the page groups the list by them. */
    p += snprintf(p, (size_t)(end - p), ",\"subsets\":[");
    if (client != NULL) {
        rc_client_subset_list_t *subs = rc_client_create_subset_list(client);

        if (subs != NULL) {
            unsigned i;

            for (i = 0; i < subs->num_subsets && end - p > 256; i++) {
                const rc_client_subset_t *sub = subs->subsets[i];

                if (i > 0)
                    p += snprintf(p, (size_t)(end - p), ",");
                p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", sub->id);
                json_field(&p, end, "title", sub->title);
                p += snprintf(p, (size_t)(end - p), ",");
                json_field(&p, end, "badge", sub->badge_name);
                p += snprintf(p, (size_t)(end - p), ",\"achievements\":%u}", sub->num_achievements);
            }
            rc_client_destroy_subset_list(subs);
        }
    }
    p += snprintf(p, (size_t)(end - p), "]");
    /* Leaderboards the console is tracking right now: the value moves
       with the game, and nothing outside a memory feed can show it. */
    p += snprintf(p, (size_t)(end - p), ",\"tracking\":[");
    /* Only while the console feeds frames: rcheevos leaves a leaderboard
       active and its tracker holding the last value, so without this the
       page shows a frozen time long after the link is gone. */
    if (client != NULL && g.connected) {
        rc_client_leaderboard_list_t *lbs =
            rc_client_create_leaderboard_list(client,
                                              RC_CLIENT_LEADERBOARD_LIST_GROUPING_TRACKING);
        int first = 1;

        if (lbs != NULL) {
            unsigned b, l;

            for (b = 0; b < lbs->num_buckets; b++) {
                for (l = 0; l < lbs->buckets[b].num_leaderboards; l++) {
                    const rc_client_leaderboard_t *lb = lbs->buckets[b].leaderboards[l];

                    if (lb->state != RC_CLIENT_LEADERBOARD_STATE_ACTIVE || end - p < 320)
                        continue;
                    if (!first)
                        p += snprintf(p, (size_t)(end - p), ",");
                    first = 0;

                    p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", lb->id);
                    json_field(&p, end, "title", lb->title);
                    p += snprintf(p, (size_t)(end - p), ",");
                    json_field(&p, end, "value", lb->tracker_value);
                    p += snprintf(p, (size_t)(end - p), "}");
                }
            }
            rc_client_destroy_leaderboard_list(lbs);
        }
    }
    p += snprintf(p, (size_t)(end - p), "]},");

unlocks:
    p += snprintf(p, (size_t)(end - p), "\"unlocks\":[");
    for (i = 0; i < g.unlock_count && end - p > 256; i++) {
        if (i > 0)
            p += snprintf(p, (size_t)(end - p), ",");
        p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", g.unlocks[i].id);
        json_field(&p, end, "title", g.unlocks[i].title);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "badge", g.unlocks[i].badge);
        p += snprintf(p, (size_t)(end - p), ",\"points\":%u,\"ago\":%ld}",
                      g.unlocks[i].points, (long)(time(NULL) - g.unlocks[i].at));
    }
    p += snprintf(p, (size_t)(end - p), "]");

    p += snprintf(p, (size_t)(end - p), "}");
    return (int)(p - buf);
}

/* Between full states only the moving numbers go out: counters, measured
   progress, trackers. The layout stays, so CSS transitions survive. Full
   state once a second and on anything structural (g.dirty). */
static int build_delta(char *buf, size_t size, rc_client_t *client)
{
    char *p = buf, *end = buf + size;

    p += snprintf(p, (size_t)(end - p),
                  "{\"delta\":1,\"console\":{\"frames\":%lu,\"gaps\":%lu,\"dupes\":%lu,"
                  "\"torn\":%lu,\"seconds\":%ld}",
                  g.frames, g.gaps, g.dupes, g.torn,
                  session_seconds());

    p += snprintf(p, (size_t)(end - p), ",\"live\":[");
    if (client != NULL) {
        rc_client_achievement_list_t *list =
            rc_client_create_achievement_list(client,
                                              RC_CLIENT_ACHIEVEMENT_CATEGORY_CORE,
                                              RC_CLIENT_ACHIEVEMENT_LIST_GROUPING_PROGRESS);
        int first = 1;

        if (list != NULL) {
            unsigned b, a;

            for (b = 0; b < list->num_buckets; b++) {
                for (a = 0; a < list->buckets[b].num_achievements; a++) {
                    const rc_client_achievement_t *ach = list->buckets[b].achievements[a];

                    if (ach->state != RC_CLIENT_ACHIEVEMENT_STATE_ACTIVE)
                        continue;
                    if (ach->measured_progress[0] == '\0' && ach->measured_percent <= 0)
                        continue;
                    if (end - p < 192)
                        break;
                    if (!first)
                        p += snprintf(p, (size_t)(end - p), ",");
                    first = 0;

                    p += snprintf(p, (size_t)(end - p), "{\"id\":%u,\"m\":", ach->id);
                    json_str(&p, end, ach->measured_progress);
                    p += snprintf(p, (size_t)(end - p), ",\"p\":%.4f}", ach->measured_percent);
                }
            }
            rc_client_destroy_achievement_list(list);
        }
    }

    p += snprintf(p, (size_t)(end - p), "],\"tracking\":[");
    /* Only while the console feeds frames: rcheevos leaves a leaderboard
       active and its tracker holding the last value, so without this the
       page shows a frozen time long after the link is gone. */
    if (client != NULL && g.connected) {
        rc_client_leaderboard_list_t *lbs =
            rc_client_create_leaderboard_list(client,
                                              RC_CLIENT_LEADERBOARD_LIST_GROUPING_TRACKING);
        int first = 1;

        if (lbs != NULL) {
            unsigned b, l;

            for (b = 0; b < lbs->num_buckets; b++) {
                for (l = 0; l < lbs->buckets[b].num_leaderboards; l++) {
                    const rc_client_leaderboard_t *lb = lbs->buckets[b].leaderboards[l];

                    if (lb->state != RC_CLIENT_LEADERBOARD_STATE_ACTIVE || end - p < 192)
                        continue;
                    if (!first)
                        p += snprintf(p, (size_t)(end - p), ",");
                    first = 0;

                    p += snprintf(p, (size_t)(end - p), "{\"id\":%u,\"v\":", lb->id);
                    json_str(&p, end, lb->tracker_value);
                    p += snprintf(p, (size_t)(end - p), "}");
                }
            }
            rc_client_destroy_leaderboard_list(lbs);
        }
    }
    p += snprintf(p, (size_t)(end - p), "]}");
    return (int)(p - buf);
}

/* ---- The Web API, cached: the page polls, the RA server must not. Each
   answer is kept for a while and served from memory. */

#define LIBRARY_TTL 120 /* seconds */
#define GAME_TTL 30

static struct raweb_game g_lib[500];
static int g_lib_count;
static time_t g_lib_at;

static struct raweb_profile g_me;
static time_t g_me_at;

static struct raweb_game_progress g_gp;
static struct raweb_subset g_gp_subsets[16];
static int g_gp_subset_count;
static unsigned g_gp_base_id;
static char g_gp_base_title[96];
static struct raweb_achievement g_gp_ach[600];
static int g_gp_count;
static unsigned g_gp_id;
static time_t g_gp_at;

/* Median unlock times, the order players take a set in --
   CLIENT.md calls it the honest sort for "what to chase next". */
static unsigned g_gp_med_ids[600], g_gp_med_secs[600];
static int g_gp_med_count;

static int build_profile(char *buf, size_t size)
{
    char *p = buf, *end = buf + size;

    if (time(NULL) - g_me_at > LIBRARY_TTL) {
        raweb_profile(&g_me);
        g_me_at = time(NULL);
    }

    p += snprintf(p, (size_t)(end - p), "{");
    json_field(&p, end, "user", g_me.user);
    p += snprintf(p, (size_t)(end - p), ",");
    json_field(&p, end, "motto", g_me.motto);
    p += snprintf(p, (size_t)(end - p), ",");
    json_field(&p, end, "presence", g_me.rich_presence);
    p += snprintf(p, (size_t)(end - p), ",\"points\":%u,\"softcore\":%u,\"rank\":%u,\"ranked\":%u}",
                  g_me.points, g_me.softcore_points, g_me.rank, g_me.total_ranked);
    return (int)(p - buf);
}

static int build_library(char *buf, size_t size)
{
    char *p = buf, *end = buf + size;
    int i;

    if (g_lib_count == 0 || time(NULL) - g_lib_at > LIBRARY_TTL) {
        g_lib_count = raweb_completion_progress(g_lib, (int)(sizeof(g_lib) / sizeof(g_lib[0])));
        g_lib_at = time(NULL);
    }

    p += snprintf(p, (size_t)(end - p), "{\"games\":[");
    for (i = 0; i < g_lib_count && end - p > 512; i++) {
        if (i > 0)
            p += snprintf(p, (size_t)(end - p), ",");
        p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", g_lib[i].id);
        json_field(&p, end, "title", g_lib[i].title);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "console", g_lib[i].console);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "icon", g_lib[i].icon);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "award", g_lib[i].award);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "played", g_lib[i].last_played);
        p += snprintf(p, (size_t)(end - p),
                      ",\"total\":%u,\"awarded\":%u,\"hardcore\":%u,\"mastered\":%s}",
                      g_lib[i].achievements, g_lib[i].awarded, g_lib[i].awarded_hardcore,
                      g_lib[i].mastered ? "true" : "false");
    }
    p += snprintf(p, (size_t)(end - p), "]}");
    return (int)(p - buf);
}

/* After an unlock: the game, the shelf and the points all moved. */
static void web_cache_expire(void)
{
    g_gp_at = 0;
    g_lib_at = 0;
    g_me_at = 0;
}

static int build_game(char *buf, size_t size, unsigned id)
{
    char *p = buf, *end = buf + size;
    int i;

    if (id != g_gp_id || time(NULL) - g_gp_at > GAME_TTL) {
        g_gp_count = raweb_game_progress(id, &g_gp, g_gp_ach,
                                         (int)(sizeof(g_gp_ach) / sizeof(g_gp_ach[0])));
        g_gp_med_count = raweb_median_times(id, g_gp_med_ids, g_gp_med_secs,
                                            (int)(sizeof(g_gp_med_ids) / sizeof(g_gp_med_ids[0])));
        /* The family: the base game and its subsets, found by title in
           the console's list. The base game itself is one of them from
           the page's point of view. */
        raweb_base_title(g_gp.title, g_gp_base_title, sizeof(g_gp_base_title));
        g_gp_subset_count = raweb_game_subsets(g_gp.console_id, g_gp_base_title, &g_gp_base_id,
                                               g_gp_subsets,
                                               (int)(sizeof(g_gp_subsets) / sizeof(g_gp_subsets[0])));
        if (g_gp_base_id == 0 && g_gp.parent_id == 0)
            g_gp_base_id = id;
        if (g_gp_base_id == 0)
            g_gp_base_id = g_gp.parent_id;
        g_gp_id = id;
        g_gp_at = time(NULL);
    }

    p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", id);
    json_field(&p, end, "title", g_gp.title);
    p += snprintf(p, (size_t)(end - p), ",");
    json_field(&p, end, "console", g_gp.console);
    p += snprintf(p, (size_t)(end - p), ",");
    json_field(&p, end, "icon", g_gp.image_icon);
    p += snprintf(p, (size_t)(end - p), ",\"total\":%u,\"awarded\":%u,\"base\":{\"id\":%u,",
                  g_gp.achievements_total, g_gp.awarded, g_gp_base_id);
    json_field(&p, end, "title", g_gp_base_title);
    p += snprintf(p, (size_t)(end - p), "},\"subsets\":[");
    for (i = 0; i < g_gp_subset_count && end - p > 256; i++) {
        if (i > 0)
            p += snprintf(p, (size_t)(end - p), ",");
        p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", g_gp_subsets[i].id);
        json_field(&p, end, "title", g_gp_subsets[i].title);
        p += snprintf(p, (size_t)(end - p), ",\"achievements\":%u,\"points\":%u}",
                      g_gp_subsets[i].achievements, g_gp_subsets[i].points);
    }
    p += snprintf(p, (size_t)(end - p), "],\"achievements\":[");

    for (i = 0; i < g_gp_count && end - p > 512; i++) {
        if (i > 0)
            p += snprintf(p, (size_t)(end - p), ",");
        p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", g_gp_ach[i].id);
        json_field(&p, end, "title", g_gp_ach[i].title);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "description", g_gp_ach[i].description);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "badge", g_gp_ach[i].badge);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "type", g_gp_ach[i].type);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "earned", g_gp_ach[i].date_earned);
        {
            unsigned median = 0;
            int m;

            for (m = 0; m < g_gp_med_count; m++)
                if (g_gp_med_ids[m] == g_gp_ach[i].id) {
                    median = g_gp_med_secs[m];
                    break;
                }
            p += snprintf(p, (size_t)(end - p),
                          ",\"points\":%u,\"order\":%u,\"players\":%u,\"median\":%u}",
                          g_gp_ach[i].points, g_gp_ach[i].display_order,
                          g_gp_ach[i].num_awarded, median);
        }
    }
    p += snprintf(p, (size_t)(end - p), "]}");
    return (int)(p - buf);
}

static struct raweb_leaderboard g_lb[300];
static int g_lb_count;
static unsigned g_lb_id;
static time_t g_lb_at;

static int build_leaderboards(char *buf, size_t size, unsigned id)
{
    char *p = buf, *end = buf + size;
    int i;

    if (id != g_lb_id || time(NULL) - g_lb_at > GAME_TTL) {
        g_lb_count = raweb_game_leaderboards(id, g_lb, (int)(sizeof(g_lb) / sizeof(g_lb[0])));
        g_lb_id = id;
        g_lb_at = time(NULL);
    }

    p += snprintf(p, (size_t)(end - p), "{\"boards\":[");
    for (i = 0; i < g_lb_count && end - p > 512; i++) {
        if (i > 0)
            p += snprintf(p, (size_t)(end - p), ",");
        p += snprintf(p, (size_t)(end - p), "{\"id\":%u,", g_lb[i].id);
        json_field(&p, end, "title", g_lb[i].title);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "description", g_lb[i].description);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "format", g_lb[i].format);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "topUser", g_lb[i].top_user);
        p += snprintf(p, (size_t)(end - p), ",");
        json_field(&p, end, "topScore", g_lb[i].top_score);
        p += snprintf(p, (size_t)(end - p), ",\"lowerBetter\":%s}",
                      g_lb[i].lower_is_better ? "true" : "false");
    }
    p += snprintf(p, (size_t)(end - p), "]}");
    return (int)(p - buf);
}

/* ---- Settings posted from the page: small urlencoded forms to loopback.
   The password goes on to the RA server the way --user sent it. Nothing
   here is reachable off the machine. */

static void urldecode(char *s)
{
    char *o = s;

    for (; *s != '\0'; s++, o++) {
        if (*s == '+') {
            *o = ' ';
        } else if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            char hex[3] = { s[1], s[2], '\0' };

            *o = (char)strtol(hex, NULL, 16);
            s += 2;
        } else {
            *o = *s;
        }
    }
    *o = '\0';
}

/* One field of an urlencoded form body. Returns 1 when present. */
static int form_field(const char *body, const char *name, char *out, size_t size)
{
    size_t nlen = strlen(name);
    const char *p = body;

    out[0] = '\0';
    while (p != NULL && *p != '\0') {
        if (strncmp(p, name, nlen) == 0 && p[nlen] == '=') {
            const char *v = p + nlen + 1;
            const char *e = strchr(v, '&');
            size_t len = e != NULL ? (size_t)(e - v) : strlen(v);

            char encoded[2048];
            if (len >= sizeof(encoded)) return -1;
            memcpy(encoded, v, len); encoded[len] = 0;
            urldecode(encoded);
            if (strlen(encoded) >= size) {
                memset(encoded, 0, sizeof(encoded)); return -1;
            }
            strcpy(out, encoded);
            memset(encoded, 0, sizeof(encoded));
            return 1;
        }
        p = strchr(p, '&');
        if (p != NULL)
            p++;
    }
    return 0;
}

static void respond(sock_t c, const char *type, const char *body, int len);

/* Returns 1 when the request was one of the settings posts. */
static int serve_settings(sock_t c, const char *req, const char *body, rc_client_t *client)
{
    char out[192];
    int n;

    if (strncmp(req, "POST /apikey", 12) == 0) {
        char key[128], user[128] = "", token[256];

        form_field(body, "key", key, sizeof(key));
        if (key[0] == '\0') {
            n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"the key is empty\"}");
        } else if (config_save_apikey(key) != 0) {
            n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"could not save the key\"}");
        } else {
            /* The user endpoints need to know who is asking. */
            if (!config_load_credentials(user, sizeof(user), token, sizeof(token)) && g.user[0] != '\0')
                snprintf(user, sizeof(user), "%s", g.user);
            raweb_set_credentials(user, key);
            webui_set_webapi(1);
            follow_reset();
            /* The page refetches at once, past yesterday's cache. */
            g_me_at = 0;
            g_lib_at = 0;
            log_info("web api key saved from the page");
            n = snprintf(out, sizeof(out), "{\"ok\":true}");
        }
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /login", 11) == 0) {
        char user[128], password[256];

        form_field(body, "user", user, sizeof(user));
        form_field(body, "password", password, sizeof(password));

        if (client == NULL) {
            n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"not available in ui-only mode\"}");
        } else if (user[0] == '\0' || password[0] == '\0') {
            n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"enter both name and password\"}");
        } else {
            int rc = ra_login_with_password(client, user, password);

            if (rc == RC_OK) {
                const rc_client_user_t *me = rc_client_get_user_info(client);
                char key[128];

                if (config_save_credentials(user, me->token) != 0)
                    log_warn("could not save the login token; you will be asked again next time");
                webui_set_login(1, me->display_name != NULL ? me->display_name : user);
                if (config_load_apikey(key, sizeof(key))) {
                    raweb_set_credentials(user, key);
                    g_me_at = 0;
                    g_lib_at = 0;
                }
                n = snprintf(out, sizeof(out), "{\"ok\":true}");
            } else if (rc == RC_INVALID_CREDENTIALS || rc == RC_EXPIRED_TOKEN || rc == RC_ACCESS_DENIED) {
                n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"wrong name or password\"}");
            } else {
                n = snprintf(out, sizeof(out),
                             "{\"ok\":false,\"error\":\"the RetroAchievements server could not be reached\"}");
            }
        }
        memset(password, 0, sizeof(password));
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /lan", 9) == 0) {
        char on[8] = "";

        form_field(body, "on", on, sizeof(on));
        g_lan = 0; /* Account controls stay on loopback in PS2-Servers. */
        if (config_save_lan(g_lan) != 0)
            log_warn("could not save the network setting; it holds until exit");
        g_rebind = 1;
        log_info(g_lan ? "interface opened to the network from the page"
                       : "interface closed to the network from the page");
        n = snprintf(out, sizeof(out), "{\"ok\":true}");
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /follow", 12) == 0) {
        char on[8] = "";

        form_field(body, "on", on, sizeof(on));
        follow_set_enabled(on[0] == '1');
        if (config_save_follow(on[0] == '1') != 0)
            log_warn("could not save the follow setting; it holds until exit");
        g.dirty = 1;
        log_info(on[0] == '1' ? "following turned on from the page" : "following turned off from the page");
        n = snprintf(out, sizeof(out), "{\"ok\":true}");
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /reset", 11) == 0) {
        int sent = console_send_reset();

        n = snprintf(out, sizeof(out), "{\"ok\":%s}", sent ? "true" : "false");
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /quit", 10) == 0) {
        g_quit = 1;
        log_info("quit requested from the page");
        n = snprintf(out, sizeof(out), "{\"ok\":true}");
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    if (strncmp(req, "POST /logout", 12) == 0) {
        if (client != NULL)
            rc_client_logout(client);
        config_forget_credentials();
        webui_set_login(0, "");
        log_info("signed out from the page");
        n = snprintf(out, sizeof(out), "{\"ok\":true}");
        respond(c, "application/json; charset=utf-8", out, n);
        return 1;
    }

    return 0;
}

/* ---- One request -------------------------------------------------------- */

static void send_all(sock_t c, const char *data, int len)
{
    int sent = 0;

    while (sent < len) {
        int n = (int)send(c, data + sent, len - sent, 0);

        if (n <= 0)
            return;
        sent += n;
    }
}

static void respond(sock_t c, const char *type, const char *body, int len)
{
    char head[256];
    int n;

    n = snprintf(head, sizeof(head),
                 "HTTP/1.1 200 OK\r\n"
                 "Content-Type: %s\r\n"
                 "Content-Length: %d\r\n"
                 "Cache-Control: no-store\r\n"
                 "Connection: close\r\n\r\n",
                 type, len);
    send_all(c, head, n);
    send_all(c, body, len);
}

static int request_header(const char *request, const char *name, char *out, size_t size)
{
    const char *line = strstr(request, "\r\n"); size_t n = strlen(name);
    out[0] = 0;
    while (line && line[2] && line[2] != '\r') {
        const char *start = line + 2, *end = strstr(start, "\r\n"); size_t i;
        if (!end) return 0;
        for (i = 0; i < n && start+i < end; i++)
            if (tolower((unsigned char)start[i]) != tolower((unsigned char)name[i])) break;
        if (i == n && start+n < end && start[n] == ':') {
            start += n+1; while (start < end && (*start == ' ' || *start == '\t')) start++;
            if ((size_t)(end-start) >= size) return -1;
            memcpy(out, start, (size_t)(end-start)); out[end-start] = 0; return 1;
        }
        line = end;
    }
    return 0;
}
static int local_request(const char *request)
{
    char host[128], origin[160], site[32], expected[128]; int have_origin;
    if (request_header(request, "Host", host, sizeof(host)) != 1) return 0;
    snprintf(expected, sizeof(expected), "127.0.0.1:%d", g_port);
    if (strcmp(host, expected)) return 0;
    have_origin = request_header(request, "Origin", origin, sizeof(origin));
    if (have_origin < 0) return 0;
    if (have_origin) {
        snprintf(expected, sizeof(expected), "http://127.0.0.1:%d", g_port);
        if (strcmp(origin, expected)) return 0;
    }
    if (request_header(request, "Sec-Fetch-Site", site, sizeof(site)) < 0 || !strcmp(site, "cross-site")) return 0;
    return 1;
}

void webui_serve(sock_t listener, rc_client_t *client)
{
    struct sockaddr_in from;
    socklen_t fromlen = sizeof(from);
    char req[2048];
    sock_t c;
    int n = 0;

    c = accept(listener, (struct sockaddr *)&from, &fromlen);
    if (c == SOCK_INVALID)
        return;
    g_page_contact = time(NULL);
    g_page_seen = 1;

    /* One request: headers, plus the body of a settings POST, which may
       need a second read. Anything larger than this buffer is cut short and
       falls through to "not found". Browsers open spare connections and
       send nothing on them; a blocking recv there held the whole client,
       telemetry included, until the browser gave up. Two seconds is
       plenty for a request that is coming. */
    for (;;) {
        char *hdr_end;
        int r;

        if (platform_wait_readable(c, 2000) != 1)
            break;
        r = (int)recv(c, req + n, (int)sizeof(req) - 1 - n, 0);
        if (r <= 0)
            break;
        n += r;
        req[n] = '\0';

        hdr_end = strstr(req, "\r\n\r\n");
        if (hdr_end == NULL) {
            if (n >= (int)sizeof(req) - 1)
                break;
            continue;
        }
        if (strncmp(req, "POST ", 5) != 0)
            break;
        {
            const char *cl = strstr(req, "Content-Length:");
            int want = cl != NULL ? atoi(cl + 15) : 0;
            int have = n - (int)(hdr_end + 4 - req);

            if (have >= want || n >= (int)sizeof(req) - 1)
                break;
        }
    }
    /* Every request in the log at detail level, method and path. */
    if (n > 0) {
        char line[96];
        const char *sp = strchr(req, ' ');
        size_t len = sp != NULL ? strcspn(sp + 1, " \r\n") : 0;

        if (len > sizeof(line) - 1)
            len = sizeof(line) - 1;
        memcpy(line, sp != NULL ? sp + 1 : "?", len);
        line[len] = '\0';
        log_detail("http %.*s %s", (int)(sp != NULL ? sp - req : 1), req, line);
    }
    if (n <= 0) {
        sock_close(c);
        return;
    }

    if (!local_request(req)) {
        const char forbidden[] = "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        send_all(c, forbidden, sizeof(forbidden)-1); sock_close(c); return;
    }

    if (strncmp(req, "POST ", 5) == 0) {
        char *body = strstr(req, "\r\n\r\n");

        /* Another device may watch; only this machine changes the
           settings or turns the client off. */
        if (from.sin_addr.s_addr != htonl(INADDR_LOOPBACK)) {
            const char *fb = "{\"ok\":false,\"error\":\"settings change only on the PC running xerabora\"}";
            char head[160];
            int hn = snprintf(head, sizeof(head),
                              "HTTP/1.1 403 Forbidden\r\nContent-Type: application/json; charset=utf-8\r\n"
                              "Content-Length: %d\r\nConnection: close\r\n\r\n", (int)strlen(fb));

            send_all(c, head, hn);
            send_all(c, fb, (int)strlen(fb));
            sock_close(c);
            return;
        }
        if (body == NULL || !serve_settings(c, req, body + 4, client)) {
            const char *nf = "not found";

            send_all(c, "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\n", 63);
            send_all(c, nf, 9);
        }
        sock_close(c);
        return;
    }

    if (strncmp(req, "GET /events", 11) == 0) {
        /* The stream stays open; everything else about this connection
           is done here. */
        const char *head =
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: keep-alive\r\n\r\n";

        send_all(c, head, (int)strlen(head));
        stream_add(c);
        g.dirty = 1; /* a fresh page needs the full layout first */
        webui_push(client);
        return; /* deliberately not closed */
    }

    if (strncmp(req, "GET /profile", 12) == 0 ||
        strncmp(req, "GET /library", 12) == 0 ||
        strncmp(req, "GET /boards", 11) == 0 ||
        strncmp(req, "GET /game", 9) == 0) {
        char *body = malloc(512 * 1024);

        if (body != NULL) {
            int len;

            if (strncmp(req, "GET /profile", 12) == 0) {
                len = build_profile(body, 512 * 1024);
            } else if (strncmp(req, "GET /library", 12) == 0) {
                len = build_library(body, 512 * 1024);
            } else if (strncmp(req, "GET /boards", 11) == 0) {
                const char *q = strstr(req, "id=");

                len = build_leaderboards(body, 512 * 1024,
                                         q != NULL ? (unsigned)strtoul(q + 3, NULL, 10) : 0);
            } else {
                const char *q = strstr(req, "id=");

                len = build_game(body, 512 * 1024,
                                 q != NULL ? (unsigned)strtoul(q + 3, NULL, 10) : 0);
            }

            respond(c, "application/json; charset=utf-8", body, len);
            free(body);
        }
    } else if (strncmp(req, "GET /state", 10) == 0) {
        char *body = malloc(256 * 1024);

        if (body != NULL) {
            int len = build_state(body, 256 * 1024, client);

            respond(c, "application/json; charset=utf-8", body, len);
            free(body);
        }
    /* "/?lang=es" is still the page, not a miss. */
    } else if (strncmp(req, "GET / ", 6) == 0 || strncmp(req, "GET /?", 6) == 0 ||
               strncmp(req, "GET /index", 10) == 0) {
        /* --ui-file: the page straight from disk, for working on it.
           Edit, refresh, see -- no re-embed, no rebuild. The shipped
           client never sets this and serves the built-in copy. */
        char *page = NULL;

        if (g_ui_file[0] != '\0') {
            FILE *f = fopen(g_ui_file, "rb");

            if (f != NULL) {
                long sz;

                fseek(f, 0, SEEK_END);
                sz = ftell(f);
                fseek(f, 0, SEEK_SET);
                if (sz > 0 && sz < 4 * 1024 * 1024) {
                    page = malloc((size_t)sz + 1);
                    if (page != NULL) {
                        if ((long)fread(page, 1, (size_t)sz, f) == sz) {
                            page[sz] = '\0';
                        } else {
                            free(page);
                            page = NULL;
                        }
                    }
                }
                fclose(f);
            }
            if (page == NULL)
                log_warn("could not read %s; serving the built-in page", g_ui_file);
        }

        if (page != NULL) {
            respond(c, "text/html; charset=utf-8", page, (int)strlen(page));
            free(page);
        } else {
            respond(c, "text/html; charset=utf-8", ui_page_html, (int)strlen(ui_page_html));
        }
    } else {
        const char *nf = "not found";

        send_all(c, "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\n", 63);
        send_all(c, nf, 9);
    }

    sock_close(c);
}

/* Server-sent events: one long-lived response written whenever the state
   moves; the browser reconnects by itself. Once per snapshot while a game
   runs. */
void webui_push(rc_client_t *client)
{
    static time_t last_full;
    char *body;
    char head[64];
    int len, n;
    time_t now;

    if (stream_count() == 0)
        return;

    body = malloc(256 * 1024);
    if (body == NULL)
        return;

    /* Full layout once a second and on anything structural; between
       those, one small delta per snapshot with just the moving
       numbers. */
    now = time(NULL);
    if (g.dirty || now - last_full >= 1) {
        len = build_state(body, 256 * 1024, client);
        last_full = now;
        g.dirty = 0;
    } else {
        len = build_delta(body, 256 * 1024, client);
    }
    n = snprintf(head, sizeof(head), "data: ");

    /* A dead page shows up as a failed write; drop it and let the
       browser reconnect on its own. */
    {
        int i;

        for (i = 0; i < WEBUI_STREAMS; i++) {
            sock_t s = g_streams[i];

            if (s == SOCK_INVALID)
                continue;
            if (send(s, head, n, 0) <= 0 ||
                send(s, body, len, 0) <= 0 ||
                send(s, "\n\n", 2, 0) <= 0) {
                sock_close(s);
                g_streams[i] = SOCK_INVALID;
            } else {
                g_stream_sent++;
                g_page_contact = time(NULL);
            }
        }
    }

    free(body);
}

/* ---- Stream output ------------------------------------------------------ */

static char g_obs_dir[512];

void webui_set_obs_dir(const char *dir)
{
    snprintf(g_obs_dir, sizeof(g_obs_dir), "%s", dir != NULL ? dir : "");
}

static void obs_file(const char *name, const char *fmt, ...)
{
    char path[600];
    va_list ap;
    FILE *f;

#ifdef _WIN32
    snprintf(path, sizeof(path), "%s\\%s", g_obs_dir, name);
#else
    snprintf(path, sizeof(path), "%s/%s", g_obs_dir, name);
#endif

    f = fopen(path, "w");
    if (f == NULL)
        return;

    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fclose(f);
}

void webui_write_obs(rc_client_t *client)
{
    char path[600];
    char *body;
    FILE *f;
    unsigned total = 0, done = 0;

    if (g_obs_dir[0] == '\0')
        return;

    if (client != NULL) {
        rc_client_user_game_summary_t sum;

        rc_client_get_user_game_summary(client, &sum);
        total = sum.num_core_achievements;
        done = sum.num_unlocked_achievements;
    }

    /* One value per file: that is what an OBS text source wants, and it
       lets a layout pick exactly the pieces it needs. */
    obs_file("game.txt", "%s", g.title[0] != '\0' ? g.title : g.serial);
    obs_file("progress.txt", "%u / %u", done, total);
    obs_file("percent.txt", "%u%%", total > 0 ? done * 100 / total : 0);
    obs_file("last-unlock.txt", "%s", g.unlock_count > 0 ? g.unlocks[0].title : "");
    obs_file("last-points.txt", "%s", g.unlock_count > 0 ? "" : "");
    if (g.unlock_count > 0)
        obs_file("last-points.txt", "+%u", g.unlocks[0].points);
    obs_file("session.txt", "%ld min", session_seconds() / 60);
    obs_file("console.txt", "%s", g.connected ? "connected" : "waiting");

    /* And the whole state, for a layout that would rather do its own
       arranging -- the same JSON the page gets. */
    body = malloc(256 * 1024);
    if (body == NULL)
        return;
#ifdef _WIN32
    snprintf(path, sizeof(path), "%s\\data.json", g_obs_dir);
#else
    snprintf(path, sizeof(path), "%s/data.json", g_obs_dir);
#endif
    f = fopen(path, "w");
    if (f != NULL) {
        int len = build_state(body, 256 * 1024, client);

        fwrite(body, 1, (size_t)len, f);
        fclose(f);
    }
    free(body);
}

void webui_open_browser(int port)
{
    if (getenv("PS2SERVERS_RA_NO_BROWSER")) return;
    char url[64];

    snprintf(url, sizeof(url), "http://127.0.0.1:%d/", port);

#ifdef _WIN32
    {
        char args[128];

        /* ShellExecute, not system("start"): no cmd.exe in the middle and
           no console flashing past. Application mode (Edge, ships with
           Windows) first; otherwise the default browser. */
        snprintf(args, sizeof(args), "--app=%s --window-size=580,800", url);
        if ((INT_PTR)ShellExecuteA(NULL, "open", "msedge.exe", args, NULL, SW_SHOWNORMAL) <= 32)
            ShellExecuteA(NULL, "open", url, NULL, NULL, SW_SHOWNORMAL);
    }
#elif defined(__APPLE__)
    {
        char cmd[512];

        /* open -n gives a window of its own even when Chrome already runs;
           without Chrome the URL goes to the default browser, which on
           Safari means a plain tab, not application mode. */
        snprintf(cmd, sizeof(cmd),
                 "(open -n -a 'Google Chrome' --args --app=%s --window-size=580,800 || open %s) >/dev/null 2>&1 &",
                 url, url);
        if (system(cmd) != 0)
            log_info("open %s in a browser", url);
    }
#else
    {
        char cmd[512];

        snprintf(cmd, sizeof(cmd),
                 "(google-chrome --app=%s --window-size=580,800 || chromium --app=%s || xdg-open %s) >/dev/null 2>&1 &",
                 url, url, url);
        if (system(cmd) != 0)
            log_info("open %s in a browser", url);
    }
#endif

    log_info("interface at %s", url);
}
