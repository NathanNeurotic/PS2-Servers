#!/usr/bin/env python3
"""PS2 Servers Core UDPFS entry point.

This module keeps the established Python file, compression and UDPBD handlers,
while replacing the global Modulo switch with per-session negotiation. It is the
entry point used by the desktop launcher and by headless ``ps2servers serve``
workflows. The legacy udpfs_server.py entry point remains available for rollback.
"""

import argparse
import os
import select
import struct
import sys
import threading
import time

from udpfs_server import (
    BLOCK_DEVICE_HANDLE,
    LIBCHDR_AVAILABLE,
    LZ4_AVAILABLE,
    SESSION_TIMEOUT,
    SESSION_TIMEOUT_MAX,
    SESSION_TIMEOUT_MIN,
    DiscHeader,
    Header,
    PacketType,
    Session,
    UDPRDMA_SVC_UDPFS,
    UDPFS_PORT,
    UdpfsServer,
    _duration_arg,
    _env_bool,
    _env_duration,
    _env_int,
    _parse_port,
    _port_arg,
    resolve_data_port,
    split_bind,
)

PROFILE_PENDING = "pending"
PROFILE_STANDARD = "standard"
PROFILE_MODULO = "modulo"
PROFILES = (PROFILE_PENDING, PROFILE_STANDARD, PROFILE_MODULO)
SOCKET_DATA = "data"
SOCKET_DISCOVERY = "discovery"
DEFAULT_FALLBACK_SECONDS = 0.25


def classify_profile(discovery_sequence: int, first_data_sequence: int) -> str:
    """Classify a peer from observable sequence behavior.

    The explicit nonzero-discovery check preserves the 4095 -> 0 Modulo case,
    which would otherwise be indistinguishable from a standard DATA sequence 0.
    """
    discovery_sequence &= 0xFFF
    first_data_sequence &= 0xFFF
    modulo_sequence = (discovery_sequence + 1) & 0xFFF
    if first_data_sequence == modulo_sequence and (
            first_data_sequence != 0 or discovery_sequence != 0):
        return PROFILE_MODULO
    if discovery_sequence == 0 and first_data_sequence == 0:
        return PROFILE_STANDARD
    if first_data_sequence == modulo_sequence:
        return PROFILE_MODULO
    return PROFILE_STANDARD


