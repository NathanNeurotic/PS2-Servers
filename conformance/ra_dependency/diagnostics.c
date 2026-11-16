#include <assert.h>
#include <stdarg.h>
#include "diag.c"
void log_info(const char *fmt, ...) {(void)fmt;}
void log_warn(const char *fmt, ...) {(void)fmt;}
void log_detail(const char *fmt, ...) {(void)fmt;}
void log_trace(const char *fmt, ...) {(void)fmt;}
const rc_client_game_t *rc_client_get_game_info(const rc_client_t *c) {return &c->game->public_;}
const char *ra_loaded_hash(void) {return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";}
int ra_test_mode(void) {return 1;}
int main(void) {
 rc_client_t client={0};rc_client_game_info_t game={0};rc_client_subset_info_t subset={0};rc_client_achievement_info_t ach={0};
 char report[65536]; unsigned i;FILE *f;
 client.game=&game;game.public_.id=42;game.public_.title="Fixture";game.subsets=&subset;
 subset.achievements=&ach;subset.public_.num_achievements=1;
 ach.public_.id=7;ach.public_.title="Fixture achievement";ach.public_.state=RC_CLIENT_ACHIEVEMENT_STATE_ACTIVE;
 rc_runtime_init(&game.runtime);
 assert(rc_runtime_activate_achievement(&game.runtime,7,"0xH080000=1",NULL,0)==0);
 ach.trigger=rc_runtime_get_achievement(&game.runtime,7);
 assert(watchlist_build(&client));
 for(i=1;i<=4000;i++) {watchlist_values()[0]=(unsigned char)i;diag_record(i,1);}
 assert(g_used==3600 && g_bytes*g_cap<=DIAG_MAX_BYTES);
 f=tmpfile();assert(f);assert(diag_write(&client,f)==0);rewind(f);i=fread(report,1,sizeof(report)-1,f);report[i]=0;fclose(f);
 assert(strstr(report,"3600 snapshots, #401 to #4000"));assert(strstr(report,"Fixture achievement"));assert(strstr(report,"0x00080000/1"));
 /* Equal-sized semantic changes must clear the old generation. */
 rc_runtime_destroy(&game.runtime);rc_runtime_init(&game.runtime);
 assert(rc_runtime_activate_achievement(&game.runtime,7,"0xH080001=1",NULL,0)==0);
 ach.trigger=rc_runtime_get_achievement(&game.runtime,7);assert(watchlist_build(&client));
 f=tmpfile();assert(diag_write(&client,f)==0);rewind(f);i=fread(report,1,sizeof(report)-1,f);report[i]=0;fclose(f);
 assert(g_used==0 && strstr(report,"no snapshots recorded"));
 f=tmpfile();g_written=DIAG_REPORT_LIMIT-130;g_truncated=0;report_printf(f,"this must be truncated");assert(g_truncated);assert(ftell(f)<128);fclose(f);
 rc_runtime_destroy(&game.runtime);return 0;
}
