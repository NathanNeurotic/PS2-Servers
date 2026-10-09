#include "platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#include <shlobj.h>
#include <process.h>
#else
#include <pthread.h>
#include <errno.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <termios.h>
#include <time.h>
#endif

static volatile int *g_stop_flag = NULL;

#ifdef _WIN32
static BOOL WINAPI on_ctrl(DWORD type)
{
    (void)type;
    if (g_stop_flag != NULL)
        *g_stop_flag = 1;
    return TRUE;
}
#else
static void on_signal(int sig)
{
    (void)sig;
    if (g_stop_flag != NULL)
        *g_stop_flag = 1;
}
#endif

void platform_on_stop(volatile int *flag)
{
    g_stop_flag = flag;
#ifdef _WIN32
    SetConsoleCtrlHandler(on_ctrl, TRUE);
#else
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
#endif
}

void platform_console_init(void)
{
#ifdef _WIN32
    /* Game titles from the server are UTF-8. */
    SetConsoleOutputCP(CP_UTF8);
#endif
}

void platform_attach_console(int force)
{
#ifdef _WIN32
    if (AttachConsole(ATTACH_PARENT_PROCESS) || (force && AllocConsole())) {
        freopen("CONOUT$", "w", stdout);
        freopen("CONOUT$", "w", stderr);
        freopen("CONIN$", "r", stdin);
    }
#else
    (void)force;
#endif
}

int platform_net_init(void)
{
#ifdef _WIN32
    WSADATA wsa;

    return WSAStartup(MAKEWORD(2, 2), &wsa) == 0 ? 0 : -1;
#else
    /* A page closed by the browser is found out at the next write;
       without this that write ends the process. */
    signal(SIGPIPE, SIG_IGN);
    return 0;
#endif
}

void platform_net_shutdown(void)
{
#ifdef _WIN32
    WSACleanup();
#endif
}

void platform_sleep_ms(unsigned int ms)
{
#ifdef _WIN32
    Sleep(ms);
#else
    struct timespec ts;

    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
#endif
}

static int make_dir(const char *path)
{
#ifdef _WIN32
    return (_mkdir(path) == 0 || GetLastError() == ERROR_ALREADY_EXISTS) ? 0 : -1;
#else
    return (mkdir(path, 0700) == 0 || errno == EEXIST) ? 0 : -1;
#endif
}

int platform_config_dir(char *out, size_t size)
{
    const char *profile = getenv("PS2SERVERS_RA_PROFILE");
    if (profile != NULL && profile[0] != 0) {
        if (strlen(profile) >= size) return -1;
        snprintf(out, size, "%s", profile);
        return make_dir(out);
    }
#ifdef _WIN32
    /* Local, not Roaming: the token should not replicate between machines. */
    const char *base = getenv("LOCALAPPDATA");

    if (base == NULL || base[0] == '\0')
        base = getenv("APPDATA");
    if (base == NULL || base[0] == '\0')
        return -1;
    snprintf(out, size, "%s\\xerabora", base);
    return make_dir(out);
#else
    const char *xdg = getenv("XDG_CONFIG_HOME");
    const char *home = getenv("HOME");
    char parent[512];

    if (xdg != NULL && xdg[0] != '\0') {
        snprintf(parent, sizeof(parent), "%s", xdg);
    } else if (home != NULL && home[0] != '\0') {
        snprintf(parent, sizeof(parent), "%s/.config", home);
    } else {
        return -1;
    }

    if (make_dir(parent) != 0)
        return -1;
    snprintf(out, size, "%s/xerabora", parent);
    return make_dir(out);
#endif
}

int platform_read_password(char *out, size_t size)
{
    int ok;

#ifdef _WIN32
    HANDLE h = GetStdHandle(STD_INPUT_HANDLE);
    DWORD mode = 0;
    int had_mode = GetConsoleMode(h, &mode);

    if (had_mode)
        SetConsoleMode(h, mode & ~ENABLE_ECHO_INPUT);
    ok = fgets(out, (int)size, stdin) != NULL;
    if (had_mode)
        SetConsoleMode(h, mode);
#else
    struct termios old, quiet;
    int have_tty = tcgetattr(0, &old) == 0;

    if (have_tty) {
        quiet = old;
        quiet.c_lflag &= ~(tcflag_t)ECHO;
        tcsetattr(0, TCSANOW, &quiet);
    }
    ok = fgets(out, (int)size, stdin) != NULL;
    if (have_tty)
        tcsetattr(0, TCSANOW, &old);
#endif

    putchar('\n');
    if (!ok)
        return -1;
    out[strcspn(out, "\r\n")] = '\0';
    return 0;
}

int platform_wait_readable(sock_t sock, int timeout_ms)
{
    fd_set set;
    struct timeval tv;
    int r;

    FD_ZERO(&set);
    FD_SET(sock, &set);
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    r = select((int)sock + 1, &set, NULL, NULL, &tv);
    if (r < 0)
        return -1;
    return r > 0 ? 1 : 0;
}

int platform_wait_readable2(sock_t a, sock_t b, int timeout_ms)
{
    fd_set set;
    struct timeval tv;
    sock_t high = a;
    int r, mask = 0;

    FD_ZERO(&set);
    if (a != SOCK_INVALID)
        FD_SET(a, &set);
    if (b != SOCK_INVALID) {
        FD_SET(b, &set);
        if (b > high)
            high = b;
    }

    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;
    r = select((int)high + 1, &set, NULL, NULL, &tv);
    if (r < 0)
        return -1;
    if (r == 0)
        return 0;

    if (a != SOCK_INVALID && FD_ISSET(a, &set))
        mask |= 1;
    if (b != SOCK_INVALID && FD_ISSET(b, &set))
        mask |= 2;
    return mask;
}

const char *platform_sock_error(void)
{
#ifdef _WIN32
    static char buf[64];

    snprintf(buf, sizeof(buf), "winsock error %d", WSAGetLastError());
    return buf;
#else
    return strerror(errno);
#endif
}

/* ---- Worker thread ---------------------------------------------------- */

struct thread_job
{
    platform_thread_fn fn;
    void *arg;
};

static struct thread_job g_job;

#ifdef _WIN32
static HANDLE g_thread = NULL;

static unsigned __stdcall thread_main(void *p)
{
    struct thread_job *j = (struct thread_job *)p;

    j->fn(j->arg);
    return 0;
}

int platform_thread_start(platform_thread_fn fn, void *arg)
{
    if (g_thread != NULL)
        return -1;
    g_job.fn = fn;
    g_job.arg = arg;
    g_thread = (HANDLE)_beginthreadex(NULL, 0, thread_main, &g_job, 0, NULL);
    return g_thread != NULL ? 0 : -1;
}

void platform_thread_join(void)
{
    if (g_thread == NULL)
        return;
    WaitForSingleObject(g_thread, INFINITE);
    CloseHandle(g_thread);
    g_thread = NULL;
}
#else
static pthread_t g_thread;
static int g_thread_up = 0;

static void *thread_main(void *p)
{
    struct thread_job *j = (struct thread_job *)p;

    j->fn(j->arg);
    return NULL;
}

int platform_thread_start(platform_thread_fn fn, void *arg)
{
    if (g_thread_up)
        return -1;
    g_job.fn = fn;
    g_job.arg = arg;
    if (pthread_create(&g_thread, NULL, thread_main, &g_job) != 0)
        return -1;
    g_thread_up = 1;
    return 0;
}

void platform_thread_join(void)
{
    if (!g_thread_up)
        return;
    pthread_join(g_thread, NULL);
    g_thread_up = 0;
}
#endif
