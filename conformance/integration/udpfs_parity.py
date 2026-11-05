#!/usr/bin/env python3
"""Differential UDPFS host conformance for udpfsd, PS2-Servers Core, and Edge.

This is a host socket test, not a physical-console acceptance test. It starts
actual server processes on one non-loopback IPv4 interface and drives identical
fixtures through the standard UDPFS protocol.
"""
import argparse
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UDPFS_DIR = ROOT / "udpfs_server"
sys.path.insert(0, str(UDPFS_DIR))
sys.path.insert(0, str(UDPFS_DIR.parent))
import udpfs_server as srv
from compression_selftest import make_cso, make_plain_iso


class UdpfsError(Exception):
    pass


class ProtocolError(UdpfsError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ParityClient:
    def __init__(self, disc_addr, bind_ip, timeout=5.0):
        self.disc_addr = disc_addr
        self.bind_ip = bind_ip
        self.data_addr = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, 0))
        self.sock.settimeout(timeout)
        self.tx_seq = 0
        self.rx_expected = 0
        self.duplicate = False
        self.inject_nack = False
        self.drop_fin = False
        self.reorder = False
        self.reorder_buffer = None
        self.reorder_release = False
        self.ack_delay = 0.0

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def discover(self, seq=0):
        pkt = (srv.Header(packet_type=srv.PacketType.DISCOVERY, seq_nr=seq).pack()
               + srv.DiscHeader(service_id=srv.UDPRDMA_SVC_UDPFS, port=0).pack())
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
            self.data_addr = ((self.disc_addr[0], disc.port) if disc.port else addr)
            return
        raise UdpfsError("no INFORM reply")

    def reset_transport(self):
        self.tx_seq = 0
        self.rx_expected = 0

    def _send_request(self, msg):
        hdr = srv.Header(packet_type=srv.PacketType.DATA, seq_nr=self.tx_seq).pack()
        dh = srv.DataHeader(seq_nr_ack=(self.rx_expected - 1) & 0xFFF,
                            flags=srv.DataFlags.ACK, hdr_word_count=0,
                            data_byte_count=len(msg)).pack()
        packet = hdr + dh + msg
        self.sock.sendto(packet, self.data_addr)
        if self.duplicate:
            self.sock.sendto(packet, self.data_addr)
            self.duplicate = False
        self.tx_seq = (self.tx_seq + 1) & 0xFFF

    def _send_nack(self, sequence):
        hdr = srv.Header(packet_type=srv.PacketType.DATA, seq_nr=self.tx_seq).pack()
        dh = srv.DataHeader(seq_nr_ack=sequence, flags=0, hdr_word_count=0,
                            data_byte_count=0).pack()
        self.sock.sendto(hdr + dh, self.data_addr)

    def _send_ack(self):
        if self.ack_delay:
            time.sleep(self.ack_delay)
            self.ack_delay = 0.0
        hdr = srv.Header(packet_type=srv.PacketType.DATA, seq_nr=self.tx_seq).pack()
        dh = srv.DataHeader(seq_nr_ack=(self.rx_expected - 1) & 0xFFF,
                            flags=srv.DataFlags.ACK, hdr_word_count=0,
                            data_byte_count=0).pack()
        self.sock.sendto(hdr + dh, self.data_addr)

    def _recv_data_packet(self):
        while True:
            if self.reorder_release:
                dh, data = self.reorder_buffer
                hdr = srv.Header.unpack(data)
                if hdr.seq_nr == self.rx_expected:
                    self.reorder_buffer = None
                    self.reorder_release = False
                    self.rx_expected = (self.rx_expected + 1) & 0xFFF
                    payload_size = dh.hdr_word_count * 4 + dh.data_byte_count
                    return dh, data[6:6 + payload_size]
            data, _addr = self.sock.recvfrom(65535)
            if len(data) < 6:
                continue
            hdr = srv.Header.unpack(data)
            if hdr.packet_type != srv.PacketType.DATA:
                continue
            dh = srv.DataHeader.unpack(data[2:6])
            payload_size = dh.hdr_word_count * 4 + dh.data_byte_count
            if payload_size == 0:
                continue
            if self.reorder and hdr.seq_nr == self.rx_expected:
                next_data, _ = self.sock.recvfrom(65535)
                next_hdr = srv.Header.unpack(next_data)
                next_dh = srv.DataHeader.unpack(next_data[2:6])
                if (next_hdr.packet_type == srv.PacketType.DATA
                        and next_hdr.seq_nr == (self.rx_expected + 1) & 0xFFF):
                    self.reorder_buffer = (next_dh, next_data)
                    self._send_nack(self.rx_expected)
                    self.reorder = False
                    continue
                self.reorder = False
            if self.inject_nack:
                self.inject_nack = False
                self._send_nack(hdr.seq_nr)
                continue
            if self.drop_fin and dh.flags & srv.DataFlags.FIN:
                self.drop_fin = False
                continue
            if hdr.seq_nr != self.rx_expected:
                self._send_ack()
                continue
            self.rx_expected = (self.rx_expected + 1) & 0xFFF
            if self.reorder_buffer is not None:
                self.reorder_release = True
            return dh, data[6:6 + payload_size]

    def _recv_result_stream(self):
        result = None
        buf = bytearray()
        first = True
        got_fin = False
        while not got_fin:
            dh, payload = self._recv_data_packet()
            hdr_size = dh.hdr_word_count * 4
            if first:
                if hdr_size < 8:
                    raise UdpfsError("missing RESULT header")
                mt, _, _, _, result = struct.unpack("<BBBBi", payload[:8])
                if mt != srv.MsgType.RESULT_REPLY:
                    raise UdpfsError(f"expected RESULT_REPLY, got {mt:#x}")
                buf += payload[hdr_size:hdr_size + dh.data_byte_count]
                first = False
            else:
                buf += payload[:dh.data_byte_count]
            self._send_ack()
            got_fin = bool(dh.flags & srv.DataFlags.FIN)
        if result is None:
            raise UdpfsError("missing transfer result")
        if result < 0:
            raise ProtocolError(result, f"transfer failed: {result}")
        return result, bytes(buf)

    def open(self, path, flags=0x01, is_dir=False):
        msg = (struct.pack("<BBHi", srv.MsgType.OPEN_REQ, int(is_dir), flags, 0)
               + path.encode() + b"\0")
        self._send_request(msg)
        _, payload = self._recv_data_packet()
        self._send_ack()
        if not payload or payload[0] != srv.MsgType.OPEN_REPLY:
            raise UdpfsError("expected OPEN_REPLY")
        handle, mode, size, hisize = struct.unpack("<iIII", payload[4:20])
        if handle < 0:
            raise ProtocolError(handle, f"OPEN {path!r} failed: {handle}")
        return handle, size | hisize << 32, mode

    def read(self, handle, size):
        msg = struct.pack("<BBBBiI", srv.MsgType.READ_REQ, 0, 0, 0, handle, size)
        self._send_request(msg)
        result, buf = self._recv_result_stream()
        return buf[:result]

    def read_file(self, path, expected_len):
        handle, _, _ = self.open(path)
        return self.read(handle, expected_len)

    def close_handle(self, handle):
        self._send_request(struct.pack("<BBBBi", srv.MsgType.CLOSE_REQ, 0, 0, 0, handle))
        _, payload = self._recv_data_packet()
        self._send_ack()
        result = struct.unpack("<i", payload[4:8])[0]
        if result != 0:
            raise UdpfsError(f"CLOSE failed: {result}")

    def getstat(self, path):
        msg = struct.pack("<BBBB", srv.MsgType.GETSTAT_REQ, 0, 0, 0) + path.encode() + b"\0"
        self._send_request(msg)
        _, payload = self._recv_data_packet()
        self._send_ack()
        result, mode, _attr, size, hisize = struct.unpack("<iIIII", payload[4:24])
        if result < 0:
            raise ProtocolError(result, f"GETSTAT failed: {result}")
        return mode, size | hisize << 32

    def listdir(self, path):
        handle, _, _ = self.open(path, is_dir=True)
        entries = []
        while True:
            self._send_request(struct.pack("<BBBBi", srv.MsgType.DREAD_REQ, 0, 0, 0, handle))
            _, payload = self._recv_data_packet()
            self._send_ack()
            result = struct.unpack("<i", payload[4:8])[0]
            if result == 0:
                break
            if result < 0:
                raise UdpfsError(f"DREAD failed: {result}")
            name_len = struct.unpack("<H", payload[2:4])[0]
            size, hisize = struct.unpack("<II", payload[16:24])
            entries.append((payload[48:48 + name_len].decode(), size | hisize << 32))
        self.close_handle(handle)
        return entries

    def write(self, handle, data):
        chunk = struct.pack("<BBHHH", srv.MsgType.WRITE_DATA, 0, 0, len(data), 1) + data
        self._send_request(struct.pack("<BBBBiI", srv.MsgType.WRITE_REQ, 0, 0, 0, handle, len(data)) + chunk)
        _, payload = self._recv_data_packet()
        self._send_ack()
        result = struct.unpack("<i", payload[4:8])[0]
        if result != len(data):
            raise UdpfsError(f"WRITE returned {result}, want {len(data)}")

    def bread(self, sector, count=1, handle=0):
        msg = struct.pack("<BBHiII", srv.MsgType.BREAD_REQ, 0, count, handle,
                          sector & 0xFFFFFFFF, (sector >> 32) & 0xFFFFFFFF)
        self._send_request(msg)
        result, buf = self._recv_result_stream()
        return buf[:result]

    def bwrite(self, handle, sector, data):
        header = struct.pack("<BBHiII", srv.MsgType.BWRITE_REQ, 0, len(data) // 512,
                             handle, sector & 0xFFFFFFFF, (sector >> 32) & 0xFFFFFFFF)
        chunk = struct.pack("<BBHHH", srv.MsgType.WRITE_DATA, 0, 0, len(data), 1) + data
        self._send_request(header + chunk)
        _, payload = self._recv_data_packet()
        self._send_ack()
        return struct.unpack("<i", payload[4:8])[0]

    def path_op(self, operation, path):
        self._send_request(struct.pack("<BBH", operation, 0, 0o755) + path.encode() + b"\0")
        _, payload = self._recv_data_packet()
        self._send_ack()
        result = struct.unpack("<i", payload[4:8])[0]
        if result != 0:
            raise UdpfsError(f"path operation {operation:#x} failed: {result}")


