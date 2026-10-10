#include "sound.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "log.h"
#include "platform.h"
#include "sounds_data.h"

#ifdef _WIN32
#include <windows.h>
#include <mmsystem.h>
#define SEP "\\"
#else
#include <sys/stat.h>
#define SEP "/"
#endif

static int g_enabled = 0;

static const char *const g_names[SOUND_COUNT] = {"connect", "disconnect", "achievement"};

struct clip
{
    const unsigned char *data; /* embedded default */
    unsigned int len;
    char path[640];            /* file to play, or "" */
};

static struct clip g_clips[SOUND_COUNT];

static int file_exists(const char *path)
{
    FILE *f = fopen(path, "rb");

    if (f == NULL)
        return 0;
    fclose(f);
    return 1;
}

#ifndef _WIN32
/* Command-line players are the portable option on Linux and macOS. The
   embedded default is written to the config directory once so a player
   can read it as a file. */
static const char *find_player(void)
{
    static const char *const candidates[] = {
#ifdef __APPLE__
        "afplay",
#endif
        "paplay", "aplay", "pw-play", NULL};
    int i;

    for (i = 0; candidates[i] != NULL; i++) {
        char cmd[128];

        snprintf(cmd, sizeof(cmd), "command -v %s >/dev/null 2>&1", candidates[i]);
        if (system(cmd) == 0)
            return candidates[i];
    }
    return NULL;
}

static const char *g_player = NULL;
#endif

void sound_init(int enabled)
{
    char dir[600];
    int i;

    g_enabled = enabled;
    if (!enabled)
        return;

    g_clips[SOUND_CONNECT].data = sound_connect_wav;
    g_clips[SOUND_CONNECT].len = sound_connect_wav_len;
    g_clips[SOUND_DISCONNECT].data = sound_disconnect_wav;
    g_clips[SOUND_DISCONNECT].len = sound_disconnect_wav_len;
    g_clips[SOUND_ACHIEVEMENT].data = sound_achievement_wav;
    g_clips[SOUND_ACHIEVEMENT].len = sound_achievement_wav_len;

    if (platform_config_dir(dir, sizeof(dir)) != 0)
        return;
    strncat(dir, SEP "sounds", sizeof(dir) - strlen(dir) - 1);
#ifdef _WIN32
    CreateDirectoryA(dir, NULL);
#else
    mkdir(dir, 0700);
#endif

    for (i = 0; i < SOUND_COUNT; i++) {
        struct clip *c = &g_clips[i];

        snprintf(c->path, sizeof(c->path), "%s" SEP "%s.wav", dir, g_names[i]);
        if (file_exists(c->path)) {
            log_trace("sound %s: using %s", g_names[i], c->path);
            continue;
        }
#ifdef _WIN32
        c->path[0] = '\0'; /* play from memory */
#else
        {
            FILE *f;

            snprintf(c->path, sizeof(c->path), "%s" SEP "default-%s.wav", dir, g_names[i]);
            f = fopen(c->path, "wb");
            if (f != NULL) {
                fwrite(c->data, 1, c->len, f);
                fclose(f);
            } else {
                c->path[0] = '\0';
            }
        }
#endif
    }

#ifndef _WIN32
    g_player = find_player();
    if (g_player == NULL)
        log_detail("no audio player found (afplay, paplay, aplay, pw-play); sounds fall back to the terminal bell");
#endif
}

void sound_play(enum sound_id id)
{
    struct clip *c;

    if (!g_enabled || id < 0 || id >= SOUND_COUNT)
        return;
    c = &g_clips[id];

#ifdef _WIN32
    if (c->path[0] != '\0')
        PlaySoundA(c->path, NULL, SND_FILENAME | SND_ASYNC | SND_NODEFAULT);
    else
        PlaySoundA((LPCSTR)c->data, NULL, SND_MEMORY | SND_ASYNC | SND_NODEFAULT);
#else
    if (g_player != NULL && c->path[0] != '\0') {
        /* The managed config directory can contain apostrophes (usernames,
           mount points). Never splice it unescaped into a shell command;
           the sound controls also permit importing clips via the GUI. */
        char cmd[sizeof(c->path) * 4 + 128];
        const unsigned char *q = (const unsigned char *)c->path;
        int head = snprintf(cmd, sizeof(cmd), "%s %s",
                            g_player, strcmp(g_player, "aplay") == 0 ? "-q " : "");
        size_t n;

        if (head < 0 || (size_t)head >= sizeof(cmd) - 32)
            goto bell;
        n = (size_t)head;
        cmd[n++] = 39; /* opening quote */
        while (*q != 0 && n + 32 < sizeof(cmd)) {
            if (*q == 39) {
                cmd[n++] = 39;
                cmd[n++] = 92; /* backslash */
                cmd[n++] = 39;
                cmd[n++] = 39;
            } else {
                cmd[n++] = (char)*q;
            }
            q++;
        }
        if (*q == 0) {
            cmd[n++] = 39; /* closing quote */
            snprintf(cmd + n, sizeof(cmd) - n, " >/dev/null 2>&1 &");
            if (system(cmd) == 0)
                return;
        }
    }
bell:
    putchar('\a');
    fflush(stdout);
#endif
}
