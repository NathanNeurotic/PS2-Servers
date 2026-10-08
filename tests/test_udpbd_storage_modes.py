"""Storage integrity, native exclusivity, and virtual exFAT interoperability."""

import ctypes
import importlib.util
import os
import pathlib
import platform
import shutil
import struct
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from launcher import raw_storage, servers, virtual_exfat

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('udpbd_modes', ROOT / 'udpbd_server/udpbd_server.py')
UDPBD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UDPBD)


class MemoryBackend:
    sector_size = 4096

    def __init__(self):
        self.data = bytearray(i % 251 for i in range(32768))
        self.size = len(self.data)
        self.flush = Mock()
        self.close = Mock()

    def read_at(self, offset, count):
        return bytes(self.data[offset:offset + count])

    def write_at(self, offset, data):
        assert offset % self.sector_size == 0
        assert len(data) % self.sector_size == 0
        self.data[offset:offset + len(data)] = data
        return len(data)


class WritableRawTests(unittest.TestCase):
    def test_fragmented_write_preserves_native_sector_neighbors(self):
        backend = MemoryBackend()
        before = bytes(backend.data)
        device = raw_storage.RawDevice('test', backend, writable=True)
        device.seek(3, 10)
        payload = b'w' * 5120
        for start in range(0, len(payload), 1408):
            device.write(payload[start:start + 1408])
        self.assertEqual(backend.data, before[:1536] + payload + before[6656:])
        with self.assertRaises(OSError):
            device.write(b'x')
        device.flush()
        backend.flush.assert_called_once()

    def test_short_read_modify_write_does_not_write(self):
        backend = MemoryBackend()
        backend.read_at = Mock(return_value=b'bad')
        backend.write_at = Mock()
        device = raw_storage.RawDevice('test', backend, writable=True)
        device.seek(1, 1)
        with self.assertRaises(OSError):
            device.write(b'x' * 512)
        backend.write_at.assert_not_called()

    def test_raw_write_without_exclusive_never_opens_target(self):
        with patch.object(raw_storage, '_LinuxDevice') as opener:
            with self.assertRaises(ValueError):
                raw_storage.open_raw_device('/dev/test', writable=True)
            opener.assert_not_called()

    def test_linux_unmount_precedes_exclusive_open_and_busy_mount_is_refused(self):
        record = dict(path='/dev/test', mounts=['/games'], system=False)
        unmounted = dict(record, mounts=[])
        backend = MemoryBackend()
        events = []
        with patch.object(raw_storage.platform, 'system', return_value='Linux'), \
                patch.object(raw_storage, '_exclusive_target', side_effect=[record, unmounted]), \
                patch.object(raw_storage.subprocess, 'run', side_effect=lambda *a, **k: events.append('unmount')), \
                patch.object(raw_storage, '_LinuxDevice', side_effect=lambda *a, **k: events.append(k) or backend), \
                patch.object(raw_storage, 'mount_warning', return_value=''):
            device = raw_storage.open_raw_device('/dev/test', writable=True, exclusive=True)
            self.assertEqual(events, ['unmount', {'writable': True, 'exclusive': True}])
            device.close()
        with patch.object(raw_storage.platform, 'system', return_value='Linux'), \
                patch.object(raw_storage, '_exclusive_target', return_value=record), \
                patch.object(raw_storage.subprocess, 'run'), \
                patch.object(raw_storage, '_LinuxDevice') as opener:
            with self.assertRaises(OSError):
                raw_storage.open_raw_device('/dev/test', exclusive=True)
            opener.assert_not_called()

    def test_system_and_unknown_targets_are_refused(self):
        for records in ([], [dict(path='test', mounts=[], system=True)],
                        [dict(path='test', mounts=['/'], system=False)]):
            with patch.object(raw_storage, '_metadata', return_value=records), self.assertRaises(ValueError):
                raw_storage._exclusive_target('test')

    def test_wire_flush_failure_reports_failure_and_foreign_peer_cannot_write(self):
        backend = MemoryBackend()
        backend.flush.side_effect = OSError('flush failed')
        server = UDPBD.UdpbdServer.__new__(UDPBD.UdpbdServer)
        server.bd = raw_storage.RawDevice('test', backend, writable=True)
        server.verbose = False
        server._total_write = 0
        server.sock = Mock()
        peer = ('127.0.0.1', 1234)
        header = UDPBD.pack_header
        server._handle_write(peer, header(UDPBD.CMD_WRITE, 3, 0) + struct.pack('<IH', 1, 1))
        packet = header(UDPBD.CMD_WRITE_RDMA, 3, 0) + UDPBD.pack_block_type(7, 1) + b'x' * 512
        before = bytes(backend.data)
        server._handle_write_rdma(('127.0.0.2', 1234), packet)
        self.assertEqual(bytes(backend.data), before)
        server._handle_write_rdma(peer, packet)
        self.assertEqual(struct.unpack_from('<i', server.sock.sendto.call_args.args[0], 2)[0], -1)
        self.assertEqual(server._write_left, 0)

    @unittest.skipUnless(platform.system() == 'Windows', 'Windows error API')
    def test_windows_failed_lock_never_dismounts_or_opens_physical_disk(self):
        api = Mock()
        api.CreateFileW.return_value = 123
        api.DeviceIoControl.return_value = False
        with patch.object(ctypes, 'WinDLL', return_value=api), self.assertRaises(OSError):
            raw_storage._WindowsDevice(r'\\.\PhysicalDrive2', writable=True,
                                       exclusive=True, volumes=[r'\\?\Volume{test}'])
        self.assertEqual(api.CreateFileW.call_count, 1)
        self.assertEqual(api.DeviceIoControl.call_args.args[1], 0x90018)
        api.CloseHandle.assert_called_once_with(123)

    def test_registry_modes_and_cli_reject_unsafe_combinations(self):
        self.assertEqual(servers.UDPBD.build_argv(dict(virtual_folder='/games')),
                         ['--virtual-exfat', '/games'])
        self.assertEqual(servers.UDPBD.build_argv(dict(raw_device='/dev/test', exclusive=True, raw_write=True)),
                         ['--raw-device', '/dev/test', '--exclusive', '--raw-write'])
        for values in (dict(raw_device='/dev/test', raw_write=True),
                       dict(image_file='game.img', exclusive=True),
                       dict(image_file='game.img', virtual_folder='/games')):
            with self.assertRaises(ValueError):
                servers.UDPBD.build_argv(values)
        with self.assertRaises(SystemExit):
            UDPBD.main(['--raw-device', '/dev/test', '--raw-write'])

    def test_windows_child_locks_precede_disk_open_and_release_on_close(self):
        api = Mock()
        api.CreateFileW.side_effect = [101, 102, 103]
        commands = []

        def ioctl(handle, command, source, source_size, output, size, returned, overlapped):
            commands.append((handle, command))
            if command == 0x7405c:
                output.raw = struct.pack('<q', 32768)
            elif command == 0x70000:
                output.raw = struct.pack('<qIIII', 1, 0, 1, 1, 4096)
            returned._obj.value = size
            return True

        api.DeviceIoControl.side_effect = ioctl
        with patch.object(ctypes, 'WinDLL', return_value=api, create=True):
            device = raw_storage._WindowsDevice(r'\\.\PhysicalDrive2', writable=True,
                exclusive=True, volumes=[r'\\?\Volume{one}', r'\\?\Volume{two}'])
        self.assertEqual(commands[:4], [(101, 0x90018), (101, 0x90020),
                                        (102, 0x90018), (102, 0x90020)])
        self.assertEqual(api.CreateFileW.call_args.args[1], 0xc0000000)
        device.flush()
        api.FlushFileBuffers.assert_called_once_with(103)
        device.close()
        self.assertEqual([c.args[0] for c in api.CloseHandle.call_args_list], [103, 101, 102])

    def test_linux_backend_writable_exclusive_flags_and_native_sync(self):
        backend_io = types.SimpleNamespace(ioctl=Mock(side_effect=[
            struct.pack('=Q', 32768), struct.pack('=I', 4096)]))
        with patch.dict('sys.modules', {'fcntl': backend_io}), \
                patch.object(raw_storage.os, 'O_NONBLOCK', 0x800, create=True), \
                patch.object(raw_storage.os, 'open', return_value=77) as opener, \
                patch.object(raw_storage.os, 'fstat', return_value=types.SimpleNamespace(st_mode=0o060000)), \
                patch.object(raw_storage.os, 'pwrite', return_value=4096, create=True) as writer, \
                patch.object(raw_storage.os, 'fsync') as sync, \
                patch.object(raw_storage.os, 'close'):
            device = raw_storage._LinuxDevice('/dev/test', writable=True, exclusive=True)
            self.assertEqual(opener.call_args.args[1],
                raw_storage.os.O_RDWR | raw_storage.os.O_NONBLOCK | raw_storage.os.O_EXCL)
            self.assertEqual(device.write_at(4096, b'x' * 4096), 4096)
            writer.assert_called_once_with(77, b'x' * 4096, 4096)
            device.flush()
            sync.assert_called_once_with(77)
            device.close()


class VirtualExfatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / 'games'
        self.root.mkdir()
        (self.root / 'DVD').mkdir()
        self.payload = bytes(i % 251 for i in range(70000))
        self.game = self.root / 'DVD' / 'SLUS_123.45.Game.iso'
        self.game.write_bytes(self.payload)
        (self.root / 'empty.txt').touch()
        (self.root / 'Unicode-é-😀.txt').write_bytes(b'unicode')
        self.device = virtual_exfat.VirtualExfat(self.root)
        self.addCleanup(self.device.close)

    def test_mbr_boot_checksum_backup_and_directory_checksums(self):
        d = self.device
        d.seek(0, 1)
        mbr = d.read(512)
        self.assertEqual(mbr[510:], b'\x55\xaa')
        start, size = struct.unpack_from('<II', mbr, 454)
        self.assertEqual(start, 2048)
        self.assertEqual(start + size, d.sector_count())
        d.seek(start, 24)
        boot = d.read(12288)
        self.assertEqual(boot[:6144], boot[6144:])
        self.assertEqual(boot[3:11], b'EXFAT   ')
        crc = virtual_exfat.checksum(boot[:5632], skip=(106, 107, 112))
        self.assertEqual(boot[5632:6144], struct.pack('<I', crc) * 128)
        for node in d._nodes:
            if not node.directory:
                continue
            data = node.payload
            offset = 96 if node is d.root else 0
            while data[offset] != 0:
                count = data[offset + 1]
                group = data[offset:offset + (count + 1) * 32]
                expected = struct.unpack_from('<H', group, 2)[0]
                self.assertEqual(virtual_exfat.checksum(group, 16, (2, 3)), expected)
                offset += len(group)

    def test_file_reads_are_lazy_fragmented_and_padding_is_zero(self):
        d = self.device
        node = next(n for n in d._nodes if n.path == self.game)
        self.assertEqual(len(d._handles), 0)
        start = d._heap_offset(node.cluster)
        d.seek(start // 512, node.clusters * 64)
        pieces = []
        for _ in range((node.clusters * 32768 + 1407) // 1408):
            pieces.append(d.read(1408))
        self.assertEqual(b''.join(pieces), self.payload + bytes(node.clusters * 32768 - len(self.payload)))
        self.assertEqual(len(d._handles), 1)
        d.seek(0, 0)
        self.assertEqual(d.read(512), b'')
        with self.assertRaises(PermissionError):
            d.write(b'bad')

    def test_changed_file_is_rejected_instead_of_serving_stale_layout(self):
        node = next(n for n in self.device._nodes if n.path == self.game)
        self.game.write_bytes(b'changed')
        self.device.seek(self.device._heap_offset(node.cluster) // 512, 1)
        with self.assertRaises(OSError):
            self.device.read(512)

    def test_case_collisions_are_rejected(self):
        (self.root / 'EMPTY.TXT').write_bytes(b'duplicate')
        # Windows cannot create distinct case-only siblings on a normal volume.
        if len(list(self.root.glob('*'))) == 3:
            self.skipTest('case-insensitive host filesystem')
        with self.assertRaises(ValueError):
            virtual_exfat.VirtualExfat(self.root)

    @unittest.skipUnless(shutil.which('fsck.exfat'), 'requires exfatprogs independent validator')
    def test_image_passes_independent_exfat_filesystem_check(self):
        # Force a multi-cluster root directory, not only the single-cluster case.
        for i in range(300):
            (self.root / f'long-name-for-directory-chain-{i:04d}.txt').touch()
        self.device.close()
        self.device = virtual_exfat.VirtualExfat(self.root)
        self.addCleanup(self.device.close)
        self.assertGreater(self.device.root.clusters, 1)
        image = pathlib.Path(self.temp.name) / 'virtual.img'
        # fsck checks the partition volume, rather than the enclosing MBR disk.
        d = self.device
        d.seek(2048)
        with image.open('wb') as f:
            while d._position < d.size:
                f.write(d.read(65536))
        result = subprocess.run(['fsck.exfat', '-n', str(image)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Negative control: the validator must reject two invalid boot sectors.
        with image.open('r+b') as f:
            for offset in (3, 12 * 512 + 3):
                f.seek(offset)
                f.write(b'BROKEN  ')
        invalid = subprocess.run(['fsck.exfat', '-n', str(image)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(invalid.returncode, 0, invalid.stdout + invalid.stderr)
