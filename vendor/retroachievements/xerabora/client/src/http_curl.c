#include "http.h"

#include <curl/curl.h>
#include <stdlib.h>
#include <string.h>

static size_t on_data(void *ptr, size_t size, size_t nmemb, void *ud)
{
    struct http_response *r = (struct http_response *)ud;
    size_t add = size * nmemb;
    char *grown = realloc(r->body, r->length + add + 1);

    if (grown == NULL)
        return 0;

    memcpy(grown + r->length, ptr, add);
    r->body = grown;
    r->length += add;
    r->body[r->length] = '\0';
    return add;
}

int http_init(void)
{
    return curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK ? 0 : -1;
}

void http_shutdown(void)
{
    curl_global_cleanup();
}

int http_request(const char *url, const char *post_data, const char *content_type,
                 const char *user_agent, struct http_response *out)
{
    CURL *curl = curl_easy_init();
    struct curl_slist *headers = NULL;
    long code = 0;
    int rc = -1;

    memset(out, 0, sizeof(*out));
    out->status = -1;

    if (curl == NULL)
        return -1;

    curl_easy_setopt(curl, CURLOPT_URL, url);
    if (post_data != NULL) {
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_data);
        if (content_type != NULL) {
            char line[128];

            snprintf(line, sizeof(line), "Content-Type: %s", content_type);
            headers = curl_slist_append(headers, line);
            curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        }
    }
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, on_data);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, out);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, user_agent);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);

    if (curl_easy_perform(curl) == CURLE_OK) {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &code);
        out->status = (int)code;
        rc = 0;
    }

    if (out->body == NULL) {
        out->body = calloc(1, 1);
        out->length = 0;
    }

    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    return rc;
}
