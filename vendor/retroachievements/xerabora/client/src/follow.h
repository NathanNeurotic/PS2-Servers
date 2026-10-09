/*
  Following the account's play on any other device. The Web API says
  which game the account is in (the profile's LastGameID and rich
  presence line) and which achievements just landed; from that the
  page gets the same LIVE view it gets from the console, minus
  measured progress and trackers, which only a memory source carries.
*/
#ifndef XERABORA_FOLLOW_H
#define XERABORA_FOLLOW_H

#include <time.h>
#include "raweb.h"

#define FOLLOW_MAX_ACH 400
#define FOLLOW_MAX_SESSION 64

struct follow_unlock
{
    unsigned id;
    char title[96];
    char badge[24];
    unsigned points;
    int hardcore;
    time_t at;
};

struct follow_state
{
    int on;                 /* the user's switch */
    unsigned game_id;       /* 0: nothing followed yet */
    struct raweb_game_progress game;
    struct raweb_achievement ach[FOLLOW_MAX_ACH];
    int ach_count;
    char rich_presence[192];
    char rich_presence_date[32]; /* the server's own timestamp of the line */
    int online;             /* the server's Status, refreshed once a minute */
    time_t rp_changed;      /* when the line last changed: the last sign of play */
    time_t game_since;      /* when this game was first seen */
    time_t polled;          /* last successful poll */
    time_t started;         /* when following began */
    struct follow_unlock session[FOLLOW_MAX_SESSION]; /* unlocks seen since start, newest first */
    int session_count;
    int failures;           /* consecutive failed polls */
};

void follow_set_enabled(int on);
int follow_enabled(void);

/* Called from the main loop about once a second. `console_active` says
   the console is the live source right now; following then only
   keeps the profile line fresh at a slow rate and never takes LIVE. */
void follow_tick(int console_active);

/* Forget the followed game and the session log; on a key change. */
void follow_reset(void);

const struct follow_state *follow_get(void);

/* True when LIVE should show the followed game: following is on, a
   game is known, and the console is not the source. */
int follow_active(int console_active);

#endif
