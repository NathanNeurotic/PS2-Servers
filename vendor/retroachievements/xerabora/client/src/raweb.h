/*
  The RetroAchievements Web API: the library, a game's achievements,
  leaderboards, the profile. Read-only, keyed by the profile's API key;
  unlocking stays with rc_client and the login token. Every call blocks: one
  HTTP request, made from a poll, not the frame loop.
*/
#ifndef XERABORA_RAWEB_H
#define XERABORA_RAWEB_H

#include <stddef.h>

/* Username and Web API key. Without them every call below returns 0
   and the client shows less. */
void raweb_set_credentials(const char *user, const char *api_key);
int raweb_have_credentials(void);

struct raweb_profile
{
    char user[64];
    char motto[128];
    unsigned points;        /* hardcore points */
    unsigned softcore_points;
    unsigned rank;
    unsigned total_ranked;  /* how many players are ranked at all */
    char last_game[96];
    unsigned last_game_id;      /* LastGameID: the game the account is in */
    char rich_presence[192];
};

/* The slow summary call, used once a minute for the two things the
   profile lacks: whether the account is online right now, and when
   the rich presence line was last updated. */
struct raweb_summary
{
    int online;
    char rich_presence_date[32];
    unsigned last_game_id;
    char last_game_console[40];
};
int raweb_summary(struct raweb_summary *out);

/* One game in the library, from GetUserCompletionProgress. */
struct raweb_game
{
    unsigned id;
    char title[96];
    char console[40];
    unsigned console_id;
    unsigned achievements;      /* in the set */
    unsigned awarded;           /* softcore unlocks */
    unsigned awarded_hardcore;
    char last_played[32];       /* "YYYY-MM-DD HH:MM:SS", may be empty */
    char icon[64];              /* "/Images/nnnnnn.png", server-relative */
    char award[24];             /* HighestAwardKind: "mastered",
                                   "completed", "beaten-hardcore",
                                   "beaten-softcore", or empty */
    int mastered;
};

/* One achievement, from GetGameInfoAndUserProgress. Measured progress ("3
   of 10") is not here: only a memory source carries it. */
struct raweb_achievement
{
    unsigned id;
    char title[96];
    char description[192];
    char badge[24];             /* badge image name on media.retroachievements.org */
    char type[24];              /* "progression", "missable", "win_condition", "" */
    unsigned points;
    unsigned num_awarded;       /* how many players have it */
    unsigned display_order;     /* the set author's own ordering */
    char date_earned[32];       /* empty when locked */
    int earned_hardcore;
};

/* Three orders, all wanted: the author's DisplayOrder, the type
   (progression, win_condition, missable) for the intended path, and the
   median unlock time, the order players take. */
int raweb_median_times(unsigned game_id, unsigned *ids, unsigned *seconds, int max);

struct raweb_game_progress
{
    unsigned id;
    char title[96];
    char console[40];
    unsigned console_id;
    unsigned parent_id;         /* ParentGameID: set when this game is a subset */
    char image_icon[64];
    unsigned achievements_total;
    unsigned awarded;
    unsigned awarded_hardcore;
    unsigned points_total;
    unsigned points_earned;
};

/* A subset is a separate game on the server, titled
   "<Parent> [Subset - Name]", and the Web API has no call that lists
   them. The console's game list does, once, by title. */
struct raweb_subset
{
    unsigned id;
    char title[96];             /* the name inside the brackets */
    unsigned achievements;
    unsigned points;
};

/* Every game with a published set on a console, from the same cached
   list the subset lookup uses: id, title, achievement count. For
   --survey-console, which walks a whole console looking for sets that
   would need several packets a snapshot. */
int raweb_console_games(unsigned console_id, struct raweb_game *rows, int max);

/* The base title of a game: the part before " [Subset - ". */
void raweb_base_title(const char *title, char *out, size_t size);

/* Subsets of the game titled `base_title` on `console_id`, and the id of
   the base game itself (0 when the list has no such title). One game
   list per console is kept in memory for an hour. */
int raweb_game_subsets(unsigned console_id, const char *base_title,
                       unsigned *base_id, struct raweb_subset *rows, int max);

/* Every call returns the number of rows written, or 0 on any failure
   (no credentials, transport error, unexpected JSON). Failures are
   logged, never fatal: the client keeps working with less. */
int raweb_profile(struct raweb_profile *out);
int raweb_completion_progress(struct raweb_game *rows, int max);
int raweb_recently_played(struct raweb_game *rows, int max);
int raweb_game_progress(unsigned game_id, struct raweb_game_progress *info,
                        struct raweb_achievement *rows, int max);

/* One leaderboard of a game, with the top score and, when the account
   has one, the account's own. */
struct raweb_leaderboard
{
    unsigned id;
    char title[96];
    char description[160];
    char format[16];        /* "SCORE", "TIME", "MILLISECS", ... */
    int lower_is_better;
    char top_user[48];
    char top_score[32];     /* already formatted by the server */
    char my_score[32];      /* empty when the account has no entry */
    unsigned my_rank;
    unsigned entries;       /* how many players are on the board */
};

/* A recent unlock, for the "recent" panel and the stream labels. */
struct raweb_unlock
{
    unsigned achievement_id;
    unsigned game_id;
    char title[96];
    char game_title[96];
    char badge[24];
    unsigned points;
    char date[32];
    int hardcore;
};

int raweb_game_leaderboards(unsigned game_id, struct raweb_leaderboard *rows, int max);
int raweb_recent_unlocks(int minutes, struct raweb_unlock *rows, int max);

/* The image hashes RetroAchievements knows for a game. This is what
   turns "your image is not recognised" into something useful: the
   client can say which images would be. */
int raweb_game_hashes(unsigned game_id, char (*rows)[33], char (*names)[96], int max);

#endif
