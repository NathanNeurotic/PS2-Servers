import json
import os
import struct
import threading
import time
import unittest
from unittest import mock

from launcher.discord_presence import DEFAULT_APPLICATION_ID, DesktopActivity, Presence, Transport, frame


class ActivityTests(unittest.TestCase):
    def test_only_public_modes_can_enter_activity(self):
        tracker = DesktopActivity()
        tracker.update(["udpfs", "http", "udpfs", "192.168.1.1", "C:/private/game.iso", "secret"])
        activity = tracker.snapshot()
        self.assertEqual(activity["details"], "HTTP, UDPFS")
        self.assertEqual(activity["name"], "PS2-Servers")
        self.assertNotIn("timestamps", activity)
        for private in ("192.168", "private", "secret"):
            self.assertNotIn(private, json.dumps(activity))

    def test_optional_uptime_is_stable_and_can_be_removed(self):
        tracker = DesktopActivity()
        tracker.update(["smbv2"], show_uptime=True)
        first = tracker.snapshot()
        tracker.update(["udpbd"], show_uptime=True)
        self.assertEqual(first["timestamps"], tracker.snapshot()["timestamps"])
        tracker.update([], show_uptime=False)
        self.assertNotIn("timestamps", tracker.snapshot())
        self.assertEqual(tracker.snapshot()["state"], "Desktop launcher")

    def test_ra_game_name_is_explicitly_opt_in_and_tracks_verified_session(self):
        tracker = DesktopActivity()
        active = {"state": "playing", "title": "Shadow of the Colossus",
                  "session": 7, "packets": 123, "serial": "SLUS_123.45",
                  "ip": "192.168.1.5", "account": "private"}
        tracker.update(["retroachievements", "udpfs"], show_game=False, game=active)
        self.assertNotIn("Shadow of the Colossus", json.dumps(tracker.snapshot()))
        tracker.update(["retroachievements", "udpfs"], show_game=True, game=active,
                       show_uptime=True)
        actual = tracker.snapshot()
        self.assertEqual(actual["details"], "Shadow of the Colossus")
        self.assertEqual(actual["state"], "Playing on PlayStation 2")
        started = actual["timestamps"]["start"]
        tracker.update(["retroachievements", "udpfs"], show_game=True, game=active,
                       show_uptime=True)
        self.assertEqual(tracker.snapshot()["timestamps"]["start"], started)
        for private in ("SLUS_123", "192.168", "private", "packets", "serial"):
            self.assertNotIn(private, json.dumps(actual))

    def test_verified_game_is_removed_on_stop_disable_and_untrusted_data(self):
        tracker = DesktopActivity()
        game = {"state": "playing", "title": "Valid game", "session": 1}
        tracker.update(["retroachievements"], show_game=True, game=game)
        self.assertEqual(tracker.snapshot()["details"], "Valid game")
        tracker.update(["retroachievements"], show_game=True,
                       game={"state": "stalled", "title": "Valid game", "session": 1})
        self.assertEqual(tracker.snapshot()["details"], "RetroAchievements")
        for candidate in ("C:/private/game.iso", "\\\\server\\share\\game",
                          "https://example.org", "192.168.1.2"):
            tracker.update(["retroachievements"], show_game=True,
                           game={"state": "playing", "title": candidate, "session": 2})
            self.assertEqual(tracker.snapshot()["details"], "RetroAchievements")
        tracker.update(["retroachievements"], show_game=True, game=game)
        tracker.update(["retroachievements"], show_game=False, game=game)
        self.assertEqual(tracker.snapshot()["details"], "RetroAchievements")
        tracker.update(["udpfs"], show_game=True, game=game)
        self.assertEqual(tracker.snapshot()["details"], "UDPFS")

    def test_game_timer_changes_only_when_verified_session_changes(self):
        tracker = DesktopActivity()
        with mock.patch("launcher.discord_presence.time.time", side_effect=[100, 200, 300]):
            # The first mocked time is the launcher's creation time.
            tracker = DesktopActivity()
            game = {"state": "playing", "title": "Game", "session": 1}
            tracker.update(["retroachievements"], show_game=True, game=game,
                           show_uptime=True)
            self.assertEqual(tracker.snapshot()["timestamps"]["start"], 200)
            tracker.update(["retroachievements"], show_game=True, game=game,
                           show_uptime=True)
            self.assertEqual(tracker.snapshot()["timestamps"]["start"], 200)
            tracker.update(["retroachievements"], show_game=True,
                           game=dict(game, session=2), show_uptime=True)
            self.assertEqual(tracker.snapshot()["timestamps"]["start"], 300)

    def test_official_application_identity(self):
        self.assertEqual(DEFAULT_APPLICATION_ID, "1558114313619898409")

    def test_application_identity_is_validated(self):
        for app_id in ("", "someone else's id", "1" * 30, "１２３４５６７８９０１２３４５６７"):
            with self.assertRaises(ValueError):
                Presence(app_id, ".")

    def test_handshake_waits_for_ready_and_responds_to_ping(self):
        presence = Presence("12345678901234567", ".")
        pipe = mock.Mock()
        pipe.receive.side_effect = [(1, {"evt": "OTHER"}), (3, {"ping": 1}), (1, {"evt": "READY", "data": {}})]
        presence.pipe = pipe
        self.assertEqual(presence.await_reply()["evt"], "READY")
        pipe.write.assert_called_once_with(frame(4, {"ping": 1}))

    def test_shutdown_clears_activity_and_closes_ipc(self):
        import tempfile
        pipe = mock.Mock()
        with tempfile.TemporaryDirectory() as profile:
            presence = Presence(DEFAULT_APPLICATION_ID, profile, transport=lambda _index: pipe)
            def ready():
                presence.stop.set()
                return {"data": {}}
            presence.await_reply = ready
            presence.run()
        packets = [json.loads(call.args[0][8:]) for call in pipe.write.call_args_list]
        self.assertEqual(packets[0]["client_id"], DEFAULT_APPLICATION_ID)
        self.assertEqual(packets[-1]["cmd"], "SET_ACTIVITY")
        self.assertIsNone(packets[-1]["args"]["activity"])
        pipe.close.assert_called_once()

    def test_unavailable_discord_does_not_escape_worker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as profile:
            presence = Presence(DEFAULT_APPLICATION_ID, profile)
            def unavailable(_index):
                presence.stop.set()
                raise OSError("closed")
            presence.transport = unavailable
            presence.run()
            self.assertIsNone(presence.pipe)
            self.assertEqual(presence.message, "Discord activity stopped")

    def test_unchanged_activity_is_not_republished(self):
        import tempfile
        class ClockedStop:
            stopped = False
            checks = 0
            def is_set(self):
                return self.stopped
            def wait(self, seconds):
                if seconds == 5:
                    self.checks += 1
                    if self.checks == 4:
                        self.stopped = True
                return self.stopped
        pipe = mock.Mock()
        pipe.readable.return_value = False
        with tempfile.TemporaryDirectory() as profile:
            presence = Presence(DEFAULT_APPLICATION_ID, profile, transport=lambda _index: pipe,
                                activity_provider=DesktopActivity().snapshot)
            presence.stop = ClockedStop()
            presence.await_reply = mock.Mock(return_value={"data": {}})
            presence.publish = mock.Mock()
            presence.run()
        presence.publish.assert_called_once()
        self.assertEqual(presence.stop.checks, 4)
        pipe.close.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows named-pipe IPC")
    def test_real_named_pipe_handshake_with_fragmented_frame(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        index = 10000 + os.getpid()
        server = kernel.CreateNamedPipeW(r"\\.\pipe\discord-ipc-" + str(index), 3, 0, 1, 4096, 4096, 0, None)
        self.assertNotEqual(server, ctypes.c_void_p(-1).value)
        result = []

        def serve():
            try:
                kernel.ConnectNamedPipe(server, None)
                buffer = ctypes.create_string_buffer(4096)
                read = wintypes.DWORD()
                kernel.ReadFile(server, buffer, 4096, ctypes.byref(read), None)
                packet = buffer.raw[:read.value]
                # The client writes the whole handshake in one operation.
                opcode, length = struct.unpack("<II", packet[:8])
                result.append((opcode, json.loads(packet[8:8 + length])))
                ready = frame(1, {"evt": "READY", "data": {"user": {"username": "fixture"}}})
                for part in (ready[:3], ready[3:11], ready[11:]):
                    written = wintypes.DWORD()
                    kernel.WriteFile(server, part, len(part), ctypes.byref(written), None)
                    time.sleep(0.01)
            finally:
                kernel.CloseHandle(server)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        pipe = Transport(index)
        try:
            pipe.write(frame(0, {"v": 1, "client_id": "12345678901234567"}))
            opcode, ready = pipe.receive(time.monotonic() + 3, threading.Event())
            self.assertEqual(opcode, 1)
            self.assertEqual(ready["evt"], "READY")
        finally:
            pipe.close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [(0, {"v": 1, "client_id": "12345678901234567"})])
