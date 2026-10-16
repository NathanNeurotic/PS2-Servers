#include "config.h"

#include <stdio.h>
#include <string.h>

#include "platform.h"

#ifndef _WIN32
#include <sys/stat.h>
#endif

#ifdef _WIN32
#define SEP "\\"
#else
#define SEP "/"
#endif

int config_credentials_path(char *out, size_t size)
{
    char dir[512];

    if (platform_config_dir(dir, sizeof(dir)) != 0)
        return -1;
    snprintf(out, size, "%s" SEP "credentials", dir);
    return 0;
}

/* PS2-Servers profile secrets. Windows DPAPI is user/machine bound;
   POSIX files are created private before any bytes are written. */
#ifdef _WIN32
#include <windows.h>
#include <wincrypt.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif
#ifndef _WIN32
/* Bind validation and secret access to the same private directory descriptor.
   Reopening the pathname after validation would permit a parent-entry swap. */
static int secret_open(const char *path, int writing)
{
    char directory[600], *name;
    struct stat info;
    int dir, fd;
    if (strlen(path) >= sizeof(directory)) return -1;
    strcpy(directory, path);
    name = strrchr(directory, '/');
    if (!name || !name[1]) return -1;
    *name++ = 0;
    dir = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (dir < 0) return -1;
    if (fstat(dir, &info) || !S_ISDIR(info.st_mode) || info.st_uid != geteuid() ||
        (info.st_mode & 0077)) { close(dir); return -1; }
    fd = openat(dir, name, O_NOFOLLOW | O_NONBLOCK |
                (writing ? O_WRONLY | O_CREAT : O_RDONLY), 0600);
    close(dir);
    if (fd < 0) return -1;
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_uid != geteuid() ||
        info.st_nlink != 1 || (info.st_mode & 0077) ||
        (writing && (fchmod(fd, 0600) || ftruncate(fd, 0)))) {
        close(fd); return -1;
    }
    return fd;
}
#endif
static int secret_read(const char *path, char *out, size_t size)
{
    unsigned char bytes[4096]; size_t n; FILE *f;
    out[0] = 0;
#ifdef _WIN32
    f = fopen(path, "rb");
#else
    int fd = secret_open(path, 0);
    if (fd < 0) return 0;
    f = fdopen(fd, "rb"); if (!f) close(fd);
#endif
    if (!f) return 0;
    n = fread(bytes, 1, sizeof(bytes), f); fclose(f);
#ifdef _WIN32
    DATA_BLOB input, result = {0};
    input.pbData = bytes; input.cbData = (DWORD)n;
    if (!CryptUnprotectData(&input, NULL, NULL, NULL, NULL, CRYPTPROTECT_UI_FORBIDDEN, &result)) return 0;
    if (!result.cbData || result.cbData > size || result.pbData[result.cbData-1]) {
        SecureZeroMemory(result.pbData, result.cbData); LocalFree(result.pbData); return 0;
    }
    memcpy(out, result.pbData, result.cbData);
    SecureZeroMemory(result.pbData, result.cbData); LocalFree(result.pbData);
#else
    if (!n || n >= size) return 0;
    memcpy(out, bytes, n); out[n] = 0;
#endif
    memset(bytes, 0, sizeof(bytes)); return out[0] != 0;
}
static int secret_write(const char *path, const char *text)
{
    FILE *f; int ok; const void *bytes = text; size_t n = strlen(text)+1;
#ifdef _WIN32
    DATA_BLOB input, result = {0};
    input.pbData = (BYTE*)text; input.cbData = (DWORD)n;
    if (!CryptProtectData(&input, L"PS2-Servers RetroAchievements", NULL, NULL, NULL, CRYPTPROTECT_UI_FORBIDDEN, &result)) return -1;
    bytes = result.pbData; n = result.cbData;
    f = fopen(path, "wb");
#else
    int fd = secret_open(path, 1);
    if (fd < 0) return -1;
    f = fdopen(fd, "wb"); if (!f) close(fd);
#endif
    ok = f && fwrite(bytes, 1, n, f) == n;
    if (f && fclose(f)) ok = 0;
#ifdef _WIN32
    SecureZeroMemory(result.pbData, result.cbData); LocalFree(result.pbData);
#endif
    return ok ? 0 : -1;
}
int config_load_credentials(char *user, size_t user_size, char *token, size_t token_size)
{
    char path[600], text[1024], *separator; int ok = 0;
    user[0] = token[0] = 0;
    if (config_credentials_path(path, sizeof(path)) || !secret_read(path, text, sizeof(text))) return 0;
    separator = strchr(text, '\n');
    if (separator) {
        *separator++ = 0;
        if (strlen(text) < user_size && strlen(separator) < token_size) {
            strcpy(user, text); strcpy(token, separator); ok = user[0] && token[0];
        }
    }
    memset(text, 0, sizeof(text)); return ok;
}
int config_save_credentials(const char *user, const char *token)
{
    char path[600], text[1024]; int n, result;
    if (config_credentials_path(path, sizeof(path))) return -1;
    n = snprintf(text, sizeof(text), "%s\n%s", user, token);
    if (n < 0 || n >= (int)sizeof(text)) return -1;
    result = secret_write(path, text); memset(text, 0, sizeof(text)); return result;
}
static int config_apikey_path(char *out, size_t size)
{
    char dir[512];
    if (platform_config_dir(dir, sizeof(dir))) return -1;
    snprintf(out, size, "%s" SEP "apikey", dir); return 0;
}
int config_load_apikey(char *key, size_t size)
{
    char path[600]; key[0] = 0;
    return !config_apikey_path(path, sizeof(path)) && secret_read(path, key, size);
}
int config_save_apikey(const char *key)
{
    char path[600];
    if (!key || !key[0] || config_apikey_path(path, sizeof(path))) return -1;
    return secret_write(path, key);
}

