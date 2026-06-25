#!/usr/bin/env python3
"""UDPBD server -- pure-Python port.

Serves a disk image (or block device) to a PlayStation 2 running Open PS2 Loader
over the UDPBD v2 protocol. The PS2 discovers the server automatically via a
broadcast on UDP port 0xBDBD -- no IP or port needs to be entered in OPL.

This is an independent reimplementation written from the published protocol
(struct layout in `udpbd.h`, behaviour in `main.cpp`). The original UDPBD server
and protocol are by **Rick Gaiser** (https://github.com/israpps/udpbd-server,
brought to GitHub by El_isra, Windows port by Alex Parrado). No upstream code is
copied here; only the wire protocol is reproduced so PS2 clients interoperate.

Standard library only -- runs identically on Windows, Linux and macOS, which is
why it replaces the Windows-only prebuilt binary in this repo.
"""

import argparse
import os
import socket
import struct
import sys

# --------------------------------------------------------------------------- #
# Protocol constants (from udpbd.h)
# --------------------------------------------------------------------------- #
UDPBD_PORT = 0xBDBD  # 48573 -- fixed; the PS2 broadcasts here to find us

CMD_INFO = 0x00        # client -> server
CMD_INFO_REPLY = 0x01  # server -> client
CMD_READ = 0x02        # client -> server
CMD_READ_RDMA = 0x03   # server -> client
CMD_WRITE = 0x04       # client -> server
CMD_WRITE_RDMA = 0x05  # client -> server
CMD_WRITE_DONE = 0x06  # server -> client

SECTOR_SIZE = 512
RECV_BUFLEN = 2048
# Header (2 bytes) + block_type (4 bytes) = 6 bytes of RDMA overhead.
RDMA_MAX_PAYLOAD = 1472 - 2 - 4  # 1466


# --------------------------------------------------------------------------- #
# Header / block-type packing
#   header  (uint16, little-endian bitfield): cmd:5  cmdid:3  cmdpkt:8
#   block_type (uint32, little-endian bitfield): block_shift:4  block_count:9
# --------------------------------------------------------------------------- #
def pack_header(cmd, cmdid, cmdpkt):
    value = (cmd & 0x1F) | ((cmdid & 0x07) << 5) | ((cmdpkt & 0xFF) << 8)
    return struct.pack("<H", value)


def header_cmd(datagram):
    return datagram[0] & 0x1F


def unpack_header(datagram):
    value = struct.unpack_from("<H", datagram, 0)[0]
    return value & 0x1F, (value >> 5) & 0x07, (value >> 8) & 0xFF


def pack_block_type(block_shift, block_count):
    return struct.pack("<I", (block_shift & 0x0F) | ((block_count & 0x1FF) << 4))


def unpack_block_type(datagram, offset=2):
    value = struct.unpack_from("<I", datagram, offset)[0]
    return value & 0x0F, (value >> 4) & 0x1FF