class AutoUdpfsServer(UdpfsServer):
    """Existing Python UDPFS implementation with per-session compatibility."""

    def __init__(self, *args, protocol_mode: str = "auto",
                 fallback_interval: float = DEFAULT_FALLBACK_SECONDS, **kwargs):
        mode = (protocol_mode or "auto").strip().lower()
        if mode not in ("auto", PROFILE_STANDARD, PROFILE_MODULO):
            raise ValueError("protocol_mode must be auto, standard, or modulo")
        self.protocol_mode = mode
        self.fallback_interval = max(0.01, float(fallback_interval))
        # Never activate the legacy global mode. Single-port remains an independent
        # topology choice and per-session response sockets are selected below.
        kwargs["modulo_compat"] = False
        super().__init__(*args, **kwargs)

    def _init_compat(self, sess: Session) -> Session:
        if not hasattr(sess, "protocol_profile"):
            sess.protocol_profile = (
                self.protocol_mode if self.protocol_mode != "auto"
                else PROFILE_PENDING)
            sess.discovery_sequence = 0
            sess.response_socket = SOCKET_DATA
            sess.handshake_generation = 0
            sess.fallback_sent = False
            sess.first_data_seen = False
            sess.compat_lock = threading.RLock()
            sess.ingress_by_packet = {}
        return sess

    def _reset_session_state(self, sess, profile):
        """Close peer-owned handles and reset transport state for a replacement.

        Handle zero is the shared UDPBD image owned by the server and is retained.
        The caller holds ``sess.compat_lock``.
        """
        for handle_id, file_handle in list(sess.handles.items()):
            if handle_id == BLOCK_DEVICE_HANDLE:
                continue
            try:
                file_handle.close()
            except Exception:
                pass
            sess.handles.pop(handle_id, None)
        sess.next_handle = 1
        sess.tx_seq_nr = 0
        sess.tx_seq_nr_acked = 0
        sess.rx_seq_nr_expected = 0
        sess.tx_buffer = []
        sess.tx_start_seq = 0
        sess.write_handle = -1
        sess.write_is_block = False
        sess.write_sector_nr = 0
        sess.write_sector_count = 0
        sess.write_data = bytearray()
        sess.write_total_chunks = 0
        sess.write_received_chunks = 0
        sess.rx_streaming = False
        sess.protocol_profile = profile
        sess.response_socket = SOCKET_DATA
        sess.fallback_sent = False
        sess.first_data_seen = False
        sess.ingress_by_packet.clear()

    def _get_or_create_session(self, addr):
        return self._init_compat(super()._get_or_create_session(addr))

    def _send_specific(self, sock, packet: bytes, addr):
        with self.send_lock:
            sock.sendto(packet, addr)

    def _sendto(self, packet: bytes, addr):
        """Send replies through the socket negotiated for this session."""
        sess = getattr(self._local, "session", None)
        if self.single_port or sess is None:
            sock = self.dsock
        elif getattr(sess, "response_socket", SOCKET_DATA) == SOCKET_DISCOVERY:
            sock = self.sock
        else:
            sock = self.dsock
        self._send_specific(sock, packet, addr)

    def _canonical_inform(self, addr):
        hdr = Header(packet_type=PacketType.INFORM, seq_nr=1)
        disc = DiscHeader(
            service_id=UDPRDMA_SVC_UDPFS,
            port=self.dsock.getsockname()[1])
        self._send_specific(self.dsock, hdr.pack() + disc.pack(), addr)

    def _compatibility_inform(self, sess):
        if sess.fallback_sent:
            return
        hdr = Header(packet_type=PacketType.INFORM, seq_nr=sess.tx_seq_nr)
        disc = DiscHeader(service_id=UDPRDMA_SVC_UDPFS, port=0)
        packet = hdr.pack() + disc.pack() + self._info_payload()
        self._send_specific(self.sock, packet, sess.addr)
        sess.tx_seq_nr_acked = sess.tx_seq_nr
        sess.tx_seq_nr = (sess.tx_seq_nr + 1) & 0xFFF
        sess.fallback_sent = True

    def _schedule_fallback(self, sess, generation: int):
        def send_later():
            time.sleep(self.fallback_interval)
            with self.sessions_lock:
                current = self.sessions.get(sess.addr)
            if current is not sess:
                return
            with sess.compat_lock:
                if (sess.handshake_generation != generation
                        or sess.protocol_profile != PROFILE_PENDING
                        or sess.first_data_seen
                        or sess.rx_streaming):
                    return
                self._compatibility_inform(sess)

        threading.Thread(
            target=send_later,
            name=f"udpfs-fallback-{sess.addr[0]}:{sess.addr[1]}",
            daemon=True,
        ).start()

    def _handle_discovery(self, data: bytes, addr):
        if len(data) < 6:
            return
        try:
            hdr = Header.unpack(data)
            disc = DiscHeader.unpack(data[2:6])
        except (struct.error, ValueError):
            return
        if (hdr.packet_type != PacketType.DISCOVERY
                or disc.service_id != UDPRDMA_SVC_UDPFS):
            return

        with self.sessions_lock:
            existing = self.sessions.get(addr)
            quiet = (time.monotonic() - existing.last_activity
                     if existing is not None else float("inf"))
        self.stats["discovery"] += 1
        sess = self._get_or_create_session(addr)
        with sess.compat_lock:
            # Active clients can keep sequence-zero discovery traffic running in
            # parallel with reads. Reply to it, but never reset that live stream.
            # After a quiet interval the same endpoint is treated as a replacement.
            if sess.rx_streaming and hdr.seq_nr == 0 and quiet < 2.0:
                self._canonical_inform(addr)
                return

            initial_profile = (
                self.protocol_mode if self.protocol_mode != "auto"
                else PROFILE_PENDING)
            self._reset_session_state(sess, initial_profile)
            sess.discovery_sequence = hdr.seq_nr
            sess.handshake_generation += 1
            generation = sess.handshake_generation

            if self.protocol_mode == PROFILE_STANDARD:
                sess.rx_seq_nr_expected = 0
                self._canonical_inform(addr)
                return

            if self.protocol_mode == PROFILE_MODULO:
                sess.rx_seq_nr_expected = (hdr.seq_nr + 1) & 0xFFF
                sess.response_socket = SOCKET_DISCOVERY
                self._compatibility_inform(sess)
                return

            self._canonical_inform(addr)
            self._schedule_fallback(sess, generation)

    def _route_data_via(self, data: bytes, addr, ingress: str):
        if len(data) < 6:
            return
        try:
            hdr = Header.unpack(data)
            data_header = struct.unpack("<I", data[2:6])[0]
        except (struct.error, ValueError):
            return
        if hdr.packet_type != PacketType.DATA:
            return
        header_words = (data_header >> 14) & 0xF
        data_bytes = (data_header >> 18) & 0x3FFF
        payload_size = header_words * 4 + data_bytes
        if payload_size > len(data) - 6:
            return
        sess = self._get_or_create_session(addr)
        # ACK/NACK packets are consumed directly by the base transfer waiters and
        # never reach _handle_data, so only payload packets need ingress metadata.
        if payload_size:
            with sess.compat_lock:
                sess.ingress_by_packet[id(data)] = ingress
        sess.queue.put((data, addr))

    def _handle_data(self, data: bytes, addr):
        sess = self._local.session
        try:
            hdr = Header.unpack(data)
        except (struct.error, ValueError):
            return
        with sess.compat_lock:
            ingress = sess.ingress_by_packet.pop(id(data), SOCKET_DATA)
            if sess.protocol_profile == PROFILE_PENDING:
                profile = classify_profile(sess.discovery_sequence, hdr.seq_nr)
                sess.protocol_profile = profile
                sess.response_socket = ingress
                sess.first_data_seen = True
                if profile == PROFILE_MODULO:
                    sess.rx_seq_nr_expected = hdr.seq_nr
                    if not sess.fallback_sent:
                        self._compatibility_inform(sess)
                else:
                    sess.rx_seq_nr_expected = 0
                    # A delayed compatibility INFORM is irrelevant to a standard
                    # session and must not consume its server DATA sequence.
                    if sess.fallback_sent:
                        sess.tx_seq_nr = 0
                        sess.tx_seq_nr_acked = 0
                        sess.tx_buffer = []
                        sess.fallback_sent = False
                self._print_event(
                    f"[{addr[0]}:{addr[1]}] protocol={profile} "
                    f"response_socket={sess.response_socket}")
            elif not sess.rx_streaming:
                # Strict modes still accept traffic through either local socket.
                # The strict selection controls sequence interpretation only.
                sess.response_socket = ingress
                if sess.protocol_profile == PROFILE_MODULO:
                    sess.rx_seq_nr_expected = hdr.seq_nr
        return super()._handle_data(data, addr)

    def run(self):
        """Receive DISCOVERY or DATA through either relevant local socket."""
        print("PS2 Servers Core — UDPFS")
        if self.root_dir:
            print(f"  Root: {self.root_dir}")
        print(f"  Protocol mode: {self.protocol_mode}")
        print(f"  Discovery bind: 0.0.0.0:{self.port}")
        print(f"  Data bind: {self.dsock.getsockname()[0]}:{self.dsock.getsockname()[1]}")
        print("  Listening...\n")
        while not self._shutdown:
            try:
                sockets = [self.sock] if self.single_port else [self.sock, self.dsock]
                ready, _, _ = select.select(sockets, [], [], 1.0)
                for source in ready:
                    try:
                        data, addr = source.recvfrom(4096)
                    except OSError:
                        continue
                    try:
                        packet_type = Header.unpack(data).packet_type if len(data) >= 2 else -1
                    except (struct.error, ValueError):
                        continue
                    if packet_type == PacketType.DISCOVERY:
                        self._handle_discovery(data, addr)
                    elif packet_type == PacketType.DATA:
                        ingress = (
                            SOCKET_DISCOVERY if source is self.sock
                            else SOCKET_DATA)
                        self._route_data_via(data, addr, ingress)
                self._sweep_idle_sessions()
                self._emit_metrics()
            except KeyboardInterrupt:
                print("\nShutting down...")
                self._shutdown_all_sessions()
                self._cleanup()
                self._print_stats()
                break


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PS2 Servers Core UDPFS server",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--block-device", "-b", default=os.environ.get("BDPATH"))
    parser.add_argument("--root-dir", "-d", default=os.environ.get("FSROOT"))
    parser.add_argument("--port", "-p", type=lambda x: int(x, 0),
                        default=_env_int("PORT", UDPFS_PORT))
    parser.add_argument("--bind", "-i", default=os.environ.get("BIND", ""),
                        metavar="IP[:PORT]")
    parser.add_argument("--sector-size", "-s", type=int,
                        default=_env_int("SECTOR_SIZE", 512))
    parser.add_argument("--read-only", "-r", action="store_true",
                        default=_env_bool("RO"))
    parser.add_argument("--verbose", "-v", action="store_true",
                        default=_env_bool("VERBOSE"))
    compression = parser.add_mutually_exclusive_group()
    compression.add_argument("--enable-compression", "-c", action="store_true")
    compression.add_argument("--no-compression", action="store_true")
    parser.add_argument("--compression-cache-size", type=int,
                        default=_env_int("COMPRESSION_CACHE_SIZE", 32))
    parser.add_argument("--data-port", type=_port_arg("--data-port"), default=None)
    parser.add_argument("--single-port", action="store_true",
                        default=_env_bool("SINGLE_PORT"))
    parser.add_argument(
        "--protocol-mode", choices=("auto", "standard", "modulo"),
        default=os.environ.get("PROTOCOL_MODE", "auto").lower(),
        help="Per-session compatibility policy (default: auto; env: PROTOCOL_MODE)")
    parser.add_argument(
        "--modulo-mode", action="store_true", default=_env_bool("MODULO_MODE"),
        help="Deprecated alias for --protocol-mode modulo")
    parser.add_argument("--compat-fallback", type=_duration_arg("--compat-fallback"),
                        default=_env_duration("COMPAT_FALLBACK", DEFAULT_FALLBACK_SECONDS))
    parser.add_argument("--peer-timeout", type=_duration_arg("--peer-timeout"),
                        default=_env_duration("PEER_TIMEOUT", SESSION_TIMEOUT))
    parser.add_argument("--metrics", action="store_true", default=_env_bool("METRICS"))
    parser.add_argument("--metrics-period", type=_duration_arg("--metrics-period"),
                        default=_env_duration("METRICS_PERIOD", 60.0))
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.modulo_mode:
        if args.protocol_mode not in ("auto", "modulo"):
            parser.error("--modulo-mode conflicts with --protocol-mode")
        print("Warning: --modulo-mode is deprecated; use --protocol-mode modulo.",
              file=sys.stderr)
        args.protocol_mode = "modulo"

    enable_compression = True
    if os.environ.get("ENABLE_COMPRESSION") is not None:
        enable_compression = _env_bool("ENABLE_COMPRESSION", True)
    if _env_bool("NO_COMPRESSION"):
        enable_compression = False
    if args.enable_compression:
        enable_compression = True
    if args.no_compression:
        enable_compression = False

    try:
        bind_ip, bind_port = split_bind(args.bind)
        data_port = resolve_data_port(
            args.data_port, bind_port, os.environ.get("DATA_PORT"))
    except ValueError as exc:
        parser.error(str(exc))

    if not args.block_device and not args.root_dir:
        parser.error("At least one of --block-device or --root-dir is required")
    if args.root_dir and not os.path.isdir(args.root_dir):
        parser.error(f"root directory does not exist: {args.root_dir}")
    if args.block_device and not os.path.exists(args.block_device):
        parser.error(f"block device does not exist: {args.block_device}")
    if not SESSION_TIMEOUT_MIN <= args.peer_timeout <= SESSION_TIMEOUT_MAX:
        parser.error(
            f"--peer-timeout must be {SESSION_TIMEOUT_MIN:g}-{SESSION_TIMEOUT_MAX:g}s")
    if args.compat_fallback <= 0 or args.compat_fallback > 5:
        parser.error("--compat-fallback must be greater than 0 and at most 5s")
    try:
        _parse_port(args.port)
    except (TypeError, ValueError) as exc:
        parser.error(f"invalid --port: {exc}")

    if enable_compression and not LZ4_AVAILABLE:
        print("Warning: lz4 is unavailable; ZSO files stay unadvertised.")
    if enable_compression and not LIBCHDR_AVAILABLE:
        print("Warning: libchdr is unavailable; CHD files stay unadvertised.")

    server = AutoUdpfsServer(
        root_dir=args.root_dir,
        block_device=args.block_device,
        port=args.port,
        bind_ip=bind_ip,
        sector_size=args.sector_size,
        read_only=args.read_only,
        verbose=args.verbose,
        enable_compression=enable_compression,
        compression_cache_size=args.compression_cache_size,
        peer_timeout=args.peer_timeout,
        metrics=args.metrics,
        metrics_period=args.metrics_period,
        single_port=args.single_port,
        data_port=data_port,
        protocol_mode=args.protocol_mode,
        fallback_interval=args.compat_fallback,
    )
    server.run()


if __name__ == "__main__":
    main()
