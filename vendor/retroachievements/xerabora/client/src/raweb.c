#include "raweb.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "http.h"
#include "log.h"
#include "version.h"

#define JSMN_STATIC
#define JSMN_PARENT_LINKS
#include "jsmn.h"

#define RAWEB_HOST "https://retroachievements.org/API/"
#define RAWEB_MAX_TOKENS 65536

static char g_user[64];
static char g_key[64];

void raweb_set_credentials(const char *user, const char *api_key)
{
    snprintf(g_user, sizeof(g_user), "%s", user != NULL ? user : "");
    snprintf(g_key, sizeof(g_key), "%s", api_key != NULL ? api_key : "");
}

int raweb_have_credentials(void)
{
    return g_user[0] != '\0' && g_key[0] != '\0';
}

/* ---- JSON: jsmn is a tokenizer, a flat token array with parent links and
   no allocation. Everything below walks that array. */

struct json
{
    char *text;
    jsmntok_t *tok;
    int count;
};

static void json_free(struct json *j)
{
    free(j->text);
    free(j->tok);
    j->text = NULL;
    j->tok = NULL;
    j->count = 0;
}

/* GET endpoint + query and parse the body; the caller json_free()s the
   result. z is the requesting user, y the key; user endpoints also want u
   (the same name here) or answer 422. */
static int json_get(struct json *j, const char *endpoint, const char *query)
{
    char url[1024], agent[64];
    struct http_response res;
    jsmn_parser p;
    int n;

    memset(j, 0, sizeof(*j));

    if (!raweb_have_credentials())
        return 0;

    snprintf(url, sizeof(url), RAWEB_HOST "%s?z=%s&y=%s%s%s",
             endpoint, g_user, g_key, query != NULL ? "&" : "", query != NULL ? query : "");
    snprintf(agent, sizeof(agent), "%s/%s", XERABORA_NAME, XERABORA_VERSION);

    memset(&res, 0, sizeof(res));
    if (http_request(url, NULL, NULL, agent, &res) != 0) {
        log_warn("web api: %s could not be reached", endpoint);
        return 0;
    }
    if (res.status != 200 || res.body == NULL) {
        log_warn("web api: %s answered %d", endpoint, res.status);
        free(res.body);
        return 0;
    }

    j->text = res.body;
    j->tok = malloc(sizeof(jsmntok_t) * RAWEB_MAX_TOKENS);
    if (j->tok == NULL) {
        json_free(j);
        return 0;
    }

    jsmn_init(&p);
    n = jsmn_parse(&p, j->text, res.length, j->tok, RAWEB_MAX_TOKENS);
    if (n < 1) {
        log_warn("web api: %s returned something that is not JSON", endpoint);
        json_free(j);
        return 0;
    }
    j->count = n;
    return 1;
}

/* The same, for endpoints that ask about a user. */
static int json_get_user(struct json *j, const char *endpoint, const char *query)
{
    char q[256];

    snprintf(q, sizeof(q), "u=%s%s%s", g_user,
             query != NULL ? "&" : "", query != NULL ? query : "");
    return json_get(j, endpoint, q);
}

static int tok_is(const struct json *j, int t, const char *s)
{
    int len = j->tok[t].end - j->tok[t].start;

    return j->tok[t].type == JSMN_STRING &&
           (int)strlen(s) == len &&
           strncmp(j->text + j->tok[t].start, s, (size_t)len) == 0;
}

/* Index of the value token for `key` in the object at `obj`, or -1. A
   value's parent is its key, whose parent is the object. */
static int obj_get(const struct json *j, int obj, const char *key)
{
    int i;

    if (obj < 0 || obj >= j->count || j->tok[obj].type != JSMN_OBJECT)
        return -1;

    for (i = obj + 1; i < j->count; i++) {
        if (j->tok[i].parent == obj && tok_is(j, i, key))
            return (i + 1 < j->count) ? i + 1 : -1;
        /* Left the object entirely. */
        if (j->tok[i].parent < obj)
            break;
    }
    return -1;
}

