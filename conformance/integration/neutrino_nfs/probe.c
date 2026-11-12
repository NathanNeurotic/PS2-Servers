/*
 * Host probe for Neutrino's NFS backing store against nfs4_server.
 *
 * The NFS client is unfs4, built by run.py from the pinned upstream files with
 * the buffer size Neutrino's iop/nfs/Makefile (PR 240) uses, UNFS4_SIZE=512,
 * on BSD sockets instead of the PS2's tiny TCP stack. That stack's other limit,
 * the 2112-byte reassembly buffer for a whole reply, is enforced by a check
 * run.py splices into unfs4_rpc().
 *
 * main() makes the calls Neutrino's nfs modules make: mount the export, open a
 * game read-only, take its size, then read it the way nfs_pread() does -- at
 * most 2048 bytes per READ, a result of 0, below 0 or above what was asked is a
 * failure, and a short result ends the request.
 *
 * Usage: probe EXPORT PATH OUTFILE [TAIL]
 *   With TAIL, only the last TAIL bytes are read (for a file past 4 GiB).
 * The port comes from UNFS4_TEST_PORT. Prints one status line; the exit code
 * says which step failed.
 */
#include <arpa/inet.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "unfs4.h"

static struct unfs4_context *nfs;

/* At most 2048 bytes per READ, as Neutrino's tiny TCP stack can reassemble. */
static int neutrino_pread(uint8_t *fh, uint8_t *buf, int count, uint64_t offset)
{
        int done, n, rc;

        for (done = 0; done < count; done += rc) {
                n = count - done > 2048 ? 2048 : count - done;
                rc = unfs4_pread(nfs, fh, buf + done, n, offset + done);
                if (rc <= 0 || rc > n) {
                        return done ? done : -1;
                }
                if (rc < n) {
                        return done + rc;
                }
        }
        return done;
}

int main(int argc, char *argv[])
{
        /* Request sizes the loader produces: whole sectors and odd tails. */
        static const int sizes[] = { 2048, 16 * 2048, 4096 + 7, 1, 2047, 2049, 32768 };
        static uint8_t buf[64 * 1024];
        uint8_t *fh;
        int64_t size, pos;
        FILE *out;
        int i = 0, rc;

        if (argc != 4 && argc != 5) {
                fprintf(stderr, "usage: probe EXPORT PATH OUTFILE [TAIL]\n");
                return 2;
        }
        nfs = unfs4_init_context(htonl(0x7f000001), 0, 0);
        if (nfs == NULL) {
                printf("CONNECT_FAILED\n");
                return 3;
        }
        nfs->xid = 0x5053320a;
        if (unfs4_mount(nfs, argv[1])) {
                printf("MOUNT_FAILED status=%u\n", (unsigned)nfs->status);
                return 4;
        }
        fh = unfs4_open(nfs, argv[2], O_RDONLY);
        if (fh == NULL) {
                printf("OPEN_FAILED status=%u\n", (unsigned)nfs->status);
                return 5;
        }
        size = unfs4_size(nfs, fh);
        if (size < 0) {
                printf("SIZE_FAILED status=%u\n", (unsigned)nfs->status);
                return 6;
        }
        out = fopen(argv[3], "wb");
        if (out == NULL) {
                return 2;
        }
        pos = 0;
        if (argc == 5 && size > atoll(argv[4])) {
                pos = size - atoll(argv[4]);
        }
        for (; pos < size; i++) {
                int n = sizes[i % (sizeof(sizes) / sizeof(sizes[0]))];
                if (n > size - pos) {
                        n = (int)(size - pos);
                }
                rc = neutrino_pread(fh, buf, n, (uint64_t)pos);
                if (rc <= 0) {
                        printf("READ_FAILED at %lld status=%u\n", (long long)pos,
                               (unsigned)nfs->status);
                        fclose(out);
                        return 7;
                }
                fwrite(buf, 1, rc, out);
                pos += rc;
        }
        fclose(out);

        /* A read that starts at the end returns nothing rather than looping. */
        rc = unfs4_pread(nfs, fh, buf, 2048, (uint64_t)size);
        if (rc != 0) {
                printf("READ_PAST_END_RETURNED %d\n", rc);
                return 8;
        }
        unfs4_close(nfs, fh);
        printf("OK %lld\n", (long long)size);
        return 0;
}
