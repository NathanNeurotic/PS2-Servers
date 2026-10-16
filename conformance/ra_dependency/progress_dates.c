/* Exercise the production Web API parser without network or real credentials. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "http.h"
#include "raweb.h"

#define SOFT "2026-01-01 00:00:00"
#define HARD "2026-01-02 00:00:00"

static const char response[] =
    "{\"Achievements\":{"
    "\"1\":{\"ID\":1,\"DateEarned\":\"" SOFT "\",\"DateEarnedHardcore\":\"" HARD "\"},"
    "\"2\":{\"ID\":2},"
    "\"3\":{\"ID\":3,\"DateEarned\":\"" SOFT "\"},"
    "\"4\":{\"ID\":4,\"DateEarnedHardcore\":\"" HARD "\"},"
    "\"5\":{\"ID\":5,\"DateEarned\":null,\"DateEarnedHardcore\":null},"
    "\"6\":{\"ID\":6,\"DateEarned\":\"\",\"DateEarnedHardcore\":\"\"},"
    "\"7\":{\"ID\":7,\"DateEarned\":null,\"DateEarnedHardcore\":\"" HARD "\"},"
    "\"8\":{\"ID\":8,\"DateEarned\":\"" SOFT "\",\"DateEarnedHardcore\":null}"
    "}}";

void log_warn(const char *format, ...) { (void)format; }

int http_request(const char *url, const char *post, const char *type,
                 const char *agent, struct http_response *out)
{
    (void)post; (void)type; (void)agent;
    if (strstr(url, "API_GetGameInfoAndUserProgress.php") == NULL) return -1;
    out->length = sizeof(response) - 1;
    out->body = malloc(sizeof(response));
    if (out->body == NULL) return -1;
    memcpy(out->body, response, sizeof(response));
    out->status = 200;
    return 0;
}

int main(void)
{
    struct raweb_game_progress game;
    struct raweb_achievement rows[8];
    const int expected_hardcore[] = {1, 0, 0, 1, 0, 0, 1, 0};
    const char *expected_dates[] = {SOFT, "", SOFT, HARD, "", "", HARD, SOFT};
    unsigned i;
    memset(rows, 0xA5, sizeof(rows));
    raweb_set_credentials("Synthetic", "synthetic-key");
    if (raweb_game_progress(1, &game, rows, 8) != 8) return 2;
    for (i = 0; i < 8; i++) {
        if (rows[i].id != i + 1 || rows[i].earned_hardcore != expected_hardcore[i] ||
            strcmp(rows[i].date_earned, expected_dates[i]) != 0) {
            fprintf(stderr, "Date case %u: hardcore=%d date=%s\n",
                    i + 1, rows[i].earned_hardcore, rows[i].date_earned);
            return 1;
        }
    }
    puts("Eight native earned-date cases passed without network access");
    return 0;
}