/* Four hex digits to a code point, or -1. */
static int hex4(const char *p)
{
    int v = 0, i;

    for (i = 0; i < 4; i++) {
        int c = (unsigned char)p[i];

        if (c >= '0' && c <= '9')
            c -= '0';
        else if (c >= 'a' && c <= 'f')
            c = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F')
            c = c - 'A' + 10;
        else
            return -1;
        v = v * 16 + c;
    }
    return v;
}

/* Copies a token out undoing JSON escapes: the API writes "NES\/Famicom"
   and \uXXXX for non-ASCII. Surrogate pairs decode; a lone one becomes
   U+FFFD. */
static void tok_copy(const struct json *j, int t, char *out, size_t size)
{
    const char *src;
    size_t o = 0;
    int len, i;

    out[0] = '\0';
    if (t < 0 || t >= j->count || size == 0)
        return;

    len = j->tok[t].end - j->tok[t].start;
    if (len < 0)
        return;
    src = j->text + j->tok[t].start;

    for (i = 0; i < len && o + 1 < size; i++) {
        unsigned cp;

        if (src[i] != '\\' || i + 1 >= len) {
            out[o++] = src[i];
            continue;
        }

        i++;
        switch (src[i]) {
        case 'n': out[o++] = '\n'; continue;
        case 't': out[o++] = '\t'; continue;
        case 'r': out[o++] = '\r'; continue;
        case 'b': out[o++] = '\b'; continue;
        case 'f': out[o++] = '\f'; continue;
        case 'u': break;
        default:  out[o++] = src[i]; continue; /* \" \\ \/ and the rest */
        }

        if (i + 4 >= len || hex4(&src[i + 1]) < 0) {
            out[o++] = 'u';
            continue;
        }
        cp = (unsigned)hex4(&src[i + 1]);
        i += 4;

        /* A high surrogate takes the low one that follows it. */
        if (cp >= 0xD800 && cp <= 0xDBFF) {
            if (i + 6 < len && src[i + 1] == '\\' && src[i + 2] == 'u' && hex4(&src[i + 3]) >= 0) {
                unsigned lo = (unsigned)hex4(&src[i + 3]);

                if (lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    i += 6;
                } else {
                    cp = 0xFFFD;
                }
            } else {
                cp = 0xFFFD;
            }
        } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            cp = 0xFFFD;
        }

        if (cp < 0x80) {
            out[o++] = (char)cp;
        } else if (cp < 0x800) {
            if (o + 2 >= size)
                break;
            out[o++] = (char)(0xC0 | (cp >> 6));
            out[o++] = (char)(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            if (o + 3 >= size)
                break;
            out[o++] = (char)(0xE0 | (cp >> 12));
            out[o++] = (char)(0x80 | ((cp >> 6) & 0x3F));
            out[o++] = (char)(0x80 | (cp & 0x3F));
        } else {
            if (o + 4 >= size)
                break;
            out[o++] = (char)(0xF0 | (cp >> 18));
            out[o++] = (char)(0x80 | ((cp >> 12) & 0x3F));
            out[o++] = (char)(0x80 | ((cp >> 6) & 0x3F));
            out[o++] = (char)(0x80 | (cp & 0x3F));
        }
    }
    out[o] = '\0';

    /* JSON null reads as the empty string: the API uses it for "never
       played", "not earned" and friends, and every caller wants that
       to be nothing rather than the word "null". */
    if (strcmp(out, "null") == 0)
        out[0] = '\0';
}

static void obj_str(const struct json *j, int obj, const char *key, char *out, size_t size)
{
    tok_copy(j, obj_get(j, obj, key), out, size);
}

static unsigned obj_uint(const struct json *j, int obj, const char *key)
{
    char buf[32];

    tok_copy(j, obj_get(j, obj, key), buf, sizeof(buf));
    if (buf[0] == '\0')
        return 0;
    return (unsigned)strtoul(buf, NULL, 10);
}

/* Next element of the array at `arr` after token `from`, or -1. */
static int arr_next(const struct json *j, int arr, int from)
{
    int i;

    for (i = from; i < j->count; i++)
        if (j->tok[i].parent == arr)
            return i;
    return -1;
}

/* ---- The calls ---------------------------------------------------------- */

