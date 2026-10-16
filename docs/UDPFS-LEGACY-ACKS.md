# Legacy Neutrino ACK compatibility

Official Neutrino 1.8.0 and the current official development client accept old
ACK numbers as if they acknowledged the current request. A lost request plus
delayed reply DATA/ACK packets can therefore strand the client waiting for a
reply to a request the server never received. Local actual-source replay produces
the six NACKs for sequence 653 seen at the end of the Tekken Tag report. This is
a reproduced mechanism consistent with the log, not console confirmation.

The server now sends request ACKs separately and omits the ACK bit from reply
payloads, including retransmissions and single-packet RPC replies. A delayed
reply can no longer falsely acknowledge the next request. The payload/FIN,
sequence numbers, read bytes, pacing and cumulative receive ACKs are unchanged.
UDPRDMA permits payloads without a piggybacked ACK; the inspected Neutrino,
RiptOPL and independent udpfsd receive paths handle them.

If the separate request ACK is lost, either send wait re-ACKs a duplicate
request without repeating file I/O, consuming a reply sequence number or
pushing the duplicate back ahead of queued window/FIN ACKs. If that control ACK is lost, recovery now waits for the client's real send
timeout (500 ms in the inspected clients) before the retried request is ACKed;
reply data no longer provides the redundant ACK shortcut. Both changes are
needed: omitting piggyback ACKs alone could otherwise stall a real retry.

The compatibility replay compiles the actual stable/development Neutrino C
send/receive/interrupt code and uses the real Python request dispatcher and
reply sender from the parent and patched server. It injects lost requests,
six delayed previous reply packets, a lost separate request ACK, and sequence
wraparound. File bytes, IOP events, alarms, FIFO and the network are host
fixtures. It verifies exact 16 KiB results and one accepted request per read.
Both bulk reads and single-packet RPC replies are replayed, including loss of
the separate request ACK. A third matrix retains the standalone-ACK limitation.
The test uses a fixed accelerated alarm deadline that packet traffic cannot
restart. Kernel/PS2 behavior is not simulated fully.

This workaround does not repair the client's ACK comparison. A delayed
standalone ACK can still incorrectly acknowledge a lost request. The separate
control-ACK replay deliberately retains that failure as an expected outcome
for unmodified clients, and verifies recovery with the repaired client at
NathanNeurotic/neutrino commit e6f139240e3e64beb34807d2e9ba90a9def80ae6.
No files, PRs, issues or comments are submitted to official Neutrino upstream.

The last working server build reported by the tester was deleted and its
version remains unknown. A particular server or RiptOPL handoff regression
range is not established. Physical-console acceptance remains pending; do not
send an unchanged build as if it exercises this workaround.
