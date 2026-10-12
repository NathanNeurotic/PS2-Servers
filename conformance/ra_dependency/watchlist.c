#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include "rc_client_internal.h"
#include "watchlist.h"
void log_info(const char *fmt, ...) {(void)fmt;}
void log_warn(const char *fmt, ...) {(void)fmt;}
void log_detail(const char *fmt, ...) {(void)fmt;}
void log_trace(const char *fmt, ...) {(void)fmt;}
const rc_client_game_t *rc_client_get_game_info(const rc_client_t *client) { return &client->game->public_; }
int main(void) {
  const char *cases[] = {
    "0xH001000=1_0x001002=2_0xX001004=3",
    "0xL001000=1_0xU001000=2_0xM001001=1",
    "d0xH001000=1_p0xH001001=2",
    "A:0xH001000_A:0xH001001_0xH001002=3",
    "I:0xX001000_0xH000004=1",
    "I:0xX001000_I:0xX000004_0xH000008=1"
  };
  unsigned i,j;
  for(i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
    rc_client_t client = {0}; rc_client_game_info_t game = {0};
    unsigned char wire[20000]; int size;
    client.game=&game; rc_runtime_init(&game.runtime);
    if(rc_runtime_activate_achievement(&game.runtime,1,cases[i],NULL,0)!=RC_OK) return 2;
    if(!watchlist_build(&client)) return 3;
    size=watchlist_serialize(wire,sizeof(wire)); if(!size) return 4;
    printf("%u %d %d %d ",i,watchlist_count(),watchlist_node_count(),watchlist_snapshot_bytes());
    for(j=0;j<(unsigned)size;j++)printf("%02x",wire[j]);puts("");
    rc_runtime_destroy(&game.runtime);
  }
  return 0;
}
