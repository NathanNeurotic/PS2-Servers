#include <stdio.h>
#include <stdint.h>
#include <string.h>

int ps2servers_build_watchlist(const char *const *definitions, uint32_t num_definitions,
                               uint32_t memory_low, uint32_t memory_high,
                               uint8_t *output, uint32_t capacity);

int main(void) {
  const char *cases[] = {
    "0xH001000=1_0x001002=2_0xX001004=3",
    "0xL001000=1_0xU001000=2_0xM001001=1",
    "d0xH001000=1_p0xH001001=2",
    "A:0xH001000_A:0xH001001_0xH001002=3",
    "I:0xX001000_0xH000004=1",
    "I:0xX001000_I:0xX000004_0xH000008=1"
  };
  unsigned i, j;
  for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
    uint8_t wire[20000];
    const char *def = cases[i];
    int size = ps2servers_build_watchlist(&def, 1, 0, 0x2000000, wire, sizeof(wire));
    if (size <= 0) return 1;
    uint32_t count = *(uint32_t *)(wire + 8);
    uint32_t direct_bytes = *(uint32_t *)(wire + 12);
    uint32_t node_count = 0;
    if ((size_t)size > 16 + count * 4) {
      node_count = *(uint32_t *)(wire + 16 + count * 4 + 4);
    }
    uint32_t snapshot_bytes = direct_bytes + node_count * 8;
    printf("%u %u %u %u ", i, count, node_count, snapshot_bytes);
    for (j = 0; j < (unsigned)size; j++) printf("%02x", wire[j]);
    puts("");
  }
  return 0;
}
