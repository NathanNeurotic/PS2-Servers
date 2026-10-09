#include "log.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

static int g_trace = 0;
static FILE *g_file = NULL;

void log_to_file(const char *path)
{
    if (g_file != NULL)
        fclose(g_file);
    g_file = fopen(path, "a");
    if (g_file != NULL) {
        time_t now = time(NULL);

        /* Every copy appends to the same file; the pid tells them
           apart when two ran at once. */
#ifdef _WIN32
        unsigned long pid = (unsigned long)GetCurrentProcessId();
#else
        unsigned long pid = (unsigned long)getpid();
#endif
        char *ts = ctime(&now);

        ts[strcspn(ts, "\n")] = '\0';
        fprintf(g_file, "\n---- %s (pid %lu)\n", ts, pid);
        fflush(g_file);
    }
}

void log_set_trace(int on)
{
    g_trace = on;
}

int log_trace_enabled(void)
{
    return g_trace;
}

static void emit_file(const char *level, const char *fmt, va_list ap)
{
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    char stamp[16];

    if (g_file == NULL)
        return;

    strftime(stamp, sizeof(stamp), "%H:%M:%S", t);
    fprintf(g_file, "%s %s", stamp, level);
    vfprintf(g_file, fmt, ap);
    fputc('\n', g_file);
    fflush(g_file);
}

void log_detail(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    emit_file("", fmt, ap);
    va_end(ap);
}

static void emit(const char *level, const char *fmt, va_list ap)
{
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    char stamp[16];

    strftime(stamp, sizeof(stamp), "%H:%M:%S", t);

    if (g_file != NULL) {
        va_list copy;

        va_copy(copy, ap);
        fprintf(g_file, "%s %s", stamp, level);
        vfprintf(g_file, fmt, copy);
        fputc('\n', g_file);
        fflush(g_file);
        va_end(copy);
    }

    printf("%s %s", stamp, level);
    vprintf(fmt, ap);
    putchar('\n');
    fflush(stdout);
}

void log_info(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    emit("", fmt, ap);
    va_end(ap);
}

void log_warn(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    emit("warning: ", fmt, ap);
    va_end(ap);
}

void log_error(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    emit("error: ", fmt, ap);
    va_end(ap);
}

void log_trace(const char *fmt, ...)
{
    va_list ap;

    if (!g_trace)
        return;
    va_start(ap, fmt);
    emit("  ", fmt, ap);
    va_end(ap);
}
