"""Bounded owned-runtime recording, exported only by an explicit local action."""
from collections import deque
import hashlib
import io
import json
import struct
import time


class Recording:
    MAX_FRAMES = 3600
    MAX_BYTES = 16 * 1024 * 1024
    MAX_EXPORT = 8 * 1024 * 1024

    def __init__(self, watch, generation):
        self.watch, self.generation = watch, generation
        self.frames = deque()
        self.bytes = self.gaps = self.replays = 0
        self.last_sequence = None

    def record(self, sequence, values):
        if len(values) not in self.watch.snapshot_sizes:
            raise ValueError("Incomplete diagnostic snapshot")
        if self.last_sequence is not None:
            delta = (sequence - self.last_sequence) % 1000000
            if delta == 0 or delta >= 500000:
                self.replays += 1
                return
            self.gaps += delta - 1
        self.last_sequence = sequence
        self.frames.append((sequence, int(time.time()), values))
        self.bytes += len(values)
        while len(self.frames) > self.MAX_FRAMES or self.bytes > self.MAX_BYTES:
            self.bytes -= len(self.frames.popleft()[2])

    def report(self, *, profile, game_id=0, image_hash="", test_mode=False, conditions="", source_id="unrecorded"):
        # Identity is deliberately allowlisted. Never serialize account, address,
        # credentials, profile directories, configuration objects or exceptions.
        output = io.StringIO()
        size = 0
        truncated = False
        def write(text):
            nonlocal size, truncated
            data = text.encode("utf-8")
            if size + len(data) > self.MAX_EXPORT - 128:
                truncated = True
                return False
            output.write(text)
            size += len(data)
            return True
        metadata = {"runtime": "PS2-Servers-owned", "profile": profile,
                    "game_id": game_id, "image_hash": image_hash,
                    "host_source_sha256": source_id,
                    "rcheevos": "1433173220a7eaede6a9ed7a18e94117be1821e0",
                    "generation": self.generation, "test_mode": test_mode,
                    "plan_sha256": hashlib.sha256(self.watch.data).hexdigest(),
                    "read_plan": self.watch.data.hex(), "nodes": self.watch.nodes,
                    "node_stride": self.watch.node_stride, "frames": len(self.frames),
                    "retained_bytes": self.bytes, "sequence_gaps": self.gaps,
                    "replays": self.replays,
                    "console_revision": "not supplied by the console protocol"}
        write(json.dumps(metadata, sort_keys=True) + "\n")
        write("active condition state and cached operand values\n" + conditions + "\n")
        offsets, offset = [], 0
        for index in range(self.watch.count):
            word, = struct.unpack_from("<I", self.watch.data, 16 + 4 * index)
            count, address = word >> 28, word & 0x0fffffff
            offsets.append((f"0x{address:08X}/{count}", offset, count))
            offset += count
        for index in range(self.watch.nodes):
            offsets.append((f"chain {index} (resolved address,value)", offset, 8))
            offset += 8
        write("per-address history (snapshot sequence=value; unresolved chain address is zero)\n")
        for label, offset, width in offsets:
            if not write(label + ":"):
                break
            previous = None
            for sequence, _, values in self.frames:
                value = values[offset:offset + width]
                if value != previous:
                    if not write(f" #{sequence}={value.hex()}"):
                        output.write("\n[report truncated at the 8 MiB export limit]\n")
                        return output.getvalue().encode("utf-8")
                    previous = value
            write("\n")
        if truncated:
            output.write("\n[report truncated at the 8 MiB export limit]\n")
        return output.getvalue().encode("utf-8")
