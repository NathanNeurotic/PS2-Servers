#!/usr/bin/env python3
"""UDPFS multi-client self-test (no PS2 hardware needed).

Runs a real UdpfsServer in-process on loopback and drives it with a minimal
UDPFS/UDPRDMA client implemented here, asserting exact byte-equality of file
contents read back over the wire. This is the safety net for the multi-client
refactor:

  * BASELINE      -- single client: DISCOVERY -> OPEN -> READ, byte-verified.
  * CONCURRENCY   -- two clients read two DIFFERENT files at once; each must
                     get its own bytes (proves per-session isolation).

Run:  python udpfs_server/multiclient_selftest.py

The client reuses the server module's own Header/DataHeader/MsgType packers, so
the test speaks the exact wire format the server implements.
"""

import os
import socket
import struct
import sys
import tempfile
import threading
import time

import udpfs_server as srv  # sys.path[0] is this dir when run as a script


# --------------------------------------------------------------------------- #
# Minimal in-process UDPFS/UDPRDMA client
# --------------------------------------------------------------------------- #
class UdpfsError(Exception):
    pass


class UdpfsTestClient:
    """A tiny UDPFS client good enough to DISCOVERY / OPEN / READ a file.

    Mirrors the IOP side of the handshake: per-connection tx_seq / rx_expected,
    ACKs every data-bearing packet from the server (which doubles as the window
    ACK the server's flow control waits for).
    """

    def __init__(self, disc_addr, timeout=5.0):
        self.disc_addr = disc_addr           # (ip, discovery_port)
        self.data_addr = None                # (ip, data_port), learned via INFORM
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(timeout)
        self.tx_seq = 0
        self.rx_expected = 0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    # -- low-level packet helpers -----------------------------------------
    def _send_request(self, msg: bytes):
        """Send a data-bearing request; consumes one client sequence number."""
        hdr = srv.Header(srv.PacketType.DATA, self.tx_seq).pack()
        dh = srv.DataHeader(
            seq_nr_ack=(self.rx_expected - 1) & 0xFFF,
            flags=srv.DataFlags.ACK,
            hdr_word_count=0,
            data_byte_count=len(msg),
        ).pack()
        self.sock.sendto(hdr + dh + msg, self.data_addr)
        self.tx_seq = (self.tx_seq + 1) & 0xFFF

    def _send_ack(self):
        """Pure ACK for the last data-bearing packet we accepted (no seq spend)."""
        hdr = srv.Header(srv.PacketType.DATA, self.tx_seq).pack()
        dh = srv.DataHeader(
            seq_nr_ack=(self.rx_expected - 1) & 0xFFF,
            flags=srv.DataFlags.ACK,
            hdr_word_count=0,
            data_byte_count=0,
        ).pack()
        self.sock.sendto(hdr + dh, self.data_addr)

    def _recv_data_packet(self):
        """Return (data_header, payload) for the next DATA packet, skipping the
        server's pure immediate-ACKs. Enforces in-order delivery (re-ACKs and
        skips anything that isn't the expected sequence)."""
        while True:
            data, _addr = self.sock.recvfrom(4096)
            if len(data) < 6:
                continue
            hdr = srv.Header.unpack(data)
            if hdr.packet_type != srv.PacketType.DATA:
                continue
            dh = srv.DataHeader.unpack(data[2:6])
            payload_size = dh.hdr_word_count * 4 + dh.data_byte_count
            if payload_size == 0:
                continue  # server's immediate/pure ACK -- nothing to accept
            if hdr.seq_nr != self.rx_expected:
                # duplicate/out-of-order: re-ACK what we last accepted, keep waiting
                self._send_ack()
                continue
            self.rx_expected = (self.rx_expected + 1) & 0xFFF
            return dh, data[6:6 + payload_size]

    # -- protocol operations ----------------------------------------------
    def discover(self):
        pkt = (srv.Header(srv.PacketType.DISCOVERY, 0).pack()
               + srv.DiscHeader(srv.UDPRDMA_SVC_UDPFS, 0).pack())
        self.sock.sendto(pkt, self.disc_addr)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            data, addr = self.sock.recvfrom(2048)
            if len(data) < 6:
                continue
            hdr = srv.Header.unpack(data)
            if hdr.packet_type != srv.PacketType.INFORM:
                continue
            disc = srv.DiscHeader.unpack(data[2:6])
            self.data_addr = (self.disc_addr[0], disc.port)
            return
        raise UdpfsError("no INFORM reply to DISCOVERY")

    def open(self, path, flags=0x01):
        msg = (struct.pack('<BBHi', srv.MsgType.OPEN_REQ, 0, flags, 0)
               + path.encode('utf-8') + b'\x00')
        self._send_request(msg)
        dh, payload = self._recv_data_packet()
        self._send_ack()  # ack the OPEN_REPLY (server waits for this)
        mt = payload[0]
        if mt != srv.MsgType.OPEN_REPLY:
            raise UdpfsError(f"expected OPEN_REPLY, got 0x{mt:02x}")
        _, _, _, _, handle, mode, size, hisize = struct.unpack('<BBBBiIII', payload[:20])
        if handle < 0:
            raise UdpfsError(f"OPEN failed, handle={handle}")
        return handle, (size | (hisize << 32))

    def _recv_result_stream(self):
        """Receive a RESULT_REPLY-headed multi-packet stream (shared by READ/BREAD)."""
        result = None
        buf = bytearray()
        got_fin = False
        first = True
        while not got_fin:
            dh, payload = self._recv_data_packet()
            hdr_size = dh.hdr_word_count * 4
            if first:
                if hdr_size < 8:
                    raise UdpfsError("first reply packet missing RESULT header")
                mt, _, _, _, result = struct.unpack('<BBBBi', payload[:8])
                if mt != srv.MsgType.RESULT_REPLY:
                    raise UdpfsError(f"expected RESULT_REPLY, got 0x{mt:02x}")
                buf += payload[hdr_size:hdr_size + dh.data_byte_count]
                first = False
            else:
                buf += payload[:dh.data_byte_count]
            self._send_ack()  # doubles as the flow-control window ACK
            if dh.flags & srv.DataFlags.FIN:
                got_fin = True
        if result is None or result < 0:
            raise UdpfsError(f"transfer failed, result={result}")
        return result, bytes(buf)

    def read(self, handle, size):
        msg = struct.pack('<BBBBiI', srv.MsgType.READ_REQ, 0, 0, 0, handle, size)
        self._send_request(msg)
        result, buf = self._recv_result_stream()
        return buf[:result]

    def bread(self, sector_nr, sector_count, sector_size=512, handle=0):
        # BREAD_REQ: msg(1) reserved(1) sector_count(2) handle(4) sec_lo(4) sec_hi(4)
        msg = struct.pack('<BBHiII', srv.MsgType.BREAD_REQ, 0, sector_count, handle,
                          sector_nr & 0xFFFFFFFF, (sector_nr >> 32) & 0xFFFFFFFF)
        self._send_request(msg)
        result, buf = self._recv_result_stream()
        return buf[:result]

    def read_file(self, path, expected_len):
        handle, _size = self.open(path)
        return self.read(handle, expected_len)


