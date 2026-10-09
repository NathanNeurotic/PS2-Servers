/*
  The raylib window, kept as a fallback build (make windows-gui). Draws
  only; data comes from raweb.c. Text goes through px(), a whole-number
  scale on the pixel grid.
*/

#include "ui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "raylib.h"

#include "log.h"
#include "raweb.h"

#define UI_W 560
#define UI_H 760

/* The palette from the concept: a dark blue ground, one accent, and
   three states (done, waiting, not available here). */
#define C_BG        CLITERAL(Color){ 7, 22, 39, 255 }
#define C_BG_TOP    CLITERAL(Color){ 18, 58, 99, 255 }
#define C_PANEL     CLITERAL(Color){ 20, 62, 100, 110 }
#define C_PANEL_LIT CLITERAL(Color){ 20, 62, 100, 170 }
#define C_LINE      CLITERAL(Color){ 29, 70, 112, 255 }
#define C_TEXT      CLITERAL(Color){ 207, 230, 247, 255 }
#define C_DIM       CLITERAL(Color){ 94, 147, 189, 255 }
#define C_DIMMER    CLITERAL(Color){ 61, 111, 150, 255 }
#define C_WHITE     CLITERAL(Color){ 255, 255, 255, 255 }
#define C_ACCENT    CLITERAL(Color){ 127, 212, 255, 255 }
#define C_GREEN     CLITERAL(Color){ 102, 255, 178, 255 }
#define C_AMBER     CLITERAL(Color){ 255, 184, 77, 255 }

/* One line of text on the pixel grid. size is a whole multiple of the
   default font's 10 px. */
static void px(const char *text, int x, int y, int size, Color c)
{
    DrawTextEx(GetFontDefault(), text, CLITERAL(Vector2){ (float)x, (float)y },
               (float)size, (float)(size / 10), c);
}

static void panel(int x, int y, int w, int h, Color c)
{
    DrawRectangle(x, y, w, h, c);
}

/* A progress bar the way the concept draws it: a hairline box with a
   solid fill, no rounding, no gradient. */
static void bar(int x, int y, int w, int h, float fraction, Color fill)
{
    int inner;

    if (fraction < 0.0f)
        fraction = 0.0f;
    if (fraction > 1.0f)
        fraction = 1.0f;

    DrawRectangleLines(x, y, w, h, C_LINE);
    inner = (int)((float)(w - 2) * fraction);
    if (inner > 0)
        DrawRectangle(x + 1, y + 1, inner, h - 2, fill);
}

/* What the window shows, fetched once at start. The console feed will
   land in the same place later, so this is a struct and not
   a pile of locals. */
struct ui_state
{
    struct raweb_profile me;
    struct raweb_game_progress game;
    struct raweb_achievement *ach;
    int ach_count;
    int have_game;
    int scroll;
};

static int by_display_order(const void *a, const void *b)
{
    const struct raweb_achievement *x = a, *y = b;

    if (x->display_order != y->display_order)
        return (int)x->display_order - (int)y->display_order;
    return (int)x->id - (int)y->id;
}

static void ui_load(struct ui_state *s)
{
    struct raweb_game recent[4];
    int n;

    memset(s, 0, sizeof(*s));
    raweb_profile(&s->me);

    /* Whatever the account played last is what the window opens on.
       When the console is wired in it will override this the moment
       telemetry names a game. */
    n = raweb_recently_played(recent, 4);
    if (n <= 0)
        return;

    s->ach = calloc(600, sizeof(*s->ach));
    if (s->ach == NULL)
        return;

    s->ach_count = raweb_game_progress(recent[0].id, &s->game, s->ach, 600);
    if (s->ach_count > 0) {
        qsort(s->ach, (size_t)s->ach_count, sizeof(*s->ach), by_display_order);
        s->have_game = 1;
    }
}

static void draw_header(const struct ui_state *s)
{
    char line[160];

    DrawRectangleGradientV(0, 0, UI_W, 120, C_BG_TOP, C_BG);
    DrawRectangle(0, 0, UI_W, 34, CLITERAL(Color){ 4, 16, 28, 190 });

    px("xerabora", 14, 12, 10, C_WHITE);
    px("a RetroAchievements client", 110, 13, 10, C_DIMMER);

    /* No console yet: say so rather than draw a hopeful green dot. */
    DrawRectangle(14, 48, 8, 8, C_DIMMER);
    px("console link: not connected", 32, 46, 10, C_DIM);

    if (s->me.user[0] != '\0') {
        snprintf(line, sizeof(line), "%s   %u points", s->me.user, s->me.points);
        px(line, 14, 70, 20, C_TEXT);
        if (s->me.rank > 0) {
            snprintf(line, sizeof(line), "rank %u of %u", s->me.rank, s->me.total_ranked);
            px(line, 14, 96, 10, C_DIM);
        }
    } else {
        px("no Web API key -- run with --api-key KEY once", 14, 70, 10, C_AMBER);
    }
}

