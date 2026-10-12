# Native watch-list dependency conformance

Run `python conformance/ra_dependency/check_watchlist.py` with GCC or Clang
available (`RA_CC` selects the compiler). The command builds the production
xeRAbora watch-list implementation against the current vendored rcheevos.

`watchlist-baseline.txt` was generated using the same unchanged client against
rcheevos `f1417fbf2236936e8334533628b2948dfa4a46d0`. Each row records a case
index, direct count, node count, snapshot byte count and serialized list hex.
Cases cover direct widths, bitfields, delta/prior reads, arithmetic, a pointer
and a nested pointer. Compare the bytes before changing the golden fixture;
do not regenerate it merely to make an upgrade pass.

The harness constructs a synthetic local game/runtime and stubs only logging
and the game-info accessor. It exercises the real runtime parser and real
watch-list builder/serializer. It uses no account, HTTP request or unlock.
These cases establish representative host wire stability, not compatibility
with every achievement set or hardware acceptance.