# --------------------------------------------------------------------------- #
# Test scaffolding
# --------------------------------------------------------------------------- #
def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(root_dir, disc_port, block_device=None, single_port=False):
    server = srv.UdpfsServer(root_dir=root_dir, block_device=block_device,
                             port=disc_port, bind_ip='127.0.0.1',
                             single_port=single_port)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(0.4)  # let the select loop come up
    return server


def _make_file(root, name, nbytes, seed):
    import random
    rnd = random.Random(seed)
    data = bytes(rnd.getrandbits(8) for _ in range(nbytes))
    with open(os.path.join(root, name), 'wb') as f:
        f.write(data)
    return data


def test_baseline(root, disc_port, files):
    print("Single-client baseline:")
    ok = True
    for name, expected in files.items():
        c = UdpfsTestClient(('127.0.0.1', disc_port))
        try:
            c.discover()
            got = c.read_file(name, len(expected))
        finally:
            c.close()
        if got == expected:
            print(f"  READ  ok: '{name}' ({len(expected)} bytes) matches on-disk file")
        else:
            print(f"  READ  FAIL: '{name}' got {len(got)} bytes, expected {len(expected)}"
                  f" (equal={got == expected})")
            ok = False
    print("  BASELINE PASSED" if ok else "  BASELINE FAILED")
    return ok


def test_single_port(root, files):
    """--single-port compatibility mode: DISCOVERY *and* DATA must both work on
    the one discovery port, and the INFORM must advertise that same port (so a
    client that never leaves the discovery port still completes the handshake).
    This is what the launcher's 'Modulo UDPFS mode' turns on."""
    print("Single-port compatibility mode:")
    disc_port = _free_udp_port()
    server = _start_server(root, disc_port, single_port=True)
    ok = True
    try:
        name, expected = next(iter(files.items()))
        c = UdpfsTestClient(('127.0.0.1', disc_port))
        try:
            c.discover()
            # The whole point: the advertised data port IS the discovery port.
            if c.data_addr[1] == disc_port:
                print(f"  INFORM ok: data port == discovery port ({disc_port})")
            else:
                print(f"  INFORM FAIL: advertised data port {c.data_addr[1]},"
                      f" expected discovery port {disc_port}")
                ok = False
            got = c.read_file(name, len(expected))
        finally:
            c.close()
        if got == expected:
            print(f"  READ  ok: '{name}' ({len(expected)} bytes) served entirely"
                  f" over port {disc_port}")
        else:
            print(f"  READ  FAIL: got {len(got)} bytes, expected {len(expected)}")
            ok = False
    finally:
        server._shutdown = True
        time.sleep(0.3)
    print("  SINGLE-PORT PASSED" if ok else "  SINGLE-PORT FAILED")
    return ok