int raweb_profile(struct raweb_profile *out)
{
    struct json j;

    memset(out, 0, sizeof(*out));
    if (!json_get_user(&j, "API_GetUserProfile.php", NULL))
        return 0;

    obj_str(&j, 0, "User", out->user, sizeof(out->user));
    obj_str(&j, 0, "Motto", out->motto, sizeof(out->motto));
    obj_str(&j, 0, "RichPresenceMsg", out->rich_presence, sizeof(out->rich_presence));
    out->last_game_id = obj_uint(&j, 0, "LastGameID");
    out->points = obj_uint(&j, 0, "TotalPoints");
    out->softcore_points = obj_uint(&j, 0, "TotalSoftcorePoints");
    out->rank = obj_uint(&j, 0, "Rank");
    out->total_ranked = obj_uint(&j, 0, "TotalRanked");

    json_free(&j);
    return out->user[0] != '\0';
}

int raweb_summary(struct raweb_summary *out)
{
    struct json j;
    char status[16] = "";
    int game;

    memset(out, 0, sizeof(*out));
    if (!json_get_user(&j, "API_GetUserSummary.php", "g=0&a=0"))
        return 0;

    obj_str(&j, 0, "Status", status, sizeof(status));
    out->online = strcmp(status, "Online") == 0;
    obj_str(&j, 0, "RichPresenceMsgDate", out->rich_presence_date, sizeof(out->rich_presence_date));
    out->last_game_id = obj_uint(&j, 0, "LastGameID");
    game = obj_get(&j, 0, "LastGame");
    if (game >= 0)
        obj_str(&j, game, "ConsoleName", out->last_game_console, sizeof(out->last_game_console));

    json_free(&j);
    return 1;
}

/* Both library calls answer with rows of the same shape; only the
   wrapper differs, so one reader serves both. */
static int read_games(struct json *j, int arr, struct raweb_game *rows, int max)
{
    int e = arr_next(j, arr, arr + 1), n = 0;

    while (e >= 0 && n < max) {
        struct raweb_game *g = &rows[n];
        char award[32];

        memset(g, 0, sizeof(*g));
        g->id = obj_uint(j, e, "GameID");
        obj_str(j, e, "Title", g->title, sizeof(g->title));
        obj_str(j, e, "ConsoleName", g->console, sizeof(g->console));
        g->console_id = obj_uint(j, e, "ConsoleID");
        g->achievements = obj_uint(j, e, "MaxPossible");
        if (g->achievements == 0)
            g->achievements = obj_uint(j, e, "AchievementsTotal");
        if (g->achievements == 0)
            g->achievements = obj_uint(j, e, "NumAchievements");
        g->awarded = obj_uint(j, e, "NumAwarded");
        if (g->awarded == 0)
            g->awarded = obj_uint(j, e, "NumAchieved");
        g->awarded_hardcore = obj_uint(j, e, "NumAwardedHardcore");
        if (g->awarded_hardcore == 0)
            g->awarded_hardcore = obj_uint(j, e, "NumAchievedHardcore");
        obj_str(j, e, "LastPlayed", g->last_played, sizeof(g->last_played));
        obj_str(j, e, "ImageIcon", g->icon, sizeof(g->icon));
        obj_str(j, e, "HighestAwardKind", g->award, sizeof(g->award));
        snprintf(award, sizeof(award), "%s", g->award);
        g->mastered = (strstr(award, "master") != NULL || strstr(award, "complet") != NULL);

        n++;
        e = arr_next(j, arr, j->tok[e].end > 0 ? e + 1 : e + 1);
        /* arr_next scans forward by index; skipping to the next token
           whose parent is the array lands on the next element. */
        while (e >= 0 && j->tok[e].parent != arr)
            e = arr_next(j, arr, e + 1);
    }
    return n;
}

int raweb_completion_progress(struct raweb_game *rows, int max)
{
    struct json j;
    int arr, n;

    if (!json_get_user(&j, "API_GetUserCompletionProgress.php", "c=500"))
        return 0;

    arr = obj_get(&j, 0, "Results");
    if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }
    n = read_games(&j, arr, rows, max);
    json_free(&j);
    return n;
}