static int config_lan_path(char *out, size_t size)
{
    char dir[512];

    if (platform_config_dir(dir, sizeof(dir)) != 0)
        return -1;
    snprintf(out, size, "%s" SEP "lan", dir);
    return 0;
}

int config_load_lan(void)
{
    char path[600], line[8] = "";
    FILE *f;

    if (config_lan_path(path, sizeof(path)) != 0)
        return 0;
    f = fopen(path, "r");
    if (f == NULL)
        return 0;
    if (fgets(line, (int)sizeof(line), f) == NULL)
        line[0] = '\0';
    fclose(f);
    return line[0] == '1';
}

int config_save_lan(int on)
{
    char path[600];
    FILE *f;

    if (config_lan_path(path, sizeof(path)) != 0)
        return -1;
    f = fopen(path, "w");
    if (f == NULL)
        return -1;
    fprintf(f, "%d\n", on ? 1 : 0);
    fclose(f);
    return 0;
}

static int config_follow_path(char *out, size_t size)
{
    char dir[512];

    if (platform_config_dir(dir, sizeof(dir)) != 0)
        return -1;
    snprintf(out, size, "%s" SEP "follow", dir);
    return 0;
}

int config_load_follow(void)
{
    char path[600], line[8] = "";
    FILE *f;

    if (config_follow_path(path, sizeof(path)) != 0)
        return 1;
    f = fopen(path, "r");
    if (f == NULL)
        return 1; /* no file: on */
    if (fgets(line, (int)sizeof(line), f) == NULL)
        line[0] = '\0';
    fclose(f);
    return line[0] != '0';
}

int config_save_follow(int on)
{
    char path[600];
    FILE *f;

    if (config_follow_path(path, sizeof(path)) != 0)
        return -1;
    f = fopen(path, "w");
    if (f == NULL)
        return -1;
    fprintf(f, "%d\n", on ? 1 : 0);
    fclose(f);
    return 0;
}

int config_games_path(char *out, size_t size)
{
    char dir[512];

    if (platform_config_dir(dir, sizeof(dir)) != 0)
        return -1;
    snprintf(out, size, "%s" SEP "games", dir);
    return 0;
}

void config_forget_credentials(void)
{
    char path[600];

    if (config_credentials_path(path, sizeof(path)) == 0)
        remove(path);
}