def test_concurrency(root, disc_port, files):
    print("Two-client concurrency (session isolation):")
    names = list(files.keys())[:2]
    if len(names) < 2:
        print("  SKIP: need >=2 files")
        return True
    results = {}
    errors = {}
    barrier = threading.Barrier(len(names))

    def worker(name):
        c = UdpfsTestClient(('127.0.0.1', disc_port))
        try:
            c.discover()
            barrier.wait(timeout=5.0)  # line up the two READ streams to overlap
            results[name] = c.read_file(name, len(files[name]))
        except Exception as e:  # noqa: BLE001 - report in the assert below
            errors[name] = e
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok = True
    if errors:
        for n, e in errors.items():
            print(f"  ERROR client '{n}': {type(e).__name__}: {e}")
        ok = False
    for n in names:
        if results.get(n) == files[n]:
            pass
        else:
            print(f"  FAIL: client '{n}' did not get its own bytes intact")
            ok = False
    if ok:
        a, b = names
        print(f"  READ  ok: '{a}' and '{b}' each received their own bytes concurrently")
        print("  CONCURRENCY PASSED")
    else:
        print("  CONCURRENCY FAILED")
    return ok


def test_block_concurrency(disc_port, image_bytes, sector_size=512):
    print("Two-client block-device concurrency (shared handle 0, positional reads):")
    total_sectors = len(image_bytes) // sector_size
    # two clients read interleaved 4-sector ranges of the SAME shared device
    plan = {
        'A': [(i, 4) for i in range(0, total_sectors - 4, 8)],
        'B': [(i, 4) for i in range(4, total_sectors - 4, 8)],
    }
    errors = {}
    barrier = threading.Barrier(2)

    def worker(name):
        c = UdpfsTestClient(('127.0.0.1', disc_port))
        try:
            c.discover()
            barrier.wait(timeout=5.0)
            for sec, cnt in plan[name]:
                got = c.bread(sec, cnt, sector_size)
                exp = image_bytes[sec * sector_size:(sec + cnt) * sector_size]
                if got != exp:
                    raise UdpfsError(f"sector {sec} x{cnt} mismatch "
                                     f"({len(got)} vs {len(exp)} bytes)")
        except Exception as e:  # noqa: BLE001
            errors[name] = e
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ('A', 'B')]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    if errors:
        for n, e in errors.items():
            print(f"  ERROR client '{n}': {type(e).__name__}: {e}")
        print("  BLOCK-DEVICE CONCURRENCY FAILED")
        return False
    reads = sum(len(v) for v in plan.values())
    print(f"  BREAD ok: {reads} interleaved reads across 2 clients, no cross-corruption")
    print("  BLOCK-DEVICE CONCURRENCY PASSED")
    return True


def main():
    tmp = tempfile.mkdtemp(prefix="udpfs_selftest_")
    files = {
        "small.txt": b"hello ps2 world!!\n",
        "fileA.bin": _make_file(tmp, "fileA.bin", 200000, seed=1),
        "fileB.bin": _make_file(tmp, "fileB.bin", 173939, seed=2),
    }
    with open(os.path.join(tmp, "small.txt"), "wb") as f:
        f.write(files["small.txt"])

    # Shared block-device image (256 KB) for the block-concurrency test.
    import random
    sector_size = 512
    image_bytes = bytes(random.Random(7).getrandbits(8) for _ in range(sector_size * 512))
    image_path = os.path.join(tmp, "disk.img")
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    disc_port = _free_udp_port()
    _start_server(tmp, disc_port, block_device=image_path)

    ok = True
    ok = test_baseline(tmp, disc_port, files) and ok
    print()
    ok = test_concurrency(tmp, disc_port,
                          {k: files[k] for k in ("fileA.bin", "fileB.bin")}) and ok
    print()
    ok = test_block_concurrency(disc_port, image_bytes, sector_size) and ok
    print()
    ok = test_single_port(tmp, {"fileA.bin": files["fileA.bin"]}) and ok

    print()
    print("ALL UDPFS TESTS PASSED" if ok else "UDPFS TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
