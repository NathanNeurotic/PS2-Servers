#!/usr/bin/env python3
"""Self-test for the UDPBD Python port.

Spins up the server on loopback against a scratch image and exercises the wire
protocol end-to-end: INFO reply, READ (reassembled RDMA must equal the file
across every block-size regime), WRITE (must land in the file), and the RDMA
block-size optimizer. No PlayStation 2 required.

Final validation is still on real hardware (an actual PS2 running OPL, or PCSX2
with a network adapter) -- this only proves the wire protocol is correct.

    python selftest.py
"""

import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time

import udpbd_server as U

SRV = ("127.0.0.1", U.UDPBD_PORT)


def _client():
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(3.0)
    return c


def _info(c, expected_sectors):
    c.sendto(U.pack_header(U.CMD_INFO, 1, 0), SRV)
    data, _ = c.recvfrom(2048)
    cmd, _, _ = U.unpack_header(data)
    _, ssize, scount = struct.unpack("<HII", data[:10])
    assert cmd == U.CMD_INFO_REPLY and ssize == 512 and scount == expected_sectors
    print("  INFO  ok: sector_size=512 sector_count={}".format(scount))


def _read(c, img, start, count):
    c.sendto(U.pack_header(U.CMD_READ, 2, 0) + struct.pack("<IH", start, count), SRV)
    want = count * 512
    got = bytearray()
    while len(got) < want:
        data, _ = c.recvfrom(2048)
        cmd, _, _ = U.unpack_header(data)
        assert cmd == U.CMD_READ_RDMA
        bshift, bcount = U.unpack_block_type(data)
        got += data[6:6 + bcount * (1 << (bshift + 2))]
    assert bytes(got[:want]) == img[start * 512:(start + count) * 512]
    print("  READ  ok: start={:<6} count={:<4} matches file".format(start, count))


def _write(c, path, start, count):
    new = random.Random(99).randbytes(count * 512)
    c.sendto(U.pack_header(U.CMD_WRITE, 3, 0) + struct.pack("<IH", start, count), SRV)
    off, blocks = 0, count
    while blocks > 0:  # block_shift 7 => 1 block == 1 sector, 2 sectors per packet
        bc = min(blocks, 2)
        blocks -= bc
        c.sendto(U.pack_header(U.CMD_WRITE_RDMA, 3, 0) + U.pack_block_type(7, bc)
                 + new[off:off + bc * 512], SRV)
        off += bc * 512
    data, _ = c.recvfrom(2048)
    assert U.unpack_header(data)[0] == U.CMD_WRITE_DONE
    time.sleep(0.1)
    with open(path, "rb") as f:
        f.seek(start * 512)
        assert f.read(count * 512) == new
    print("  WRITE ok: start={:<6} count={:<4} written and verified".format(start, count))


def _optimizer():
    s = U.UdpbdServer.__new__(U.UdpbdServer)
    s.verbose = False
    s._block_shift = None
    s._set_block_shift(5)
    for sectors, expected_block in {1: 512, 8: 128, 64: 32}.items():
        s._set_block_shift_for_sectors(sectors)
        assert s._block_size == expected_block, (sectors, s._block_size)
    print("  SHIFT ok: block-size optimizer matches upstream table")


def _fuzz(c, expected_sectors):
    # malformed / short packets (fuzzing, port scans) must never crash the server
    for bad in (b"\x00", b"\x02", b"\x02\x00\x00", b"\x05\x00\x00\x00\x00",
                random.Random(7).randbytes(5), b"\x04\x00\x01\x00\x00\x00\x00"):
        c.sendto(bad, SRV)
    _info(c, expected_sectors)  # still answers -> it survived the garbage
    print("  FUZZ  ok: server survived malformed packets")


def main():
    with tempfile.TemporaryDirectory(prefix="udpbd_") as tmp:
        path = os.path.join(tmp, "test.img")
        size = 4 * 1024 * 1024
        img = random.Random(1234).randbytes(size)
        with open(path, "wb") as f:
            f.write(img)

        server = U.UdpbdServer(U.BlockDevice(path), bind="127.0.0.1")
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        time.sleep(0.3)

        c = _client()
        try:
            print("UDPBD port self-test:")
            _info(c, size // 512)
            _read(c, img, 0, 1)        # smallest read  -> 512-byte blocks
            _read(c, img, 100, 8)      # 8 sectors      -> 128-byte blocks
            _read(c, img, 1000, 512)   # max read       -> 32-byte blocks (~183 packets)
            _read(c, img, 7777, 17)    # odd offset/count
            _write(c, path, 200, 5)
            with open(path, "rb") as f:  # the written region must read back as new data
                img2 = f.read()
            _read(c, img2, 200, 5)
            _fuzz(c, size // 512)
            _optimizer()
            print("ALL UDPBD TESTS PASSED")
        finally:
            c.close()
            server.sock.close()       # unblocks recvfrom -> run() closes the image
            thread.join(timeout=1.0)  # ensure the image handle is freed before cleanup
    return 0


if __name__ == "__main__":
    sys.exit(main())
