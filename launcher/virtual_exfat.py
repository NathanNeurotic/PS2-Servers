"""Read-only, lazy exFAT disk over a frozen host-folder inventory.

Independent implementation of Microsoft's exFAT specification. Metadata is
generated in memory; file payloads are read on demand, never copied to an image.
The MBR exposes one exFAT partition, as expected by PS2 block-device loaders.
"""

import bisect
import collections
import errno
import math
import os
import pathlib
import stat
import struct

SECTOR = 512
CLUSTER = 32768
PARTITION = 2048


def checksum(data, bits=32, skip=()):
    mask = (1 << bits) - 1
    value = 0
    for i, byte in enumerate(data):
        if i not in skip:
            value = (((value >> 1) | ((value & 1) << (bits - 1))) + byte) & mask
    return value


def name_units(name):
    units = struct.unpack('<' + 'H' * (len(name.encode('utf-16le')) // 2),
                          name.encode('utf-16le'))
    if not units or len(units) > 255 or any(c < 32 or chr(c) in '"*/:<>?\\|' for c in units):
        raise ValueError(f"Name cannot be represented in exFAT: {name!r}")
    return units


def upcase_table():
    # exFAT maps individual UTF-16 code units, without multi-character expansion.
    values = []
    for c in range(65536):
        upper = chr(c).upper()
        values.append(ord(upper) if len(upper) == 1 and ord(upper) <= 65535 else c)
    return values


class Node:
    def __init__(self, path, directory, info):
        self.path = path
        self.directory = directory
        self.identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        self.children = []
        self.size = 0 if directory else info.st_size
        self.cluster = 0
        self.clusters = 0
        self.payload = None


class VirtualExfat:
    """Present a folder as an immutable-layout, read-only UDPBD disk."""

    read_only = True
    is_raw = True  # use bounded request extents in UDPBD

    def __init__(self, root):
        self.path = str(pathlib.Path(root).resolve())
        self._handles = collections.OrderedDict()
        self._position = 0
        self._request_end = None
        self._upper = upcase_table()
        self._nodes = []
        self.root = self._scan(pathlib.Path(self.path), 0)
        encoded = []
        c = 0
        while c < 65536:
            end = c
            while end < 65536 and self._upper[end] == end and end - c < 65535:
                end += 1
            if end - c > 1 or c == 65535:
                encoded.extend((0xffff, end - c))
                c = end
            else:
                encoded.append(self._upper[c])
                c += 1
        upcase = struct.pack('<' + 'H' * len(encoded), *encoded)
        upcase_clusters = math.ceil(len(upcase) / CLUSTER)
        for node in self._nodes:
            if node.directory:
                entries = 3 if node is self.root else 0  # bitmap, upcase, label
                entries += sum(2 + math.ceil(len(name_units(c.path.name)) / 15)
                               for c in node.children)
                node.size = (entries + 1) * 32  # include end marker
            node.clusters = math.ceil(node.size / CLUSTER)
        payload_clusters = sum(n.clusters for n in self._nodes)
        # Solve bitmap capacity including its own clusters, plus a small free tail.
        bitmap_clusters = 1
        while True:
            cluster_count = max(64, payload_clusters + bitmap_clusters + upcase_clusters + 16)
            needed = math.ceil(math.ceil(cluster_count / 8) / CLUSTER)
            if needed == bitmap_clusters:
                break
            bitmap_clusters = needed
        self.cluster_count = cluster_count
        self.bitmap_length = math.ceil(cluster_count / 8)
        self.bitmap_cluster = 2
        self.upcase_cluster = 2 + bitmap_clusters
        next_cluster = self.upcase_cluster + upcase_clusters
        for node in self._nodes:
            node.cluster = next_cluster if node.clusters else 0
            next_cluster += node.clusters
        self.allocated_clusters = next_cluster - 2
        self.fat_length = math.ceil((cluster_count + 2) * 4 / SECTOR)
        self.heap_sector = math.ceil((24 + self.fat_length) / 64) * 64
        volume_sectors = self.heap_sector + cluster_count * (CLUSTER // SECTOR)
        self.size = (PARTITION + volume_sectors) * SECTOR
        if self.size // SECTOR > 0xffffffff:
            raise ValueError("Virtual disk exceeds UDPBD's 32-bit sector capacity")
        self._chains = [(self.bitmap_cluster, bitmap_clusters),
                        (self.upcase_cluster, upcase_clusters)]
        self._chains += [(n.cluster, n.clusters) for n in self._nodes if n.directory]
        self._chains.sort()
        self._chain_starts = [start for start, _ in self._chains]
        self._extents = []
        self._add_extent(self.upcase_cluster, len(upcase), upcase)
        for node in self._nodes:
            if node.directory:
                node.payload = self._directory_bytes(node, upcase)
            self._add_extent(node.cluster, node.size, node)
        self._extents.sort(key=lambda x: x[0])
        self._starts = [x[0] for x in self._extents]
        self._boot = self._boot_region(volume_sectors)
        mbr = bytearray(SECTOR)
        # Saturated CHS fields; modern readers use the LBA start/length.
        mbr[446:462] = struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', 7,
                                  b'\xfe\xff\xff', PARTITION, volume_sectors)
        mbr[510:] = b'\x55\xaa'
        self._mbr = bytes(mbr)

    def _scan(self, path, depth):
        if depth > 64:
            raise ValueError("Virtual exFAT folder nesting exceeds 64 levels")
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
            raise ValueError(f"Virtual exFAT excludes symlinks, reparse points and special files: {path}")
        node = Node(path, stat.S_ISDIR(info.st_mode), info)
        self._nodes.append(node)
        if len(self._nodes) > 100000:
            raise ValueError("Virtual exFAT supports at most 100000 entries")
        if node.directory:
            names = set()
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                key = tuple(self._upper[c] for c in name_units(child.name))
                if key in names:
                    raise ValueError(f"Duplicate exFAT name in {path}: {child.name}")
                names.add(key)
                node.children.append(self._scan(child, depth + 1))
        elif depth == 0:
            raise ValueError("Virtual exFAT requires a folder")
        return node

    def _heap_offset(self, cluster):
        return (PARTITION + self.heap_sector) * SECTOR + (cluster - 2) * CLUSTER

    def _add_extent(self, cluster, length, payload):
        if length:
            self._extents.append((self._heap_offset(cluster), length, payload))

    def _directory_bytes(self, node, upcase):
        entries = bytearray()
        if node is self.root:
            bitmap = bytearray(32)
            bitmap[0] = 0x81
            struct.pack_into('<IQ', bitmap, 20, self.bitmap_cluster, self.bitmap_length)
            case = bytearray(32)
            case[0] = 0x82
            struct.pack_into('<I', case, 4, checksum(upcase))
            struct.pack_into('<IQ', case, 20, self.upcase_cluster, len(upcase))
            label = bytearray(32)
            label[0:2] = bytes((0x83, 9))
            label[2:20] = 'PS2SERVER'.encode('utf-16le')
            entries += bitmap + case + label
        for child in node.children:
            units = name_units(child.path.name)
            primary = bytearray(32)
            primary[0:2] = bytes((0x85, 1 + math.ceil(len(units) / 15)))
            struct.pack_into('<H', primary, 4, 0x10 if child.directory else 0x20)
            # A deterministic valid timestamp, 1980-01-01 00:00:00.
            struct.pack_into('<III', primary, 8, 0x00210000, 0x00210000, 0x00210000)
            stream = bytearray(32)
            stream[0:2] = bytes((0xc0, 1 if child.directory or not child.clusters else 3))
            stream[3] = len(units)
            upper = struct.pack('<' + 'H' * len(units), *(self._upper[c] for c in units))
            struct.pack_into('<H', stream, 4, checksum(upper, 16))
            length = child.clusters * CLUSTER if child.directory else child.size
            struct.pack_into('<Q', stream, 8, length)
            struct.pack_into('<IQ', stream, 20, child.cluster, length)
            secondary = bytearray()
            for start in range(0, len(units), 15):
                name = bytearray(32)
                name[0] = 0xc1
                chunk = units[start:start + 15]
                struct.pack_into('<' + 'H' * len(chunk), name, 2, *chunk)
                secondary += name
            group = primary + stream + secondary
            struct.pack_into('<H', group, 2, checksum(group, 16, (2, 3)))
            entries += group
        return bytes(entries) + bytes(32)

    def _boot_region(self, volume_sectors):
        boot = bytearray(11 * SECTOR)
        boot[0:11] = b'\xeb\x76\x90EXFAT   '
        struct.pack_into('<QQIIIIIIHHBBBBB', boot, 64, PARTITION, volume_sectors,
                         24, self.fat_length, self.heap_sector, self.cluster_count,
                         self.root.cluster, 0x50533253, 0x100, 0, 9, 6, 1, 0x80,
                         math.ceil(self.allocated_clusters * 100 / self.cluster_count))
        boot[510:512] = b'\x55\xaa'
        for i in range(1, 9):
            struct.pack_into('<I', boot, (i + 1) * SECTOR - 4, 0xaa550000)
        crc = checksum(boot, skip=(106, 107, 112))
        region = bytes(boot) + struct.pack('<I', crc) * (SECTOR // 4)
        return region + region

    def sector_count(self):
        return self.size // SECTOR

    def seek(self, sector, sector_count=None):
        self._position = sector * SECTOR
        self._request_end = self.size if sector_count is None else self._position + sector_count * SECTOR
        if not 0 <= self._position <= self._request_end <= self.size:
            raise OSError(errno.EINVAL, "Virtual disk request is out of bounds")

    def _fat(self, offset, count):
        first = offset // 4
        last = math.ceil((offset + count) / 4)
        values = [0] * (last - first)
        for i in range(first, last):
            if i < 2:
                values[i - first] = (0xfffffff8, 0xffffffff)[i]
            else:
                chain = bisect.bisect_right(self._chain_starts, i) - 1
                if chain >= 0:
                    start, length = self._chains[chain]
                    if i < start + length:
                        values[i - first] = i + 1 if i < start + length - 1 else 0xffffffff
        data = struct.pack('<' + 'I' * len(values), *values)
        return data[offset % 4:offset % 4 + count]

    def _file_bytes(self, node, offset, count):
        handle = self._handles.pop(node.path, None)
        if handle is None:
            handle = open(node.path, 'rb')
        self._handles[node.path] = handle
        while len(self._handles) > 16:
            self._handles.popitem(last=False)[1].close()
        info = os.fstat(handle.fileno())
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if identity != node.identity:
            raise OSError(errno.ESTALE, f"File changed since virtual disk startup: {node.path}")
        handle.seek(offset)
        data = handle.read(count)
        if len(data) != count:
            raise OSError(errno.EIO, f"Short virtual file read: {node.path}")
        return data

    def read(self, count):
        if count < 0:
            raise ValueError("Negative read size")
        limit = self.size if self._request_end is None else self._request_end
        count = min(count, limit - self._position)
        end = self._position + count
        pieces = []
        position = self._position
        boot_start = PARTITION * SECTOR
        fat_start = (PARTITION + 24) * SECTOR
        fat_end = fat_start + self.fat_length * SECTOR
        bitmap_start = self._heap_offset(self.bitmap_cluster)
        bitmap_end = bitmap_start + self.bitmap_length
        while position < end:
            if position < SECTOR:
                size = min(end - position, SECTOR - position)
                data = self._mbr[position:position + size]
            elif boot_start <= position < boot_start + len(self._boot):
                offset = position - boot_start
                size = min(end - position, len(self._boot) - offset)
                data = self._boot[offset:offset + size]
            elif fat_start <= position < fat_end:
                size = min(end - position, fat_end - position)
                data = self._fat(position - fat_start, size)
            elif bitmap_start <= position < bitmap_end:
                size = min(end - position, bitmap_end - position)
                byte = position - bitmap_start
                full, remainder = divmod(self.allocated_clusters, 8)
                data = bytes(255 if i < full else (1 << remainder) - 1 if i == full else 0
                             for i in range(byte, byte + size))
            else:
                i = bisect.bisect_right(self._starts, position) - 1
                extent = self._extents[i] if i >= 0 else None
                if extent and position < extent[0] + extent[1]:
                    start, length, payload = extent
                    offset = position - start
                    size = min(end - position, length - offset)
                    if isinstance(payload, bytes):
                        data = payload[offset:offset + size]
                    elif payload.directory:
                        data = payload.payload[offset:offset + size]
                    else:
                        data = self._file_bytes(payload, offset, size)
                else:
                    boundaries = [end, boot_start, fat_start, bitmap_start] + self._starts[i + 1:i + 2]
                    size = min(p for p in boundaries if p > position) - position
                    data = bytes(size)
            pieces.append(data)
            position += size
        self._position = end
        return b''.join(pieces)

    def write(self, data):
        raise PermissionError(errno.EROFS, "Virtual exFAT is read-only; VMC writes are unavailable")

    def close(self):
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