int raweb_recently_played(struct raweb_game *rows, int max)
{
    struct json j;
    int n;

    if (!json_get_user(&j, "API_GetUserRecentlyPlayedGames.php", "c=10"))
        return 0;

    /* This one answers with a bare array. */
    if (j.tok[0].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }
    n = read_games(&j, 0, rows, max);
    json_free(&j);
    return n;
}

int raweb_game_progress(unsigned game_id, struct raweb_game_progress *info,
                        struct raweb_achievement *rows, int max)
{
    struct json j;
    char query[64];
    int obj, e, n = 0;

    memset(info, 0, sizeof(*info));
    snprintf(query, sizeof(query), "g=%u", game_id);
    if (!json_get_user(&j, "API_GetGameInfoAndUserProgress.php", query))
        return 0;

    info->id = game_id;
    obj_str(&j, 0, "Title", info->title, sizeof(info->title));
    obj_str(&j, 0, "ConsoleName", info->console, sizeof(info->console));
    info->console_id = obj_uint(&j, 0, "ConsoleID");
    info->parent_id = obj_uint(&j, 0, "ParentGameID");
    obj_str(&j, 0, "ImageIcon", info->image_icon, sizeof(info->image_icon));
    info->achievements_total = obj_uint(&j, 0, "NumAchievements");
    info->awarded = obj_uint(&j, 0, "NumAwardedToUser");
    info->awarded_hardcore = obj_uint(&j, 0, "NumAwardedToUserHardcore");
    info->points_total = obj_uint(&j, 0, "points_total");
    info->points_earned = obj_uint(&j, 0, "UserCompletion") /* percentage, not points */ * 0;

    /* "Achievements" is an object keyed by achievement id, not an
       array: every value token whose parent is that object is one
       achievement, and its key is the id. */
    obj = obj_get(&j, 0, "Achievements");
    if (obj < 0 || j.tok[obj].type != JSMN_OBJECT) {
        json_free(&j);
        return 0;
    }

    for (e = obj + 1; e < j.count && n < max; e++) {
        struct raweb_achievement *a;

        if (j.tok[e].parent != obj)
            continue;
        /* e is the key (the id); the value follows it. */
        if (e + 1 >= j.count || j.tok[e + 1].type != JSMN_OBJECT)
            continue;

        a = &rows[n];
        memset(a, 0, sizeof(*a));
        a->id = obj_uint(&j, e + 1, "ID");
        obj_str(&j, e + 1, "Title", a->title, sizeof(a->title));
        obj_str(&j, e + 1, "Description", a->description, sizeof(a->description));
        obj_str(&j, e + 1, "BadgeName", a->badge, sizeof(a->badge));
        obj_str(&j, e + 1, "type", a->type, sizeof(a->type));
        /* The one lowercase key in the row; guard against it ever
           joining the ID/Title/BadgeName capitalization around it. */
        if (a->type[0] == '\0')
            obj_str(&j, e + 1, "Type", a->type, sizeof(a->type));
        a->points = obj_uint(&j, e + 1, "Points");
        a->num_awarded = obj_uint(&j, e + 1, "NumAwarded");
        a->display_order = obj_uint(&j, e + 1, "DisplayOrder");
        obj_str(&j, e + 1, "DateEarned", a->date_earned, sizeof(a->date_earned));
        if (a->date_earned[0] == '\0') {
            obj_str(&j, e + 1, "DateEarnedHardcore", a->date_earned, sizeof(a->date_earned));
            a->earned_hardcore = a->date_earned[0] != '\0';
        }
        n++;
    }

    json_free(&j);
    return n;
}

/* ---- Subsets ------------------------------------------------------------ */

#define SUBSET_MARK " [Subset - "
#define GAME_LIST_TTL 3600

static struct json g_list;
static unsigned g_list_console;
static time_t g_list_at;
static time_t g_list_failed_at;   /* a list that would not come: retry in a while */
#define GAME_LIST_RETRY 300

