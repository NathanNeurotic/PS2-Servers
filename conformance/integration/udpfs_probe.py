#!/usr/bin/env python3
"""Socket-level UDPFS probe for Python Core or PS2 Servers Edge.

It opens one standards-compatible client and three Modulo-shaped clients at the
same time, including resumed and 4095 -> 0 sequence cases. Every client verifies
the handshake source port, opens a caller-provided file, reads it, acknowledges
server replies, and compares returned bytes with the source file.
"""
import argparse
import socket
import struct
import threading
import time
from pathlib import Path

DISCOVERY, INFORM, DATA = 0, 1, 2
SERVICE = 0xF5F5
OPEN_REQ, OPEN_REPLY = 0x10, 0x11
READ_REQ, RESULT_REPLY = 0x14, 0x26
ACK, FIN, MASK = 1, 2, 0xFFF


def header(kind, seq):
    return struct.pack("<H", (kind & 0xF) | ((seq & MASK) << 4))


def discovery(seq):
    return header(DISCOVERY, seq) + struct.pack("<HH", SERVICE, 0)


def data_packet(seq, payload):
    padded = payload + b"\0" * ((-len(payload)) & 3)
    data_header = ((FIN & 3) << 12) | ((len(padded) & 0x3FFF) << 18)
    return header(DATA, seq) + struct.pack("<I", data_header) + padded


def ack_packet(server_seq):
    return header(DATA, 0) + struct.pack("<I", (server_seq & MASK) | (ACK << 12))


def parse_header(packet):
    value, = struct.unpack_from("<H", packet)
    return value & 0xF, (value >> 4) & MASK


def parse_data(packet):
    kind, seq = parse_header(packet)
    if kind != DATA or len(packet) < 6:
        raise ValueError("not a complete DATA packet")
    value, = struct.unpack_from("<I", packet, 2)
    flags = (value >> 12) & 3
    header_words = (value >> 14) & 0xF
    data_bytes = (value >> 18) & 0x3FFF
    length = header_words * 4 + data_bytes
    if length > len(packet) - 6:
        raise ValueError("DATA lengths exceed datagram")
    return seq, flags, packet[6:6 + length]


def receive_until(sock, predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        sock.settimeout(max(0.01, end - time.monotonic()))
        packet, addr = sock.recvfrom(65535)
        if predicate(packet, addr):
            return packet, addr
    raise TimeoutError("expected packet not received")


def receive_response(sock, opcode):
    while True:
        packet, addr = receive_until(sock, lambda p, _a: len(p) >= 6 and parse_header(p)[0] == DATA)
        seq, _flags, payload = parse_data(packet)
        if payload and payload[0] == opcode:
            return seq, payload, addr


def run_client(host, port, path, expected, discovery_seq, first_seq, modulo):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        sock.sendto(discovery(discovery_seq), (host, port))

        canonical, canonical_addr = receive_until(
            sock, lambda p, _a: len(p) >= 6 and parse_header(p)[0] == INFORM)
        _, canonical_seq = parse_header(canonical)
        if canonical_seq != 1:
            raise AssertionError(f"canonical INFORM seq={canonical_seq}, expected 1")
        service, advertised_port = struct.unpack_from("<HH", canonical, 2)
        if service != SERVICE or advertised_port == 0:
            raise AssertionError("canonical INFORM advertised an invalid service/port")

        if modulo:
            _compatibility, target = receive_until(
                sock,
                lambda p, a: len(p) >= 6 and parse_header(p)[0] == INFORM
                and a[1] == port,
            )
            if target[1] != port:
                raise AssertionError("Modulo fallback did not originate on discovery port")
        else:
            target = (host, advertised_port)
            if canonical_addr[1] != advertised_port:
                raise AssertionError("canonical INFORM source and advertised data port differ")

        open_request = bytearray(8)
        open_request[0] = OPEN_REQ
        struct.pack_into("<H", open_request, 2, 1)  # O_RDONLY
        open_request += path.encode("utf-8") + b"\0"
        sock.sendto(data_packet(first_seq, open_request), target)
        server_seq, payload, response_addr = receive_response(sock, OPEN_REPLY)
        if response_addr[1] != target[1]:
            raise AssertionError("OPEN reply used the wrong local response socket")
        handle, = struct.unpack_from("<i", payload, 4)
        size, = struct.unpack_from("<I", payload, 12)
        if handle <= 0 or size != len(expected):
            raise AssertionError(f"OPEN handle/size mismatch: {handle}/{size}")
        sock.sendto(ack_packet(server_seq), response_addr)

        read_request = bytearray(12)
        read_request[0] = READ_REQ
        struct.pack_into("<iI", read_request, 4, handle, len(expected))
        sock.sendto(data_packet((first_seq + 1) & MASK, read_request), target)
        server_seq, payload, response_addr = receive_response(sock, RESULT_REPLY)
        result, = struct.unpack_from("<i", payload, 4)
        returned = payload[8:8 + max(0, result)]
        if result != len(expected) or returned != expected:
            raise AssertionError(
                f"READ mismatch: result={result}, got={returned.hex()}, want={expected.hex()}")
        sock.sendto(ack_packet(server_seq), response_addr)
        return server_seq
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0xF5F6)
    parser.add_argument("--root", required=True, help="same filesystem root used by the server")
    parser.add_argument("--path", required=True, help="client-relative fixture path")
    args = parser.parse_args()

    source = Path(args.root).resolve() / args.path
    expected = source.read_bytes()
    if not expected:
        raise SystemExit("fixture must contain at least one byte")
    if len(expected) > 1 << 20:
        raise SystemExit("fixture must be at most 1 MiB")

    cases = [(0, 0, False), (0, 1, True), (7, 8, True), (4095, 0, True)]
    failures = []
    threads = []
    lock = threading.Lock()
    for dseq, fseq, modulo in cases:
        def work(d=dseq, f=fseq, m=modulo):
            try:
                run_client(args.host, args.port, args.path, expected, d, f, m)
            except Exception as exc:
                with lock:
                    failures.append((d, f, str(exc)))
        thread = threading.Thread(target=work)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    if failures:
        raise SystemExit("conformance failures: " + repr(failures))
    print("UDPFS mixed-client open/read byte conformance passed")


if __name__ == "__main__":
    main()
