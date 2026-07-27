#!/usr/bin/env python3
"""Socket-level UDPBD probe for the Python UDPBD server or PS2 Servers Edge.

Speaks the UDPBD v2 wire protocol against a already-running server and checks
the externally observable behaviour both implementations must share: the INFO
reply, RDMA reads reassembling to exactly the image bytes across every
block-size regime, writes landing in the image, and the three abuse cases that
must not corrupt anything (fuzz, truncated RDMA, unsolicited RDMA).

Deliberately defines its own packing rather than importing udpbd_server: a probe
that shares its encoder with one implementation cannot catch that
implementation's encoding bugs.

The block-size optimizer and the simulated-write-error path stay in
udpbd_server/selftest.py, because both need in-process access to the Python
server and neither is observable from the wire.

    python udpbd_probe.py --image /path/to/test.img [--host H] [--port P]
"""
import argparse
import socket
import struct
import sys
from pathlib import Path

CMD_INFO, CMD_INFO_REPLY = 0x00, 0x01
CMD_READ, CMD_READ_RDMA = 0x02, 0x03
CMD_WRITE, CMD_WRITE_RDMA, CMD_WRITE_DONE = 0x04, 0x05, 0x06
SECTOR_SIZE = 512


def pack_header(cmd, cmdid, cmdpkt):
    return struct.pack("<H", (cmd & 0x1F) | ((cmdid & 0x07) << 5) | ((cmdpkt & 0xFF) << 8))


def unpack_header(datagram):
    value = struct.unpack_from("<H", datagram, 0)[0]
    return value & 0x1F, (value >> 5) & 0x07, (value >> 8) & 0xFF


def pack_block_type(block_shift, block_count):
    return struct.pack("<I", (block_shift & 0x0F) | ((block_count & 0x1FF) << 4))


def unpack_block_type(datagram, offset=2):
    value = struct.unpack_from("<I", datagram, offset)[0]
    return value & 0x0F, (value >> 4) & 0x1FF