static void draw_game(const struct ui_state *s)
{
    char line[200];
    float done;

    DrawLine(0, 128, UI_W, 128, C_LINE);

    if (!s->have_game) {
        px("nothing played recently", 14, 146, 10, C_DIM);
        return;
    }

    px(s->game.title, 14, 144, 20, C_WHITE);
    snprintf(line, sizeof(line), "%s", s->game.console);
    px(line, 14, 172, 10, C_DIM);

    done = s->game.achievements_total > 0
               ? (float)s->game.awarded / (float)s->game.achievements_total
               : 0.0f;
    bar(14, 192, UI_W - 120, 13, done, C_ACCENT);
    snprintf(line, sizeof(line), "%u / %u", s->game.awarded, s->game.achievements_total);
    px(line, UI_W - 98, 193, 10, C_TEXT);
}

/* One row per achievement, clipped and wheel-scrolled. Unlocked rows keep
   their colour, locked ones dim. */
static void draw_list(const struct ui_state *s)
{
    const int top = 220, row_h = 46;
    int visible = (UI_H - top - 44) / row_h;
    int i;

    if (!s->have_game)
        return;

    for (i = 0; i < visible && s->scroll + i < s->ach_count; i++) {
        const struct raweb_achievement *a = &s->ach[s->scroll + i];
        int y = top + i * row_h;
        int done = a->date_earned[0] != '\0';
        char pts[32];

        panel(10, y, UI_W - 20, row_h - 4, done ? C_PANEL_LIT : C_PANEL);
        if (done)
            DrawRectangle(10, y, 2, row_h - 4, C_GREEN);

        /* The badge image lands here once the icon cache exists; until
           then a plain block keeps the row's rhythm. */
        DrawRectangle(18, y + 7, 30, 30, CLITERAL(Color){ 10, 33, 54, 255 });
        DrawRectangleLines(18, y + 7, 30, 30, done ? C_ACCENT : C_LINE);

        px(a->title, 58, y + 8, 10, done ? C_WHITE : C_TEXT);
        px(a->description, 58, y + 24, 10, C_DIM);

        snprintf(pts, sizeof(pts), "%u", a->points);
        px(pts, UI_W - 46, y + 8, 10, done ? C_GREEN : C_DIMMER);
        if (a->type[0] != '\0')
            px(a->type, UI_W - 46 - 8 * (int)strlen(a->type), y + 24, 10, C_DIMMER);
    }
}

static void draw_footer(const struct ui_state *s)
{
    char line[120];

    DrawLine(0, UI_H - 34, UI_W, UI_H - 34, C_LINE);
    if (s->have_game) {
        snprintf(line, sizeof(line), "%d of %d shown   wheel to scroll",
                 s->ach_count > 0 ? s->scroll + 1 : 0, s->ach_count);
        px(line, 14, UI_H - 24, 10, C_DIMMER);
    }
    px("softcore", UI_W - 90, UI_H - 24, 10, C_DIM);
}

int ui_run(void)
{
    struct ui_state s;

    if (!raweb_have_credentials())
        log_warn("no Web API key: the window will come up empty");

    ui_load(&s);

    SetTraceLogLevel(LOG_WARNING);
    SetConfigFlags(FLAG_VSYNC_HINT);
    InitWindow(UI_W, UI_H, "xerabora");
    SetTargetFPS(60);

    while (!WindowShouldClose()) {
        int visible = (UI_H - 220 - 44) / 46;
        float wheel = GetMouseWheelMove();

        if (wheel != 0.0f) {
            s.scroll -= (int)wheel * 3;
            if (s.scroll > s.ach_count - visible)
                s.scroll = s.ach_count - visible;
            if (s.scroll < 0)
                s.scroll = 0;
        }

        BeginDrawing();
        ClearBackground(C_BG);
        draw_header(&s);
        draw_game(&s);
        draw_list(&s);
        draw_footer(&s);
        EndDrawing();
    }

    CloseWindow();
    free(s.ach);
    return 0;
}
