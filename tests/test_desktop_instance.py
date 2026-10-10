import ctypes
import platform
import subprocess
import sys
import unittest
import uuid
from unittest.mock import Mock, patch

from launcher.single_instance import DesktopInstance, restart_args


class DesktopInstanceTests(unittest.TestCase):
    def setUp(self):
        # Exercise a real kernel mutex without contending with a user's desktop.
        self.mutex_name = "Local\\PS2Servers.Test." + uuid.uuid4().hex
        mutex_patch = patch("launcher.single_instance._MUTEX_NAME", self.mutex_name)
        mutex_patch.start()
        self.addCleanup(mutex_patch.stop)

    def child_setup(self):
        return ("import launcher.single_instance as instance; "
                "instance._MUTEX_NAME=" + repr(self.mutex_name) + "; ")

    def test_list_does_not_acquire_desktop_lock(self):
        from launcher import main
        with patch("launcher.single_instance.DesktopInstance", side_effect=AssertionError), \
                patch.object(main, "_print_list") as listing:
            self.assertEqual(main.main(["--list"]), 0)
            listing.assert_called_once_with()

    @unittest.skipUnless(platform.system() == "Windows", "Windows kernel integration")
    def test_restart_child_waits_for_parent_release(self):
        code = self.child_setup() + ("from launcher.single_instance import DesktopInstance; "
                "print('ready', flush=True); x=DesktopInstance(restarting=True); "
                "x.__enter__(); print(int(x.acquired), flush=True); x.__exit__()")
        proc = None
        try:
            with DesktopInstance() as owner:
                self.assertTrue(owner.acquired)
                proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                self.assertIsNone(proc.poll())
            output, _ = proc.communicate(timeout=5)
            self.assertEqual(output.strip(), "1")
            self.assertEqual(proc.returncode, 0)
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_non_windows_is_unrestricted(self):
        with patch("launcher.single_instance.platform.system", return_value="Linux"):
            with DesktopInstance() as instance:
                self.assertTrue(instance.acquired)
            self.assertEqual(restart_args(), [])

    def test_windows_lock_results_and_cleanup(self):
        for result, acquired in [(0, True), (0x80, True), (258, False), (0xffffffff, False)]:
            with self.subTest(result=result):
                kernel, user = Mock(), Mock()
                kernel.CreateMutexW.return_value = 0x100000001
                kernel.WaitForSingleObject.return_value = result
                with patch("launcher.single_instance.platform.system", return_value="Windows"), \
                        patch("launcher.single_instance.ctypes.WinDLL", create=True,
                              side_effect=[kernel, user]):
                    with DesktopInstance(restarting=True) as instance:
                        self.assertEqual(instance.acquired, acquired)
                    kernel.WaitForSingleObject.assert_called_once_with(0x100000001, 30000)
                    kernel.CloseHandle.assert_called_once_with(0x100000001)
                    self.assertEqual(kernel.ReleaseMutex.call_count, int(acquired))
                    self.assertEqual(user.MessageBoxW.call_count, int(not acquired))
                    self.assertIs(kernel.CreateMutexW.restype, ctypes.wintypes.HANDLE)

    @unittest.skipUnless(platform.system() == "Windows", "Windows kernel integration")
    def test_real_mutex_blocks_another_process_and_releases(self):
        code = self.child_setup() + ("from launcher.single_instance import DesktopInstance; "
                "x=DesktopInstance(); x.__enter__(); "
                "print(int(x.acquired), flush=True); input(); x.__exit__()")
        with DesktopInstance() as owner:
            self.assertTrue(owner.acquired)
            # Probe directly to avoid a modal dialog during the test.
            probe = ("import ctypes; from ctypes import wintypes; "
                     "k=ctypes.WinDLL('kernel32'); "
                     "k.OpenMutexW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR]; "
                     "k.OpenMutexW.restype=wintypes.HANDLE; "
                     "k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]; "
                     "h=k.OpenMutexW(0x100000,False," + repr(self.mutex_name) + "); "
                     "print(k.WaitForSingleObject(h,0))")
            self.assertEqual(subprocess.check_output([sys.executable, "-c", probe], text=True).strip(), "258")
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "1")
            proc.communicate("\n", timeout=5)
            self.assertEqual(proc.returncode, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
