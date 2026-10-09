/*
  Saved credentials: the RetroAchievements username and login token,
  stored in the user's config directory. The password is never stored.
*/
#ifndef XERABORA_CONFIG_H
#define XERABORA_CONFIG_H

#include <stddef.h>

/* Path of the credentials file. Returns 0 on success. */
int config_credentials_path(char *out, size_t size);

/* Returns 1 when a saved username and token were read. */
int config_load_credentials(char *user, size_t user_size, char *token, size_t token_size);

/* Returns 0 on success. */
int config_save_credentials(const char *user, const char *token);

/* Deletes the saved credentials. */
void config_forget_credentials(void);

/* The Web API key, kept apart from the login token: different door,
   read-only, and the user pastes it from their RA profile settings.
   Returns 1 when a key was read. */
int config_load_apikey(char *key, size_t size);
int config_save_apikey(const char *key);

/* Whether the interface page is open to other devices on the network.
   Off by default: the page carries the settings and the off switch. */
int config_load_lan(void);
int config_save_lan(int on);

/* Whether the client follows the account's play on other devices
   through the Web API. On by default: it costs a key and nothing else. */
int config_load_follow(void);
int config_save_follow(int on);

/* Path of the file with remembered serial-to-hash pairs. */
int config_games_path(char *out, size_t size);

#endif
