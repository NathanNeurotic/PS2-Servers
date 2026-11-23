#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include "rc_client_internal.h"
#include "watchlist.h"
#include "snapshot.h"
void log_info(const char *fmt, ...) {(void)fmt;}
void log_warn(const char *fmt, ...) {(void)fmt;}
void log_detail(const char *fmt, ...) {(void)fmt;}
void log_trace(const char *fmt, ...) {(void)fmt;}
const rc_client_game_t *rc_client_get_game_info(const rc_client_t *client) { return &client->game->public_; }
static int feed(const unsigned char *values, unsigned bytes, unsigned count, unsigned sequence) {
  char packet[512], size_field[5];
  int header=sprintf(packet,
    "RA15 seq=000000 sz=0000 us=00000 mx=00000 rxq=000 fail=000000 "
    "err=+000 sk=000000 lk=0000 sq=%06u ds=000000 bad=0000 "
    "n=%04u vb=%04u pt=0 np=1 rc=0000000 fc=0000000 id=SLUS_213.76~~~~ ",
    sequence,count,bytes);
  sprintf(size_field,"%04u",header+bytes);memcpy(packet+19,size_field,4);
  memcpy(packet+header,values,bytes);packet[header+bytes]=0;
  return snapshot_feed(packet,header+bytes);
}
int main(void) {
#ifdef V2_WATCHLIST
  watchlist_set_format(1);
#endif
  const char *cases[] = {
    "0xH001000=1_0x001002=2_0xX001004=3",
    "0xL001000=1_0xU001000=2_0xM001001=1",
    "d0xH001000=1_p0xH001001=2",
    "A:0xH001000_A:0xH001001_0xH001002=3",
    "I:0xX001000_0xH000004=1",
    "I:0xX001000_I:0xX000004_0xH000008=1"
#ifdef V2_WATCHLIST
    , "I:0xH001000*4_0xH080000=1"
    , "I:0xU001000*4_0xH080000=1"
    , "I:0xM001000*4_0xH080000=1"
    , "I:4_0xH080000=1"
#endif
  };
  unsigned i,j;
  for(i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
    rc_client_t client = {0}; rc_client_game_info_t game = {0};
    unsigned char wire[20000]; int size;
    client.game=&game; rc_runtime_init(&game.runtime);
    if(rc_runtime_activate_achievement(&game.runtime,1,cases[i],NULL,0)!=RC_OK) return 2;
    if(!watchlist_build(&client)) return 3;
    if(i==0) {
      char packet[512], serial[16], size_field[5];
      const unsigned char values[7]={0x7f,0x34,0x12,0x78,0x56,0x34,0x12};
      unsigned char readback[4];
      int header=sprintf(packet,
        "RA15 seq=000000 sz=0000 us=00000 mx=00000 rxq=000 fail=000000 "
        "err=+000 sk=000000 lk=0000 sq=000001 ds=000000 bad=0000 "
        "n=0003 vb=0007 pt=0 np=1 rc=0000000 fc=0000000 id=SLUS_213.76~~~~ ");
      sprintf(size_field,"%04u",(unsigned)(header+sizeof(values)));
      memcpy(packet+19,size_field,4);
      memcpy(packet+header,values,sizeof(values));packet[header+sizeof(values)]=0;
      snapshot_reset();
      if(!snapshot_serial(packet,serial,sizeof(serial)) || strcmp(serial,"SLUS_213.76")) return 5;
      if(!snapshot_feed(packet,header+sizeof(values))) return 6;
      if(watchlist_read_memory(0x1004,readback,4,&client)!=4 || memcmp(readback,values+3,4)) return 7;
      if(snapshot_feed(packet,header+sizeof(values)) || snapshot_stats()->dupes!=1) return 8;
    }
    size=watchlist_serialize(wire,sizeof(wire),NULL); if(!size) return 4;
    if(i==4 || i==5) {
      const unsigned char values[]={0x00,0x20,0,0, 0x04,0x20,0,0, 0x00,0x30,0,0,
                                   0x08,0x30,0,0, 1,0,0,0};
      unsigned char readback=0;
      snapshot_reset();
      if(feed(values,4,1,1) || !snapshot_stale()) return 9;
      if(!feed(values,watchlist_snapshot_bytes(),1,2)) return 10;
      if(watchlist_read_memory(i==4 ? 0x2004 : 0x3008,&readback,1,&client)!=1 ||
         readback!=(i==4 ? 0 : 1)) return 11;
      if(feed(values,4,1,3) || !snapshot_stale()) return 12;
    }
#ifdef V2_WATCHLIST
    if(i>=6 && i<=8) {
      unsigned address=0x80000 + 4*(i==6 ? 0x31 : i==7 ? 3 : 1);
      unsigned char values[9]={0x31}, result=0;
      values[1]=(unsigned char)address; values[2]=(unsigned char)(address>>8);
      values[3]=(unsigned char)(address>>16); values[4]=(unsigned char)(address>>24);
      values[5]=1;snapshot_reset();
      if(!feed(values,9,1,1) || watchlist_read_memory(address,&result,1,&client)!=1 || result!=1) return 13;
    }
#endif
    printf("%u %d %d %d ",i,watchlist_count(),watchlist_node_count(),watchlist_snapshot_bytes());
    for(j=0;j<(unsigned)size;j++)printf("%02x",wire[j]);puts("");
    rc_runtime_destroy(&game.runtime);
  }
  return 0;
}
