/*
  Wire protocol between the console (OPL fork) and this client.
  Everything is UDP. Text requests are ASCII; snapshot values are raw.

  Console -> PC, port 18194:

    RAP1 <console-ip> <port>          discovery, sent from inside the game
    RAQ1 <hash> <serial> <ip> <port>  "do you know this image?", from the menu
    RAG1 <hash> <index> <ip> <port>   "send chunk <index> of the watch list"
    RA15 ... <values>                 telemetry: one snapshot part per packet

  PC -> console, to the address given in the request:

    RAO1 OK <client>/<version>        discovery reply
    RAA1 OK <bytes> <chunks> <achievements> <title>
                                      image known, watch list ready; the
                                      last two fields are optional extras
    RAA1 WAIT                         still asking the RA server, retry
    RAA1 NO                           RA server does not know the image
    RAC1 <index> <length> <data>      one chunk of the watch list

  The console states its own address because the client may run behind
  NAT (a container), where the datagram's source address is rewritten.

  Replies are padded to a multiple of 64 bytes and at least 128 bytes.
  The console receives through ps2sdk's RPC layer, which delivers short
  or unaligned tails by a path that on real hardware delivered zeros;
  padding keeps every reply on the direct DMA path. A chunk of 896 bytes
  plus header and padding stays under the 960-byte ceiling of one
  receive on the console.

  Telemetry packet header (fixed-width fields, see raudp.c in the OPL
  fork). Fields this client reads: sq, vb, n, pt, np, id. The values
  start 4 + 15 + 1 bytes after " id=".
*/
#ifndef XERABORA_PROTOCOL_H
#define XERABORA_PROTOCOL_H

#include "ra_snap.h"
#include "ra_watch.h"

#define XERABORA_DEFAULT_PORT 18194

/* The interface page, on loopback only. Away from the console ports so
   a browser reload can never look like a console talking. */
#define XERABORA_UI_PORT 18280
#define XERABORA_CHUNK 896
#define XERABORA_REPLY_ALIGN 64
#define XERABORA_REPLY_MIN 128

/* The unlock badge pushed to the console: 64x64 PSMCT16 pixels, 8 KB,
   in "RAB1 <idx> <total> " datagrams of 512 raw bytes each. */
#define XERABORA_BADGE_BYTES 8192
#define XERABORA_BADGE_CHUNK 512
#define XERABORA_BADGE_CHUNKS (XERABORA_BADGE_BYTES / XERABORA_BADGE_CHUNK)

#endif
