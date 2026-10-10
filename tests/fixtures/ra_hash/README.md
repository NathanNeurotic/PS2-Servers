# Synthetic RetroAchievements disc

`synthetic-dvd.chd` contains only the generated ISO9660 layout, SYSTEM.CNF,
and synthetic ELF bytes from `fixture()` in `tests/test_ra_compressed_hash.py`.
It contains no commercial game data.

Generated with official MAME 0.289 chdman:

```
chdman createdvd -i Synthetic.iso -o synthetic-dvd.chd
```

The MAME Windows archive SHA256 was checked against its published SHA256SUMS:
`a1aa7912168c9d1b05e611906bc21b8b9be3935822aead36d12a1da363150b7d`.
The 49,152-byte ISO hash is `4c3d78a0558915d533693a8283fab3a2`, independently
computed as MD5 of the BOOT2 filename followed by its synthetic ELF bytes and
confirmed with the pinned native rcheevos engine. The CHD is decoded using the
actual pinned libchdr; no logical-reader substitution is used in its test.
