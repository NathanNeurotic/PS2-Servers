/*
  Platform shims: sockets, sleeping, config directory, password prompt.
  POSIX and Windows (MinGW) share the rest of the client.
*/
#ifndef XERABORA_PLATFORM_H
#define XERABORA_PLATFORM_H

#include <stddef.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET sock_t;
typedef int socklen_t_compat;
#define SOCK_INVALID INVALID_SOCKET
#define sock_close closesocket
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int sock_t;
#define SOCK_INVALID (-1)
#define sock_close close
#endif

/* Console setup: UTF-8 output on Windows. */
void platform_console_init(void);

/* Windows builds are window-subsystem programs. Attach to the starting
   terminal so text commands print there; `force` (--console) opens a
   console window regardless. */
void platform_attach_console(int force);

/* Installs a Ctrl-C / SIGTERM handler that sets *flag to 1. */
void platform_on_stop(volatile int *flag);

/* One-time socket library setup. Returns 0 on success. */
int platform_net_init(void);
void platform_net_shutdown(void);

void platform_sleep_ms(unsigned int ms);

/* Directory for the credentials file, created if missing:
   %LOCALAPPDATA%\xerabora on Windows, $XDG_CONFIG_HOME/xerabora or
   ~/.config/xerabora elsewhere. Returns 0 on success. */
int platform_config_dir(char *out, size_t size);

/* Reads a line from the terminal without echo. Returns 0 on success. */
int platform_read_password(char *out, size_t size);

/* Waits until the socket is readable. Returns 1 when readable, 0 on
   timeout, -1 on error. */
int platform_wait_readable(sock_t sock, int timeout_ms);

/* Wait on two sockets at once: telemetry and the UI listener. Returns a
   bitmask, 1 for the first, 2 for the second; 0 on timeout, -1 on error. */
int platform_wait_readable2(sock_t a, sock_t b, int timeout_ms);

/* Human-readable text for the last socket error. */
const char *platform_sock_error(void);

/* One worker thread at a time: the identification of an image runs off
   the receive loop. Returns 0 when the thread started. Join waits for
   it and frees the handle. */
typedef void (*platform_thread_fn)(void *arg);
int platform_thread_start(platform_thread_fn fn, void *arg);
void platform_thread_join(void);

#endif
