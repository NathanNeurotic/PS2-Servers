#include "discord.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "log.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#endif

/* Discord application id. Zero until one is registered: the presence is
   skipped rather than shown under someone else's app name. */
#define DISCORD_APP_ID ""

/* Discord accepts one presence update every fifteen seconds. */
#define DISCORD_MIN_GAP 15
#define DISCORD_RETRY 60

#ifdef _WIN32
static HANDLE g_pipe = INVALID_HANDLE_VALUE;
#define PIPE_OK (g_pipe != INVALID_HANDLE_VALUE)
#else
static int g_sock = -1;
#define PIPE_OK (g_sock >= 0)
#endif

static int g_ready;          /* handshake accepted */
static time_t g_last_send;
static time_t g_last_try;
static time_t g_since;       /* when the current game started */

static char g_game[128];
static char g_detail[128];
static char g_badge[32];
static int g_dirty;

/* ---- The pipe ----------------------------------------------------------- */

static void discord_close(void)
{
#ifdef _WIN32
    if (g_pipe != INVALID_HANDLE_VALUE) {
        CloseHandle(g_pipe);
        g_pipe = INVALID_HANDLE_VALUE;
    }
#else
    if (g_sock >= 0) {
        close(g_sock);
        g_sock = -1;
    }
#endif
    g_ready = 0;
}

/* Discord listens on ten possible endpoints, numbered 0 to 9, because
   several clients can run side by side. First one that answers wins. */
static int discord_open(void)
{
    int i;

    for (i = 0; i < 10; i++) {
#ifdef _WIN32
        char name[64];

        snprintf(name, sizeof(name), "\\\\\\\\.\\\\pipe\\\\discord-ipc-%d", i);
        g_pipe = CreateFileA(name, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                             OPEN_EXISTING, 0, NULL);
        if (g_pipe != INVALID_HANDLE_VALUE)
            return 1;
#else
        struct sockaddr_un addr;
        const char *base = getenv("XDG_RUNTIME_DIR");

        if (base == NULL)
            base = getenv("TMPDIR");
        if (base == NULL)
            base = "/tmp";

        memset(&addr, 0, sizeof(addr));
        addr.sun_family = AF_UNIX;
        snprintf(addr.sun_path, sizeof(addr.sun_path), "%s/discord-ipc-%d", base, i);

        g_sock = socket(AF_UNIX, SOCK_STREAM, 0);
        if (g_sock < 0)
            return 0;
        if (connect(g_sock, (struct sockaddr *)&addr, sizeof(addr)) == 0)
            return 1;
        close(g_sock);
        g_sock = -1;
#endif
    }
    return 0;
}

/* One frame: opcode, length, payload. Both numbers are little-endian,
   which is what the machines this runs on write anyway. */
static int discord_send(unsigned opcode, const char *json)
{
    unsigned char head[8];
    unsigned len = (unsigned)strlen(json);

    if (!PIPE_OK)
        return 0;

    head[0] = (unsigned char)opcode;
    head[1] = (unsigned char)(opcode >> 8);
    head[2] = (unsigned char)(opcode >> 16);
    head[3] = (unsigned char)(opcode >> 24);
    head[4] = (unsigned char)len;
    head[5] = (unsigned char)(len >> 8);
    head[6] = (unsigned char)(len >> 16);
    head[7] = (unsigned char)(len >> 24);

#ifdef _WIN32
    {
        DWORD wrote = 0;

        if (!WriteFile(g_pipe, head, 8, &wrote, NULL) || wrote != 8 ||
            !WriteFile(g_pipe, json, len, &wrote, NULL) || wrote != len) {
            discord_close();
            return 0;
        }
    }
#else
    if (send(g_sock, head, 8, 0) != 8 || send(g_sock, json, len, 0) != (int)len) {
        discord_close();
        return 0;
    }
#endif
    return 1;
}

/* ---- Presence ----------------------------------------------------------- */

static void discord_handshake(void)
{
    char json[128];

    snprintf(json, sizeof(json), "{\"v\":1,\"client_id\":\"%s\"}", DISCORD_APP_ID);
    if (discord_send(0, json)) {
        g_ready = 1;
        log_info("discord: connected");
    }
}

static void json_escape(char *out, size_t size, const char *in)
{
    size_t o = 0;

    for (; in != NULL && *in != '\0' && o + 2 < size; in++) {
        if (*in == '"' || *in == '\\') {
            out[o++] = '\\';
            out[o++] = *in;
        } else if ((unsigned char)*in >= 0x20) {
            out[o++] = *in;
        }
    }
    out[o] = '\0';
}

static void discord_flush(void)
{
    char json[768], game[256], detail[256];

    if (!g_ready || !g_dirty)
        return;
    if (time(NULL) - g_last_send < DISCORD_MIN_GAP)
        return;

    json_escape(game, sizeof(game), g_game);
    json_escape(detail, sizeof(detail), g_detail);

    /* The nonce has to be there and has to be unique; the clock is
       unique enough for a presence update. */
    snprintf(json, sizeof(json),
             "{\"cmd\":\"SET_ACTIVITY\",\"nonce\":\"%ld\",\"args\":{\"pid\":%d,\"activity\":{"
             "\"details\":\"%s\",\"state\":\"%s\","
             "\"timestamps\":{\"start\":%ld},"
             "\"assets\":{\"large_image\":\"%s\",\"large_text\":\"RetroAchievements on real hardware\"}"
             "}}}",
             (long)time(NULL),
#ifdef _WIN32
             (int)GetCurrentProcessId(),
#else
             (int)getpid(),
#endif
             game, detail, (long)g_since,
             g_badge[0] != '\0' ? g_badge : "ps2");

    if (discord_send(1, json)) {
        g_last_send = time(NULL);
        g_dirty = 0;
    }
}

void discord_init(void)
{
    if (DISCORD_APP_ID[0] == '\0')
        return; /* no application registered yet; stay silent */

    g_last_try = time(NULL);
    if (discord_open())
        discord_handshake();
}

void discord_shutdown(void)
{
    discord_close();
}

void discord_set(const char *game, const char *detail, const char *badge)
{
    if (game != NULL && strcmp(g_game, game) != 0)
        g_since = time(NULL);

    snprintf(g_game, sizeof(g_game), "%s", game != NULL ? game : "");
    snprintf(g_detail, sizeof(g_detail), "%s", detail != NULL ? detail : "");
    snprintf(g_badge, sizeof(g_badge), "%s", badge != NULL ? badge : "");
    g_dirty = 1;
}

void discord_tick(void)
{
    if (DISCORD_APP_ID[0] == '\0')
        return;

    if (!PIPE_OK) {
        /* Discord may have been started after us, or restarted. */
        if (time(NULL) - g_last_try < DISCORD_RETRY)
            return;
        g_last_try = time(NULL);
        if (discord_open())
            discord_handshake();
        return;
    }

    if (!g_ready)
        discord_handshake();
    else
        discord_flush();
}