void raweb_base_title(const char *title, char *out, size_t size)
{
    const char *cut = strstr(title, SUBSET_MARK);
    size_t n = cut != NULL ? (size_t)(cut - title) : strlen(title);

    if (n >= size)
        n = size - 1;
    memcpy(out, title, n);
    out[n] = '\0';
}

/* The console's game list, fetched once an hour. ~1000 sets for the
   PS2, well inside the token budget without hashes. */
static int game_list(unsigned console_id)
{
    char query[64];

    if (g_list.count > 0 && g_list_console == console_id &&
        time(NULL) - g_list_at < GAME_LIST_TTL)
        return 1;
    if (g_list_failed_at != 0 && g_list_console == console_id &&
        time(NULL) - g_list_failed_at < GAME_LIST_RETRY)
        return 0;

    json_free(&g_list);
    g_list_console = console_id;
    snprintf(query, sizeof(query), "i=%u&f=1", console_id);
    if (!json_get(&g_list, "API_GetGameList.php", query)) {
        g_list_failed_at = time(NULL);
        return 0;
    }
    g_list_failed_at = 0;
    g_list_at = time(NULL);
    return 1;
}

int raweb_console_games(unsigned console_id, struct raweb_game *rows, int max)
{
    int e, n = 0;

    if (console_id == 0 || !game_list(console_id))
        return 0;
    if (g_list.tok[0].type != JSMN_ARRAY)
        return 0;

    for (e = 1; e < g_list.count && n < max; e++) {
        if (g_list.tok[e].parent != 0 || g_list.tok[e].type != JSMN_OBJECT)
            continue;
        memset(&rows[n], 0, sizeof(rows[n]));
        rows[n].id = obj_uint(&g_list, e, "ID");
        obj_str(&g_list, e, "Title", rows[n].title, sizeof(rows[n].title));
        rows[n].achievements = obj_uint(&g_list, e, "NumAchievements");
        rows[n].console_id = console_id;
        if (rows[n].id != 0)
            n++;
    }
    return n;
}

int raweb_game_subsets(unsigned console_id, const char *base_title,
                       unsigned *base_id, struct raweb_subset *rows, int max)
{
    char prefix[128], title[160];
    size_t plen;
    int e, n = 0;

    *base_id = 0;
    if (console_id == 0 || base_title[0] == '\0' || !game_list(console_id))
        return 0;

    snprintf(prefix, sizeof(prefix), "%s" SUBSET_MARK, base_title);
    plen = strlen(prefix);

    if (g_list.tok[0].type != JSMN_ARRAY)
        return 0;
    for (e = 1; e < g_list.count; e++) {
        if (g_list.tok[e].parent != 0 || g_list.tok[e].type != JSMN_OBJECT)
            continue;
        obj_str(&g_list, e, "Title", title, sizeof(title));
        if (strcmp(title, base_title) == 0) {
            *base_id = obj_uint(&g_list, e, "ID");
            continue;
        }
        if (n >= max || strncmp(title, prefix, plen) != 0)
            continue;
        rows[n].id = obj_uint(&g_list, e, "ID");
        snprintf(rows[n].title, sizeof(rows[n].title), "%s", title + plen);
        /* drop the closing bracket */
        if (rows[n].title[0] != '\0' && rows[n].title[strlen(rows[n].title) - 1] == ']')
            rows[n].title[strlen(rows[n].title) - 1] = '\0';
        rows[n].achievements = obj_uint(&g_list, e, "NumAchievements");
        rows[n].points = obj_uint(&g_list, e, "Points");
        n++;
    }
    return n;
}

int raweb_median_times(unsigned game_id, unsigned *ids, unsigned *seconds, int max)
{
    struct json j;
    char query[64];
    int arr, e, n = 0;

    snprintf(query, sizeof(query), "i=%u", game_id);
    if (!json_get(&j, "API_GetGameProgression.php", query))
        return 0;

    arr = obj_get(&j, 0, "Achievements");
    if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }

    for (e = arr + 1; e < j.count && n < max; e++) {
        if (j.tok[e].parent != arr || j.tok[e].type != JSMN_OBJECT)
            continue;
        ids[n] = obj_uint(&j, e, "ID");
        seconds[n] = obj_uint(&j, e, "MedianTimeToUnlock");
        n++;
    }

    json_free(&j);
    return n;
}

