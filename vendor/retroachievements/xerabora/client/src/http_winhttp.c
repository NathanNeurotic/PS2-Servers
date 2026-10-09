#include "http.h"

#include <windows.h>
#include <winhttp.h>

#ifndef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2
#define WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 0x00000800
#endif
#ifndef WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3
#define WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3 0x00002000
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HINTERNET g_session = NULL;

static wchar_t *to_wide(const char *s)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    wchar_t *w;

    if (n <= 0)
        return NULL;
    w = malloc((size_t)n * sizeof(wchar_t));
    if (w != NULL)
        MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}

int http_init(void)
{
    g_session = WinHttpOpen(L"xerabora", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (g_session == NULL)
        return -1;
    WinHttpSetTimeouts(g_session, 15000, 15000, 30000, 30000);

    /* Older Windows defaults to TLS 1.0, which the server refuses.
       TLS 1.3 is unknown to Windows 7/8, so fall back to 1.2 alone. */
    {
        DWORD protocols = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2 | WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_3;

        if (!WinHttpSetOption(g_session, WINHTTP_OPTION_SECURE_PROTOCOLS, &protocols, sizeof(protocols))) {
            protocols = WINHTTP_FLAG_SECURE_PROTOCOL_TLS1_2;
            WinHttpSetOption(g_session, WINHTTP_OPTION_SECURE_PROTOCOLS, &protocols, sizeof(protocols));
        }
    }
    return 0;
}

void http_shutdown(void)
{
    if (g_session != NULL)
        WinHttpCloseHandle(g_session);
    g_session = NULL;
}

int http_request(const char *url, const char *post_data, const char *content_type,
                 const char *user_agent, struct http_response *out)
{
    URL_COMPONENTS uc;
    wchar_t *wurl = NULL, *wua = NULL, *whdr = NULL;
    wchar_t host[256], path[2048];
    HINTERNET conn = NULL, req = NULL;
    DWORD status = 0, status_len = sizeof(status);
    DWORD flags;
    int rc = -1;

    memset(out, 0, sizeof(*out));
    out->status = -1;

    wurl = to_wide(url);
    if (wurl == NULL || g_session == NULL)
        goto done;

    memset(&uc, 0, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    uc.lpszHostName = host;
    uc.dwHostNameLength = sizeof(host) / sizeof(host[0]);
    uc.lpszUrlPath = path;
    uc.dwUrlPathLength = sizeof(path) / sizeof(path[0]);
    if (!WinHttpCrackUrl(wurl, 0, 0, &uc))
        goto done;

    conn = WinHttpConnect(g_session, host, uc.nPort, 0);
    if (conn == NULL)
        goto done;

    flags = (uc.nScheme == INTERNET_SCHEME_HTTPS) ? WINHTTP_FLAG_SECURE : 0;
    req = WinHttpOpenRequest(conn, post_data ? L"POST" : L"GET", path, NULL,
                             WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (req == NULL)
        goto done;

    if (user_agent != NULL) {
        char line[256];

        snprintf(line, sizeof(line), "User-Agent: %s", user_agent);
        wua = to_wide(line);
        if (wua != NULL)
            WinHttpAddRequestHeaders(req, wua, (DWORD)-1, WINHTTP_ADDREQ_FLAG_ADD);
    }

    if (post_data != NULL) {
        char line[192];

        snprintf(line, sizeof(line), "Content-Type: %s",
                 content_type ? content_type : "application/x-www-form-urlencoded");
        whdr = to_wide(line);
    }

    if (!WinHttpSendRequest(req, whdr ? whdr : WINHTTP_NO_ADDITIONAL_HEADERS, (DWORD)-1,
                            post_data ? (LPVOID)post_data : WINHTTP_NO_REQUEST_DATA,
                            post_data ? (DWORD)strlen(post_data) : 0,
                            post_data ? (DWORD)strlen(post_data) : 0, 0))
        goto done;

    if (!WinHttpReceiveResponse(req, NULL))
        goto done;

    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &status_len,
                        WINHTTP_NO_HEADER_INDEX);
    out->status = (int)status;

    for (;;) {
        DWORD avail = 0, got = 0;
        char *grown;

        if (!WinHttpQueryDataAvailable(req, &avail) || avail == 0)
            break;

        grown = realloc(out->body, out->length + avail + 1);
        if (grown == NULL)
            break;
        out->body = grown;

        if (!WinHttpReadData(req, out->body + out->length, avail, &got))
            break;
        out->length += got;
        out->body[out->length] = '\0';
    }

    rc = 0;

done:
    if (out->body == NULL) {
        out->body = calloc(1, 1);
        out->length = 0;
    }
    if (req)
        WinHttpCloseHandle(req);
    if (conn)
        WinHttpCloseHandle(conn);
    free(wurl);
    free(wua);
    free(whdr);
    return rc;
}