def bindable_host():
    candidates = []
    try:
        candidates.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None,
                                                                    family=socket.AF_INET))
    except OSError:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    for ip in dict.fromkeys(candidates):
        if ip.startswith("127.") or ip == "0.0.0.0":
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((ip, 0))
            return ip
        except OSError:
            continue
        finally:
            sock.close()
    raise UdpfsError("no bindable non-loopback IPv4 address")


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def make_zso(path, data, block_size=2048):
    def literal(block):
        if len(block) < 15:
            return bytes([len(block) << 4]) + block
        remaining = len(block) - 15
        encoded = bytearray([0xF0])
        while remaining >= 255:
            encoded.append(255)
            remaining -= 255
        encoded.append(remaining)
        return bytes(encoded) + block

    blobs = []
    for offset in range(0, len(data), block_size):
        blobs.append(literal(data[offset:offset + block_size]))
    offsets = []
    current = 24 + (len(blobs) + 1) * 4
    for blob in blobs:
        offsets.append(current)
        current += len(blob)
    offsets.append(current)
    header = struct.pack("<4sIQIBBH", b"ZSO\x00", 24, len(data), block_size, 1, 0, 0)
    path.write_bytes(header + struct.pack("<%dI" % len(offsets), *offsets) + b"".join(blobs))


