# synthetic-cd-1003.chd

A made-up CD, not a real disc. 1003 MODE1/2352 sectors; sector `i` has 2048
user bytes made of `i` as a little-endian 32-bit word repeated 512 times.
Built with `chdman createcd` (0.264) from a one-track CUE/BIN. chdman rounds the
image up to 1004 frames and then to 126 hunks of 8 frames, so a CHD reader that
only counts hunks sees 1008 sectors. The track metadata says 1003.