int raweb_game_leaderboards(unsigned game_id, struct raweb_leaderboard *rows, int max)
{
    struct json j;
    char query[64];
    int arr, e, n = 0;

    snprintf(query, sizeof(query), "i=%u", game_id);
    if (!json_get(&j, "API_GetGameLeaderboards.php", query))
        return 0;

    arr = obj_get(&j, 0, "Results");
    if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }

    for (e = arr + 1; e < j.count && n < max; e++) {
        struct raweb_leaderboard *lb = &rows[n];
        char rank_asc[16] = "";
        int top;

        if (j.tok[e].parent != arr || j.tok[e].type != JSMN_OBJECT)
            continue;

        memset(lb, 0, sizeof(*lb));
        lb->id = obj_uint(&j, e, "ID");
        obj_str(&j, e, "Title", lb->title, sizeof(lb->title));
        obj_str(&j, e, "Description", lb->description, sizeof(lb->description));
        obj_str(&j, e, "Format", lb->format, sizeof(lb->format));
        obj_str(&j, e, "RankAsc", rank_asc, sizeof(rank_asc));
        lb->lower_is_better = (strcmp(rank_asc, "true") == 0 || strcmp(rank_asc, "1") == 0);

        /* TopEntry is an object of its own, and absent on an empty
           board -- a new leaderboard nobody has entered yet. */
        top = obj_get(&j, e, "TopEntry");
        if (top >= 0 && j.tok[top].type == JSMN_OBJECT) {
            obj_str(&j, top, "User", lb->top_user, sizeof(lb->top_user));
            obj_str(&j, top, "FormattedScore", lb->top_score, sizeof(lb->top_score));
            if (lb->top_score[0] == '\0')
                obj_str(&j, top, "Score", lb->top_score, sizeof(lb->top_score));
        }
        n++;
    }

    json_free(&j);
    return n;
}

int raweb_recent_unlocks(int minutes, struct raweb_unlock *rows, int max)
{
    struct json j;
    char query[64];
    int e, n = 0;

    snprintf(query, sizeof(query), "m=%d", minutes > 0 ? minutes : 60 * 24 * 7);
    if (!json_get_user(&j, "API_GetUserRecentAchievements.php", query))
        return 0;

    /* A bare array, newest first. */
    if (j.tok[0].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }

    for (e = 1; e < j.count && n < max; e++) {
        struct raweb_unlock *u = &rows[n];
        char hc[8] = "";

        if (j.tok[e].parent != 0 || j.tok[e].type != JSMN_OBJECT)
            continue;

        memset(u, 0, sizeof(*u));
        u->achievement_id = obj_uint(&j, e, "AchievementID");
        u->game_id = obj_uint(&j, e, "GameID");
        obj_str(&j, e, "Title", u->title, sizeof(u->title));
        obj_str(&j, e, "GameTitle", u->game_title, sizeof(u->game_title));
        obj_str(&j, e, "BadgeName", u->badge, sizeof(u->badge));
        u->points = obj_uint(&j, e, "Points");
        obj_str(&j, e, "Date", u->date, sizeof(u->date));
        obj_str(&j, e, "HardcoreMode", hc, sizeof(hc));
        u->hardcore = (hc[0] == '1');
        n++;
    }

    json_free(&j);
    return n;
}

int raweb_game_hashes(unsigned game_id, char (*rows)[33], char (*names)[96], int max)
{
    struct json j;
    char query[64];
    int arr, e, n = 0;

    snprintf(query, sizeof(query), "i=%u", game_id);
    if (!json_get(&j, "API_GetGameHashes.php", query))
        return 0;

    arr = obj_get(&j, 0, "Results");
    if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
        json_free(&j);
        return 0;
    }

    for (e = arr + 1; e < j.count && n < max; e++) {
        if (j.tok[e].parent != arr || j.tok[e].type != JSMN_OBJECT)
            continue;
        obj_str(&j, e, "MD5", rows[n], 33);
        obj_str(&j, e, "Name", names[n], 96);
        n++;
    }

    json_free(&j);
    return n;
}