def make_fixtures(root):
    (root / "DVD").mkdir()
    plain = make_plain_iso(root / "DVD" / "Example.iso", 4096)
    compressed = make_plain_iso(root / "DVD" / "source.bin", 5120)[:5000]
    (root / "DVD" / "source.bin").unlink()
    make_cso(root / "DVD" / "Example.cso", compressed)
    make_cso(root / "DVD" / "Legacy.cso", compressed)
    (root / "NAMESPACE").mkdir()
    make_cso(root / "NAMESPACE" / "Collision.cso", compressed)
    (root / "NAMESPACE" / "Collision.cso.iso").write_bytes(b"REAL")
    (root / "NAMESPACE" / "Corrupt.cso").write_bytes(b"not a valid CSO")
    (root / "NAMESPACE" / "PrefG.cso").write_bytes(b"not a valid CSO")
    make_zso(root / "NAMESPACE" / "PrefG.zso", compressed)
    for name in ("a" * 247 + ".cso", "b" * 248 + ".cso"):
        make_cso(root / "NAMESPACE" / name, compressed)
    (root / "card.bin").write_bytes(bytes(1024))
    (root / "large.iso").write_bytes(bytes((i * 7) & 0xFF for i in range(65536)))
    image = bytes((i * 7 + i // 512) & 0xFF for i in range(64 * 512 + 17))
    (root / "disk.img").write_bytes(image)
    return plain, compressed, image


class Server:
    def __init__(self, name, binary, root, host, block_only=False, protocol="standard"):
        self.name = name
        self.root = root
        self.host = host
        self.protocol = protocol
        self.port = free_port()
        self.log = root / f"{name}-{block_only}.log"
        args = self.command(binary, block_only)
        self.process = subprocess.Popen(args, cwd=ROOT, stdout=self.log.open("wb"),
                                        stderr=subprocess.STDOUT,
                                        env=dict(os.environ, PYTHONUNBUFFERED="1"))
        try:
            self.wait_ready()
        except Exception:
            self.stop()
            raise

    def command(self, binary, block_only):
        kind = self.name.split("-")[0]
        block = str(self.root / "disk.img")
        if kind == "reference":
            return [binary, "-fsroot", "" if block_only else str(self.root),
                    "-bdpath", block, "-bind", f"{self.host}:0", "-port", str(self.port)]
        if kind == "core":
            args = [sys.executable, "ps2servers.py", "serve", "udpfs",
                    "--bind", self.host, "--port", str(self.port),
                    "--protocol-mode", self.protocol, "--block-device", block]
            if not block_only:
                args += ["--root-dir", str(self.root)]
            return args
        if kind == "edge":
            args = [binary, "udpfs", "--bind", self.host, "--port", str(self.port),
                    "--protocol-mode", self.protocol, "--block-device", block]
            if not block_only:
                args += ["--root", str(self.root)]
            return args
        raise ValueError(self.name)

    def wait_ready(self):
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise UdpfsError(f"{self.name} exited early; see {self.log}")
            client = ParityClient((self.host, self.port), self.host, timeout=0.5)
            try:
                client.discover()
                client.close()
                return
            except (UdpfsError, OSError):
                client.close()
                time.sleep(0.1)
        raise UdpfsError(f"{self.name} did not become ready; see {self.log}")

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def expect(condition, message):
    if not condition:
        raise UdpfsError(message)


def connected_client(server, timeout=5.0):
    client = ParityClient((server.host, server.port), server.host, timeout=timeout)
    client.discover()
    return client


def case_filesystem(server):
    a = connected_client(server)
    b = connected_client(server)
    try:
        plain_handle, plain_size, _ = a.open("/DVD/Example.iso")
        compressed_handle, compressed_size, _ = b.open("/DVD/Example.cso.iso")
        expect(plain_size == 4096, "plain ISO size")
        # udpfsd's CachedReader.Stat is empty for virtual aliases; its decoded
        # OPEN reads are checked, and its GETSTAT limitation is checked separately.
        if server.name == "reference":
            expect(compressed_size == 0, f"reference virtual CSO size: {compressed_size}")
        else:
            expect(compressed_size == 5000, f"virtual CSO size: got {compressed_size}")
        expect(a.read(plain_handle, 4096) == (root_plain := bytes(range(256)) * 16), "plain ISO bytes")
        expected_cso = (root_plain + root_plain)[:5000]
        expect(b.read(compressed_handle, 5000) == expected_cso, "virtual CSO bytes")
        raw_size = (server.root / "DVD" / "Example.cso").stat().st_size
        raw_handle, raw_stat_size, _ = a.open("/DVD/Example.cso")
        expect(raw_stat_size == raw_size, "direct CSO raw size")
        raw = a.read(raw_handle, raw_size)
        expect(raw == (server.root / "DVD" / "Example.cso").read_bytes(), "direct CSO raw bytes")
        raw_getstat = a.getstat("/DVD/Example.cso")
        expect(raw_getstat[1] == raw_size, f"direct CSO GETSTAT raw size: {raw_getstat}, want {raw_size}")
        if server.name == "reference":
            try:
                a.getstat("/DVD/Example.cso.iso")
            except ProtocolError as exc:
                expect(exc.code == -5, f"reference virtual GETSTAT errno: {exc.code}")
            expect(a.read_file("/DVD/Example.iso", 4096) == root_plain,
                   "server did not remain usable after virtual GETSTAT")
        else:
            expect(a.getstat("/DVD/Example.cso.iso")[1] == 5000, "virtual CSO GETSTAT size")
        if server.name == "reference":
            try:
                a.open("/DVD/Legacy.iso")
            except ProtocolError as exc:
                expect(exc.code == -2, f"reference legacy alias errno: {exc.code}")
            expect(a.read_file("/DVD/Example.iso", 4096) == root_plain,
                   "server did not remain usable after legacy alias failure")
        else:
            legacy_handle, _, _ = a.open("/DVD/Legacy.iso")
            expect(a.read(legacy_handle, 5000) == expected_cso, "legacy ISO alias")
        names = dict(a.listdir("/DVD"))
        for name in ("Example.iso", "Example.cso.iso", "Legacy.cso.iso"):
            expect(name in names, f"missing advertised {name}: {names}")
        expect("Example.cso" not in names, "raw compressed name leaked into listing")
        a.path_op(srv.MsgType.MKDIR_REQ, "/TMP_PARITY")
        save, _, _ = a.open("/TMP_PARITY/save.bin", flags=0x03 | 0x0200 | 0x0400)
        a.write(save, b"parity save")
        a.close_handle(save)
        save, _, _ = a.open("/TMP_PARITY/save.bin")
        expect(a.read(save, 11) == b"parity save", "WRITE/READ round trip")
        a.close_handle(save)
        expect(a.getstat("/TMP_PARITY/save.bin")[1] == 11, "written file size")
        card, _, _ = a.open("card.bin", flags=0x03)
        result = a.bwrite(card, 1, bytes([0xA5]) * 512)
        expect(result in (0, 512), f"file BWRITE result {result}")
        expect(a.bread(1, handle=card) == bytes([0xA5]) * 512, "file BWRITE/BREAD")
        a.close_handle(card)
        a.path_op(srv.MsgType.REMOVE_REQ, "/TMP_PARITY/save.bin")
        a.path_op(srv.MsgType.RMDIR_REQ, "/TMP_PARITY")
    finally:
        a.close()
        b.close()


def case_namespace(server):
    c = connected_client(server)
    try:
        if server.name != "reference":
            names = [name for name, _size in c.listdir("/NAMESPACE")]
            expect(names.count("Collision.cso.iso") == 1,
                   f"real/alias collision produced {names.count('Collision.cso.iso')} names")
            expect("Corrupt.cso.iso" not in names, "corrupt alias advertised")
            expect("Collision.cso" in names or "Collision.cso.iso" in names,
                   "valid entry hidden by corrupt alias")
            expect("a" * 247 + ".cso.iso" in names, "255-byte alias rejected")
            expect("b" * 248 + ".cso.iso" not in names, "overlength alias advertised")
            handle, _, _ = c.open("/NAMESPACE/Collision.cso.iso")
            expect(c.read(handle, 4) == b"REAL", "real alias did not win collision")
            expected_errno = -2 if server.name == "core" else -5
            try:
                c.getstat("/NAMESPACE/Corrupt.cso.iso")
            except ProtocolError as exc:
                expect(exc.code == expected_errno,
                       f"corrupt GETSTAT errno {exc.code}, want {expected_errno}")
            else:
                raise UdpfsError("corrupt virtual GETSTAT succeeded")
            expect(c.read_file("/DVD/Example.iso", 4096) == bytes(range(256)) * 16,
                   "server did not remain usable after corrupt GETSTAT")
        if server.name != "core" or srv.LZ4_AVAILABLE:
            handle, _, _ = c.open("/NAMESPACE/PrefG.zso.iso")
            expect(len(c.read(handle, 5000)) == 5000, "valid ZSO decode")
        if server.name != "reference" and (server.name != "core" or srv.LZ4_AVAILABLE):
            handle, _, _ = c.open("/NAMESPACE/PrefG.iso")
            expect(len(c.read(handle, 5000)) == 5000,
                   "legacy alias did not select parseable sibling")
    finally:
        c.close()


def case_wrap(server):
    c = connected_client(server)
    try:
        for _ in range(4096):
            expect(c.getstat("/DVD/Example.iso")[1] == 4096, "GETSTAT before wrap")
        expect(len(c.read_file("/DVD/Example.iso", 4096)) == 4096,
               "read after 4095-to-0 sequence wrap")
    finally:
        c.close()


def case_block_and_restart(server):
    c = connected_client(server)
    try:
        expect(c.getstat("")[1] == 64 * 512, "block capacity")
        handle, _, _ = c.open("/DVD/Example.iso")
        expected = c.bread(1, handle=handle)
        c.discover(7)
        expect(c.bread(2, handle=handle) == expected, "nonzero background discovery")
        c.discover(0)
        expect(c.bread(3, handle=handle) == expected, "zero background discovery")
        c.discover(0)
        time.sleep(2.1)
        expect(c.bread(3, handle=handle) == expected, "delayed loader handoff")
        card, _, _ = c.open("card.bin", flags=0x03)
        c.reset_transport()
        expect(c.bread(4, handle=handle) == expected, "DATA-first inherited FILEID")
        result = c.bwrite(card, 0, bytes([0x53]) * 512)
        expect(result in (0, 512), f"restart BWRITE result {result}")
        expect(c.bread(0, handle=card) == bytes([0x53]) * 512, "restart BWRITE data")
        c.close_handle(0)
    finally:
        c.close()


def case_nonzero_replacement(server, binary):
    root = Path(tempfile.mkdtemp(prefix=f"udpfs-{server.name}-auto-", dir=server.root.parent))
    make_fixtures(root)
    auto = Server(server.name + "-auto", binary, root, server.host, protocol="auto")
    client = None
    succeeded = False
    try:
        client = connected_client(auto)
        handle, _, _ = client.open("/DVD/Example.iso")
        expected = client.bread(1, handle=handle)
        client.discover(7)
        client.reset_transport()
        client.tx_seq = 8
        client.rx_expected = 1
        actual = client.bread(2, handle=handle)
        expect(actual == expected, "Auto nonzero-discovery replacement inherited FILEID")
        succeeded = True
    finally:
        if client is not None:
            client.close()
        auto.stop()
        if succeeded:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"logs retained: {root}")


def case_faults(server):
    for fault in ("nack", "duplicate", "fin", "reorder"):
        c = connected_client(server)
        try:
            handle, _, _ = c.open("large.iso")
            if fault == "nack":
                c.inject_nack = True
            elif fault == "duplicate":
                c.duplicate = True
            elif fault == "reorder":
                c.reorder = True
            else:
                c.drop_fin = True
            expect(len(c.read(handle, 65536)) == 65536, f"{fault} recovery")
        finally:
            c.close()
    c = connected_client(server, timeout=4.0)
    try:
        handle, _, _ = c.open("large.iso")
        c.ack_delay = 1.5
        try:
            got = c.read(handle, 65536)
        except socket.timeout:
            if server.name != "reference":
                raise
            probe = connected_client(server)
            try:
                expect(len(probe.read_file("/DVD/Example.iso", 4096)) == 4096,
                       "reference did not remain usable after ACK timeout")
            finally:
                probe.close()
            return
        else:
            if server.name == "reference":
                raise UdpfsError("reference unexpectedly completed delayed-ACK transfer")
        expect(len(got) == 65536,
               f"{server.name}: delayed ACK recovery returned {len(got)} bytes")
    finally:
        c.close()


def case_block_only(server):
    c = connected_client(server)
    try:
        expect(c.getstat("")[1] == 64 * 512, "block-only capacity")
        expect(len(c.bread(0)) == 512, "block-only BREAD")
        expect(c.bwrite(0, 0, bytes([0xA5]) * 512) in (0, 512), "block-only BWRITE")
        c.close_handle(0)
        expected_errno = {"reference": -2, "core": -13, "edge": -2}[server.name.split("-")[0]]
        try:
            c.open("missing.iso")
        except ProtocolError as exc:
            expect(exc.code == expected_errno,
                   f"block-only OPEN errno {exc.code}, want {expected_errno}")
        else:
            raise UdpfsError("block-only filesystem OPEN succeeded")
        expect(len(c.bread(0)) == 512, "server was not usable after block-only OPEN")
        expect(server.process.poll() is None, "block-only server process exited")
    finally:
        c.close()


def run_server(name, binary, work, host, cases):
    server_root = Path(tempfile.mkdtemp(prefix=f"udpfs-{name}-", dir=work))
    plain, compressed, image = make_fixtures(server_root)
    server = Server(name, str(binary), server_root, host)
    succeeded = False
    try:
        for case in cases:
            if case == "filesystem":
                case_filesystem(server)
            elif case == "namespace":
                case_namespace(server)
            elif case == "restart":
                case_block_and_restart(server)
                if name != "reference":
                    case_nonzero_replacement(server, binary)
            elif case == "wrap":
                case_wrap(server)
            elif case == "faults":
                case_faults(server)
        if "block-only" in cases:
            only_root = Path(tempfile.mkdtemp(prefix=f"udpfs-{name}-only-", dir=work))
            only_succeeded = False
            try:
                (only_root / "disk.img").write_bytes(image)
                only = Server(name + "-block-only", str(binary), only_root, host, block_only=True)
                try:
                    case_block_only(only)
                    only_succeeded = True
                finally:
                    only.stop()
            finally:
                if only_succeeded:
                    shutil.rmtree(only_root, ignore_errors=True)
                else:
                    print(f"logs retained: {only_root}")
        succeeded = True
    finally:
        server.stop()
        if succeeded:
            shutil.rmtree(server_root, ignore_errors=True)
        else:
            print(f"logs retained: {server_root}")
    return plain, compressed, image


def source_revision():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-binary", required=True)
    parser.add_argument("--edge-binary", required=True)
    parser.add_argument("--cases", default="filesystem,namespace,restart,wrap,faults,block-only")
    parser.add_argument("--work", help="temporary/log parent (defaults to system temp)")
    args = parser.parse_args()
    cases = [case for case in args.cases.split(",") if case]
    unknown = set(cases) - {"filesystem", "namespace", "restart", "wrap", "faults", "block-only"}
    if unknown:
        raise SystemExit(f"unknown cases: {sorted(unknown)}")
    host = bindable_host()
    work = args.work or tempfile.gettempdir()
    os.makedirs(work, exist_ok=True)
    reference = os.path.abspath(args.reference_binary)
    edge = os.path.abspath(args.edge_binary)
    revision = source_revision()
    print(f"topology: host={host}, cases={','.join(cases)}")
    for name, binary in (("reference", reference), ("core", "core"), ("edge", edge)):
        started = time.monotonic()
        label = "ee94414" if name == "reference" else revision
        try:
            run_server(name, binary, work, host, cases)
        except Exception as exc:
            print(f"FAIL {name}@{label}: {exc}")
            raise
        print(f"PASS {name}@{label}: {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
