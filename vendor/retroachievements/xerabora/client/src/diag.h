/* Bounded local diagnostics; xeRAbora v2.0.0 adapted under MIT. */
#ifndef PS2SERVERS_RA_DIAG_H
#define PS2SERVERS_RA_DIAG_H
#include <stdio.h>
#include "rc_client.h"
void diag_reset(void);
void diag_record(unsigned int seq, unsigned int bytes);
int diag_write(rc_client_t *client, FILE *out);
#endif