# --------------------------------------------------------------------------- #
# Block device backed by an image file
# --------------------------------------------------------------------------- #
class BlockDevice:
    def __init__(self, path, read_only=False):
        self.path = path
        self.read_only = read_only
        try:
            mode = "rb" if read_only else "r+b"
            self._f = open(path, mode, buffering=0)
        except OSError:
            self.read_only = True
            self._f = open(path, "rb", buffering=0)
        self._f.seek(0, os.SEEK_END)
        self.size = self._f.tell()
        self._f.seek(0)

    def sector_count(self):
        return self.size // SECTOR_SIZE

    def seek(self, sector):
        self._f.seek(sector * SECTOR_SIZE)

    def read(self, size):
        data = self._f.read(size)
        if len(data) < size:  # past end of image -- pad so packet sizing stays correct
            data = data + b"\x00" * (size - len(data))
        return data

    def write(self, data):
        if self.read_only:
            return
        self._f.write(data)

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class UdpbdServer:
    def __init__(self, device, bind="", verbose=False):
        self.bd = device
        self.verbose = verbose
        self._total_read = 0
        self._total_write = 0
        self._write_left = 0

        self._block_shift = None
        self._set_block_shift(5)  # default 128-byte blocks, matching upstream

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((bind or "0.0.0.0", UDPBD_PORT))
        self._bind_addr = bind or "0.0.0.0"

    # -- RDMA block sizing ------------------------------------------------- #
    def _set_block_shift(self, shift):
        if shift == self._block_shift:
            return
        self._block_shift = shift
        self._block_size = 1 << (shift + 2)
        self._blocks_per_packet = RDMA_MAX_PAYLOAD // self._block_size
        self._blocks_per_sector = SECTOR_SIZE // self._block_size
        if self.verbose:
            print("Block size changed to {}".format(self._block_size))

    def _set_block_shift_for_sectors(self, sectors):
        # Pick the largest block size that still yields the minimum packet count
        # (fewest packets = least overhead; largest blocks = faster on the PS2).
        size = sectors * SECTOR_SIZE
        packets_min = -(-size // 1440)  # ceil
        if -(-size // 1024) == packets_min:
            shift = 7  # 512-byte blocks
        elif -(-size // 1280) == packets_min:
            shift = 6  # 256-byte blocks
        elif -(-size // 1408) == packets_min:
            shift = 5  # 128-byte blocks
        else:
            shift = 3  # 32-byte blocks
        self._set_block_shift(shift)

    # -- command handlers -------------------------------------------------- #
    def _handle_info(self, addr, datagram):
        _, cmdid, _ = unpack_header(datagram)
        print("UDPBD_CMD_INFO from {}".format(addr[0]))
        self._print_stats()
        reply = pack_header(CMD_INFO_REPLY, cmdid, 1) + struct.pack(
            "<II", SECTOR_SIZE, self.bd.sector_count()
        )
        self.sock.sendto(reply, addr)

    def _handle_read(self, addr, datagram):
        _, cmdid, _ = unpack_header(datagram)
        _, sector_nr, sector_count = struct.unpack_from("<HIH", datagram, 0)
        if self.verbose:
            print("UDPBD_CMD_READ(cmdid={}, start={}, count={})".format(
                cmdid, sector_nr, sector_count))

        self._set_block_shift_for_sectors(sector_count)
        blocks_left = sector_count * self._blocks_per_sector
        self._total_read += blocks_left * self._block_size
        self.bd.seek(sector_nr)

        cmdpkt = 1
        while blocks_left > 0:
            block_count = min(blocks_left, self._blocks_per_packet)
            blocks_left -= block_count
            data = self.bd.read(block_count * self._block_size)
            packet = (pack_header(CMD_READ_RDMA, cmdid, cmdpkt)
                      + pack_block_type(self._block_shift, block_count)
                      + data)
            self.sock.sendto(packet, addr)
            cmdpkt = (cmdpkt + 1) & 0xFF

    def _handle_write(self, addr, datagram):
        _, cmdid, _ = unpack_header(datagram)
        _, sector_nr, sector_count = struct.unpack_from("<HIH", datagram, 0)
        if self.verbose:
            print("UDPBD_CMD_WRITE(cmdid={}, start={}, count={})".format(
                cmdid, sector_nr, sector_count))
        if self.bd.read_only:
            print("Warning: write requested on a read-only image -- ignoring")
        self.bd.seek(sector_nr)
        self._write_left = sector_count * SECTOR_SIZE
        self._total_write += self._write_left

    def _handle_write_rdma(self, addr, datagram):
        _, cmdid, _ = unpack_header(datagram)
        block_shift, block_count = unpack_block_type(datagram)
        size = block_count * (1 << (block_shift + 2))
        self.bd.write(datagram[6:6 + size])
        self._write_left -= size
        if self._write_left <= 0:
            reply = pack_header(CMD_WRITE_DONE, cmdid, (cmdid + 1) & 0xFF)
            reply += struct.pack("<i", 0)
            self.sock.sendto(reply, addr)

    def _print_stats(self):
        print("Total read: {} KiB, total write: {} KiB".format(
            self._total_read // 1024, self._total_write // 1024))

    # -- main loop --------------------------------------------------------- #
    def run(self):
        ro = "read-only" if self.bd.read_only else "read/write"
        print("UDPBD server")
        print("  Image: {}  ({})".format(self.bd.path, ro))
        print("  Size : {} MiB / {} sectors".format(
            self.bd.size // (1024 * 1024), self.bd.sector_count()))
        print("  Listening on {}:{} (0x{:X}) -- PS2 finds it via broadcast".format(
            self._bind_addr, UDPBD_PORT, UDPBD_PORT))
        print("  In OPL: choose UDPBD; no IP or port to enter.")
        print()
        try:
            while True:
                try:
                    datagram, addr = self.sock.recvfrom(RECV_BUFLEN)
                except OSError:
                    break  # socket closed -> shut down
                if len(datagram) < 2:  # smaller than the header: ignore
                    continue
                try:
                    cmd = header_cmd(datagram)
                    if cmd == CMD_INFO:
                        self._handle_info(addr, datagram)
                    elif cmd == CMD_READ and len(datagram) >= 8:
                        self._handle_read(addr, datagram)
                    elif cmd == CMD_WRITE and len(datagram) >= 8:
                        self._handle_write(addr, datagram)
                    elif cmd == CMD_WRITE_RDMA and len(datagram) >= 6:
                        self._handle_write_rdma(addr, datagram)
                    elif self.verbose:
                        print("Ignoring bad/short packet (cmd 0x{:x}) from {}".format(
                            cmd, addr[0]))
                except (struct.error, IndexError) as e:
                    # never let a malformed packet (fuzzing / port scan) kill the server
                    if self.verbose:
                        print("Bad packet from {}: {}".format(addr[0], e))
        except KeyboardInterrupt:
            print("\nShutting down...")
            self._print_stats()
        finally:
            self.sock.close()
            self.bd.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="UDPBD server -- serve a disk image to a PS2 over UDP (OPL).")
    parser.add_argument("image", help="disk image / block device to serve")
    parser.add_argument("-r", "--read-only", action="store_true",
                        help="serve the image read-only (no saves / VMC writes)")
    parser.add_argument("-i", "--bind", default="", metavar="IP",
                        help="interface to bind (default: all interfaces)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every read/write command")
    args = parser.parse_args(argv)

    if not os.path.exists(args.image):
        print("Error: '{}' not found".format(args.image))
        return 1

    try:
        device = BlockDevice(args.image, read_only=args.read_only)
    except OSError as e:
        print("Error: cannot open '{}': {}".format(args.image, e))
        return 1

    try:
        server = UdpbdServer(device, bind=args.bind, verbose=args.verbose)
    except OSError as e:
        device.close()
        print("Error: cannot bind UDP port 0x{:X}: {}".format(UDPBD_PORT, e))
        return 1

    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
