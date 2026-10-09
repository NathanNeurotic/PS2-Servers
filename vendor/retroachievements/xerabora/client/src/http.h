/*
  One blocking HTTP POST/GET, enough for the RetroAchievements API.
  Implemented with libcurl on POSIX and WinHTTP on Windows, so the
  Windows binary needs no bundled TLS library.
*/
#ifndef XERABORA_HTTP_H
#define XERABORA_HTTP_H

#include <stddef.h>

struct http_response
{
    char *body;      /* malloc'd, NUL-terminated; caller frees */
    size_t length;
    int status;      /* HTTP status, or -1 when the request never completed */
};

int http_init(void);
void http_shutdown(void);

/* POST when post_data is non-NULL, GET otherwise. Returns 0 when a
   response was received (any status), -1 on transport failure. */
int http_request(const char *url, const char *post_data, const char *content_type,
                 const char *user_agent, struct http_response *out);

#endif
