/*
  Discord rich presence without the SDK: a named pipe (a unix socket
  elsewhere), a four-byte opcode, a four-byte length and JSON. Everything
  here fails quietly; the rest of the program never learns.
*/
#ifndef XERABORA_DISCORD_H
#define XERABORA_DISCORD_H

/* Connects if Discord is there. Safe to call when it is not. */
void discord_init(void);
void discord_shutdown(void);

/* The game, how far along, and the last unlock's badge as the small icon.
   NULL or empty clears. Rate-limited to Discord's one update per fifteen
   seconds. */
void discord_set(const char *game, const char *detail, const char *badge);

/* Called from the main loop; keeps the connection alive and retries a
   failed one now and then. Costs nothing when there is no Discord. */
void discord_tick(void);

#endif
