"""Exercise raw alignment, capacity, read-only enforcement, and target selection."""

import ctypes
import importlib.util
import pathlib
import stat
import struct
import types
import unittest
from unittest.mock import Mock, patch

from launcher import path_access, raw_storage, servers

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("udpbd_raw", ROOT / "udpbd_server/udpbd_server.py")
UDPBD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UDPBD)


class AlignedBackend:
    def __init__(self, sector_size=4096):
        self.data = bytes(i % 251 for i in range(128 * 1024))
        self.size = len(self.data)
        self.sector_size = sector_size
        self.calls = []
        self.closed = False

    def read_at(self, offset, count):
        assert offset % self.sector_size == 0
        assert count % self.sector_size == 0
        self.calls.append((offset, count))
        return self.data[offset:offset + count]

    def close(self):
        self.closed = True


class RawStorageTests(unittest.TestCase):
    def test_fragmented_udpbd_reads_match_native_4k_storage(self):
        backend = AlignedBackend()
        device = raw_storage.RawDevice("test", backend)
        device.seek(3)
        chunks = [device.read(count) for count in (1408, 1408, 1408, 512)]
        self.assertEqual(b"".join(chunks), backend.data[1536:1536 + 4736])
        device.seek(device.sector_count() - 1)
        self.assertEqual(device.read(512), backend.data[-512:])
        self.assertTrue(device.read_only)
        with self.assertRaises(PermissionError):
            device.write(b"bad")
        device.close()
        self.assertTrue(backend.closed)

    def test_unrepresentable_capacity_is_rejected_and_closed(self):
        for size in (0, 513, 512 * 0x100000000):
            backend = AlignedBackend()
            backend.size = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                raw_storage.RawDevice("test", backend)
            self.assertTrue(backend.closed)

    def test_short_native_read_is_an_error_not_zero_padding(self):
        backend = AlignedBackend()
        backend.read_at = lambda offset, count: b""
        device = raw_storage.RawDevice("test", backend)
        with self.assertRaises(OSError):
            device.read(512)

    def test_out_of_bounds_wire_request_does_not_read_device(self):
        backend = AlignedBackend()
        server = UDPBD.UdpbdServer.__new__(UDPBD.UdpbdServer)
        server.bd = raw_storage.RawDevice("test", backend)
        packet = struct.pack("<HIH", 2, server.bd.sector_count(), 1)
        with self.assertRaises(OSError):
            server._handle_read(("127.0.0.1", 1234), packet)
        self.assertEqual(backend.calls, [])

    def test_raw_write_protocol_returns_failure_without_changing_data(self):
        backend = AlignedBackend()
        original = backend.data
        server = UDPBD.UdpbdServer.__new__(UDPBD.UdpbdServer)
        server.bd = raw_storage.RawDevice("test", backend)
        server.verbose = False
        server._total_write = 0
        server.sock = Mock()
        server._handle_write(("127.0.0.1", 1234), struct.pack("<HIH", 4, 0, 1))
        packet = UDPBD.pack_header(5, 0, 1) + UDPBD.pack_block_type(7, 1) + b"x" * 512
        server._handle_write_rdma(("127.0.0.1", 1234), packet)
        reply = server.sock.sendto.call_args.args[0]
        self.assertEqual(struct.unpack("<i", reply[2:])[0], -1)
        self.assertEqual(backend.data, original)

    def test_image_mode_does_not_open_a_block_device_for_writing(self):
        with patch.object(UDPBD.os, "stat", return_value=types.SimpleNamespace(
                st_mode=stat.S_IFBLK)), patch("builtins.open") as opener:
            with self.assertRaises(ValueError):
                UDPBD.BlockDevice("/dev/test")
        opener.assert_not_called()

    def test_registry_requires_exactly_one_backing_target(self):
        self.assertEqual(servers.UDPBD.build_argv({"raw_device": "/dev/test"}),
                         ["--raw-device", "/dev/test"])
        self.assertEqual(servers.UDPBD.build_argv({"image_file": "disk.img"}), ["disk.img"])
        for values in ({}, {"image_file": "disk.img", "raw_device": "/dev/test"}):
            with self.assertRaises(ValueError):
                servers.UDPBD.build_argv(values)

    def test_raw_access_probe_reads_and_closes(self):
        backend = AlignedBackend()
        device = raw_storage.RawDevice("test", backend)
        with patch.object(raw_storage, "open_raw_device", return_value=device):
            usable, report = path_access.check_path("test", "device", False)
        self.assertTrue(usable)
        self.assertIn("Always read-only", report)
        self.assertTrue(backend.closed)

    def test_device_list_hides_windows_boot_and_system_disks(self):
        records = [dict(path="OS", kind="Disk", size=1000, system=True),
                   dict(path=r"\\.\PhysicalDrive2", kind="Disk", model="Games", size=4294967296,
                        system=False, mounts=["E:"], fs=""),
                   dict(path=r"\\.\E:", kind="Volume", model="Games", size=4294967296,
                        system=False, mounts=["E:"], fs="exFAT")]
        with patch.object(raw_storage, "_metadata", return_value=records):
            devices = raw_storage.list_devices()
        self.assertEqual([path for path, _ in devices], [r"\\.\PhysicalDrive2", r"\\.\E:"])
        self.assertIn("Volume", devices[1][1])
        self.assertIn("exFAT", devices[1][1])
        self.assertIn("MOUNTED: E:", devices[0][1])

    def test_windows_open_requests_read_only_and_aligned_native_io(self):
        api = Mock()
        api.CreateFileW.return_value = 123
        api.CloseHandle.return_value = True

        def ioctl(handle, command, source, source_size, output, size, returned, overlapped):
            if command == 0x7405C:
                output.raw = struct.pack("<q", 1024 * 1024)
            elif command == 0x70000:
                output.raw = struct.pack("<qIIII", 1, 0, 1, 1, 4096)
            else:
                self.fail(f"Unexpected ioctl {command:x}")
            returned._obj.value = size
            return True

        api.DeviceIoControl.side_effect = ioctl
        with patch.object(ctypes, "WinDLL", return_value=api, create=True):
            device = raw_storage._WindowsDevice(r"\\.\PhysicalDrive2")
        self.assertEqual(device.size, 1024 * 1024)
        self.assertEqual(device.sector_size, 4096)
        args = api.CreateFileW.call_args.args
        self.assertEqual(args[1], 0x80000000)  # GENERIC_READ, no GENERIC_WRITE
        self.assertEqual(args[5], 0x20000000)  # FILE_FLAG_NO_BUFFERING
        device.close()
        api.CloseHandle.assert_called_once_with(123)

    def test_linux_queries_capacity_on_opened_read_only_descriptor(self):
        fcntl = types.SimpleNamespace(ioctl=Mock(side_effect=[
            struct.pack("=Q", 1024 * 1024), struct.pack("=I", 4096)]))
        with patch.dict("sys.modules", {"fcntl": fcntl}), \
                patch.object(raw_storage.os, "O_NONBLOCK", 0x800, create=True), \
                patch.object(raw_storage.os, "open", return_value=77) as opener, \
                patch.object(raw_storage.os, "fstat", return_value=types.SimpleNamespace(
                    st_mode=stat.S_IFBLK)), patch.object(raw_storage.os, "close"):
            device = raw_storage._LinuxDevice("/dev/test")
            self.assertEqual(opener.call_args.args[1],
                             raw_storage.os.O_RDONLY | raw_storage.os.O_NONBLOCK)
            self.assertEqual(device.size, 1024 * 1024)
            self.assertEqual(fcntl.ioctl.call_args_list[0].args[0], 77)
            device.close()

    def test_windows_read_copies_native_buffer_and_frees_it(self):
        device = raw_storage._WindowsDevice.__new__(raw_storage._WindowsDevice)
        device.handle = 123
        device.api = Mock()
        buffer = ctypes.create_string_buffer(b"x" * 4096)
        address = ctypes.addressof(buffer)
        device.api.VirtualAlloc.return_value = address
        device.api.SetFilePointerEx.return_value = True

        def read(handle, destination, count, received, overlapped):
            self.assertEqual(destination, address)
            received._obj.value = count
            return True

        device.api.ReadFile.side_effect = read
        self.assertEqual(device.read_at(4096, 4096), b"x" * 4096)
        device.api.SetFilePointerEx.assert_called_once_with(123, 4096, None, 0)
        device.api.VirtualFree.assert_called_once_with(address, 0, 0x8000)

    def test_windows_read_failure_still_frees_native_buffer(self):
        device = raw_storage._WindowsDevice.__new__(raw_storage._WindowsDevice)
        device.handle = 123
        device.api = Mock()
        device.api.VirtualAlloc.return_value = 65536
        device.api.SetFilePointerEx.side_effect = OSError("disconnected")
        with self.assertRaises(OSError):
            device.read_at(0, 4096)
        device.api.VirtualFree.assert_called_once_with(65536, 0, 0x8000)
        device.api.ReadFile.assert_not_called()

    def test_image_mode_rejects_fifo_before_open(self):
        with patch.object(UDPBD.os, "stat", return_value=types.SimpleNamespace(
                st_mode=stat.S_IFIFO)), patch("builtins.open") as opener:
            with self.assertRaises(ValueError):
                UDPBD.BlockDevice("pipe")
        opener.assert_not_called()

    def test_raw_wire_read_reassembles_across_native_sectors(self):
        backend = AlignedBackend()
        server = UDPBD.UdpbdServer.__new__(UDPBD.UdpbdServer)
        server.bd = raw_storage.RawDevice("test", backend)
        server.verbose = False
        server._total_read = 0
        server._block_shift = None
        server.sock = Mock()
        server._handle_read(("127.0.0.1", 1234), struct.pack("<HIH", 2, 3, 17))
        packets = [call.args[0] for call in server.sock.sendto.call_args_list]
        self.assertGreater(len(packets), 1)
        self.assertEqual(b"".join(packet[6:] for packet in packets),
                         backend.data[1536:1536 + 17 * 512])
        self.assertEqual(backend.calls, [(0, 12288)])

    def test_buffer_reuses_sector_and_is_invalidated_on_next_request(self):
        backend = AlignedBackend()
        device = raw_storage.RawDevice("test", backend)
        device.seek(0)
        self.assertEqual(device.read(1408) + device.read(1408), backend.data[:2816])
        self.assertEqual(backend.calls, [(0, 4096)])
        backend.data = b"z" * backend.size
        device.seek(0)
        self.assertEqual(device.read(512), b"z" * 512)
        self.assertEqual(backend.calls, [(0, 4096), (0, 4096)])

    def test_linux_disk_mounts_include_nested_child_partitions(self):
        import json
        data = {"blockdevices": [{"path": "/dev/test", "type": "disk", "size": 8192,
                "model": "USB Games", "mountpoints": [None], "children": [
                {"path": "/dev/test1", "type": "part", "size": 4096,
                 "fstype": "exfat", "mountpoints": ["/games"]}]}]}
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b"")
        with patch.object(raw_storage.platform, "system", return_value="Linux"), \
                patch.object(raw_storage.subprocess, "run", return_value=result):
            devices = raw_storage.list_devices()
            self.assertIn("USB Games", devices[0][1])
            self.assertIn("exfat", devices[0][1])
            self.assertIn("/games", raw_storage.mount_warning("/dev/test"))
            self.assertIn("/games", raw_storage.mount_warning("/dev/test1"))

    def test_unknown_mount_status_is_not_reported_as_unmounted(self):
        with patch.object(raw_storage, "_metadata", side_effect=OSError("missing tool")):
            self.assertIn("Could not check mount state", raw_storage.mount_warning("test"))
        with patch.object(raw_storage, "_metadata", return_value=[]):
            self.assertIn("unknown", raw_storage.mount_warning("test"))

    def test_windows_metadata_command_uses_valid_raw_paths(self):
        result = types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")
        with patch.object(raw_storage.platform, "system", return_value="Windows"), \
                patch.object(raw_storage.subprocess, "run", return_value=result) as run:
            self.assertEqual(raw_storage._metadata(), [])
        command = run.call_args.args[0][-1]
        self.assertIn("'" + chr(92) * 2 + "." + chr(92) + "PhysicalDrive'", command)
        self.assertNotIn("'" + chr(92) * 4, command)

    def test_request_buffer_is_bounded_on_512_byte_devices(self):
        backend = AlignedBackend(sector_size=512)
        device = raw_storage.RawDevice("test", backend)
        device.seek(0, 256)
        chunks = [device.read(1024) for _ in range(128)]
        self.assertEqual(b"".join(chunks), backend.data)
        self.assertEqual(backend.calls, [(0, 65536), (65536, 65536)])