class Probe:
    def __init__(self, host, port, timeout=5.0):
        self.srv = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def close(self):
        self.sock.close()

    def send(self, payload):
        self.sock.sendto(payload, self.srv)

    def info(self, expected_sectors):
        self.send(pack_header(CMD_INFO, 0, 0) + b"\0" * 6)
        data, _ = self.sock.recvfrom(2048)
        cmd, _, _ = unpack_header(data)
        if cmd != CMD_INFO_REPLY:
            raise AssertionError(f"INFO replied cmd 0x{cmd:02x}, expected INFO_REPLY")
        sector_size, sector_count = struct.unpack_from("<II", data, 2)
        if sector_size != SECTOR_SIZE:
            raise AssertionError(f"INFO sector_size={sector_size}, expected {SECTOR_SIZE}")
        if sector_count != expected_sectors:
            raise AssertionError(
                f"INFO sector_count={sector_count}, expected {expected_sectors}")

    def read(self, image, start, count, cmdid=1):
        """Read sectors and require the reassembled RDMA stream to equal the image."""
        self.send(pack_header(CMD_READ, cmdid, 0) + struct.pack("<IH", start, count))
        want = count * SECTOR_SIZE
        got = bytearray()
        while len(got) < want:
            data, _ = self.sock.recvfrom(2048)
            cmd, rid, _ = unpack_header(data)
            if cmd != CMD_READ_RDMA:
                raise AssertionError(f"expected READ_RDMA, got 0x{cmd:02x}")
            if rid != cmdid:
                raise AssertionError(f"READ_RDMA cmdid={rid}, expected {cmdid}")
            shift, blocks = unpack_block_type(data)
            size = blocks * (1 << (shift + 2))
            if len(data) < 6 + size:
                raise AssertionError("READ_RDMA payload shorter than its block_type")
            got += data[6:6 + size]
        expect = image[start * SECTOR_SIZE:(start + count) * SECTOR_SIZE]
        if bytes(got[:want]) != expect:
            raise AssertionError(
                f"READ sectors {start}+{count} did not match the image")

    def write(self, path, start, count, cmdid=2):
        """Write a recognisable pattern and require it to land in the file."""
        payload = bytes(((start + i) & 0xFF) for i in range(count * SECTOR_SIZE))
        self.send(pack_header(CMD_WRITE, cmdid, 0) + struct.pack("<IH", start, count))
        # 512-byte blocks (shift 7); two sectors per RDMA keeps packets legal.
        per = 2
        offset = 0
        while offset < len(payload):
            chunk = payload[offset:offset + per * SECTOR_SIZE]
            blocks = len(chunk) // 512
            self.send(pack_header(CMD_WRITE_RDMA, cmdid, 0)
                      + pack_block_type(7, blocks) + chunk)
            offset += len(chunk)
        data, _ = self.sock.recvfrom(2048)
        cmd, _, _ = unpack_header(data)
        result = struct.unpack_from("<i", data, 2)[0]
        if cmd != CMD_WRITE_DONE:
            raise AssertionError(f"expected WRITE_DONE, got 0x{cmd:02x}")
        if result != 0:
            raise AssertionError(f"WRITE_DONE result={result}, expected 0")
        on_disk = Path(path).read_bytes()
        landed = on_disk[start * SECTOR_SIZE:(start + count) * SECTOR_SIZE]
        if landed != payload:
            raise AssertionError(f"write to sectors {start}+{count} did not land")
        return payload

    def fuzz(self, expected_sectors):
        """Malformed and short datagrams must not take the server down."""
        for junk in (b"", b"\x00", b"\xff" * 3, b"\x02" + b"\x00" * 3,
                     pack_header(CMD_READ, 0, 0) + b"\x01",
                     pack_header(0x1F, 7, 255) + b"\xAA" * 32):
            self.send(junk)
        self.info(expected_sectors)  # still answering -> it survived

    def truncated_rdma(self, image, start):
        """An RDMA shorter than its block_type must be dropped, not written."""
        self.send(pack_header(CMD_WRITE, 5, 0) + struct.pack("<IH", start, 1))
        self.send(pack_header(CMD_WRITE_RDMA, 5, 0) + pack_block_type(7, 1) + b"\xEE" * 100)
        self.read(image, start, 1, cmdid=5)  # region unchanged

    def unsolicited_rdma(self, image, start):
        """An RDMA with no CMD_WRITE in front of it must be ignored."""
        self.send(pack_header(CMD_WRITE_RDMA, 6, 0) + pack_block_type(7, 1) + b"\xCD" * 512)
        self.read(image, start, 2, cmdid=6)  # region unchanged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0xBDBD)
    ap.add_argument("--image", required=True,
                    help="the same image file the server was pointed at")
    args = ap.parse_args()

    path = Path(args.image).resolve()
    image = path.read_bytes()
    sectors = len(image) // SECTOR_SIZE
    if sectors < 2048:
        raise SystemExit("image must be at least 1 MiB so every block regime is covered")

    p = Probe(args.host, args.port)
    try:
        p.info(sectors)
        # Each count lands in a different block-size regime of the optimizer.
        p.read(image, 0, 1)      # 512-byte blocks
        p.read(image, 100, 8)    # 128-byte blocks
        p.read(image, 1000, 512) # 32-byte blocks, many packets
        p.read(image, 777, 17)   # odd offset and count
        p.unsolicited_rdma(image, 600)
        p.truncated_rdma(image, 400)
        p.write(str(path), 200, 5)
        image = path.read_bytes()  # re-read: the write changed it
        p.read(image, 200, 5)      # written region reads back as the new data
        p.fuzz(sectors)
    except Exception as exc:
        print(f"UDPBD conformance FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        p.close()
    print("UDPBD open/read/write byte conformance passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
