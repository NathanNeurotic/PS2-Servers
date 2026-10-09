/*
  The interface, served by the client to itself: a loopback listener, the
  page compiled into the executable, JSON and SSE endpoints. One file, no
  runtime, real text layout, and OBS gets a browser source. Loopback
  unless the user opens it to the network; settings posts stay local.
*/
#ifndef XERABORA_WEBUI_H
#define XERABORA_WEBUI_H

#include "platform.h"
#include "rc_client.h"

/* Starts the listener. Returns the socket, or SOCK_INVALID when the
   port is taken -- which is not fatal: the client runs on without an
   interface, the way it always did. */
sock_t webui_start(int port);

/* The port it settled on, which may not be the one asked for. */
int webui_port(void);

/* Open the page to other devices on the network (bind every interface)
   or keep it to this machine. Set before webui_start; a change from
   the page takes effect through webui_rebind. */
void webui_set_lan(int on);
int webui_lan(void);

/* Called from the main loop after webui_serve: when the page switched
   the network setting, the listener is closed and opened again on the
   same port with the new binding. Returns the listener to use next. */
sock_t webui_rebind(sock_t listener);

/* Serve the page from this file instead of the built-in copy: the
   edit-refresh loop for working on the interface (--ui-file). */
void webui_set_ui_file(const char *path);

/* Serves whatever is waiting. Called from the main loop when the
   listener is readable; never blocks longer than one request. */
void webui_serve(sock_t listener, rc_client_t *client);

/* Pushes the state to a page that asked for the live stream. Called
   once per snapshot: the page then moves in step with the game rather
   than in step with a poll timer. Cheap when nobody is listening. */
void webui_push(rc_client_t *client);

void webui_stop(sock_t listener);

/* Opens the page in the browser, in application mode where supported: no
   address bar, no tabs. */
void webui_open_browser(int port);

/* The session state the page shows. The client fills these in as
   things happen; the page polls and renders whatever is here. */
/* Whether the account is logged in, and as whom. The page shows the
   difference between "waiting for a console" and "this client cannot
   talk to RetroAchievements yet". */
void webui_set_login(int ok, const char *user);

/* Whether a Web API key is saved: the page's settings screen shows
   which of the two credentials is still missing. */
void webui_set_webapi(int ok);

/* Something structural changed outside the console path (the followed
   game, its rich presence line): the next push is a full state. */
void webui_mark_dirty(void);

void webui_set_console(const char *ip, int connected);
void webui_set_game(const char *serial, const char *hash, const char *title);

/* The title alone, once rc_client has the set: the serial and the hash
   the console gave stay as they are. */
void webui_set_game_title(const char *title);
void webui_note_unlock(unsigned id, const char *title, const char *badge, unsigned points);

/* Every telemetry datagram, even one that decodes to nothing yet: the
   page can then show a console that is alive before any game is
   identified, which is what the terminal log always showed first. */
void webui_note_packet(void);

/* The console pipeline as one word the page turns into a sentence: "", "no-
   hash", "identifying", "telemetry-only", "active", "stale". Mirrors
   the terminal warnings. */
void webui_set_status(const char *status);

void webui_set_stats(unsigned long frames, unsigned long gaps,
                     unsigned long dupes, unsigned long torn,
                     unsigned parts, unsigned bytes, unsigned addresses);

/* True once a page has been open and none has been heard from for
   `grace_seconds`: the last page was closed. The main loop then exits,
   so a windowless client does not outlive its only window. */
int webui_page_gone(int grace_seconds);

/* Set by POST /quit from the settings screen; the main loop ends the
   same way Ctrl-C does. The page is the only visible surface of a
   windowless process, so it carries the off switch too. */
int webui_quit_requested(void);

/* rc_client's synthetic warning achievements ("Unknown Emulator")
   start at this id; rcheevos keeps them out of its own summaries and
   the state and the unlock toast do the same. */
#define WEBUI_WARNING_ACH_ID 101000001u

/* Stream output: plain text files, one value each, plus data.json. OBS
   reads a text file as a source and follows changes. Written on change, not
   on a timer. */
void webui_set_obs_dir(const char *dir);
void webui_write_obs(rc_client_t *client);

#endif
