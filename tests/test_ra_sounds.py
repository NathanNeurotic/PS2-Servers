"""Custom RA sounds stay inside the managed profile and never alter defaults."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

from launcher import gui, ra_sounds


def make_wav(path, duration=0.05, channels=1, width=2, rate=22050):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(bytes(int(rate * duration) * channels * width))


class SoundProfiles(unittest.TestCase):
    def test_atomic_custom_file_and_restore_leave_other_sounds_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            src = folder / "chosen.wav"
            make_wav(src)
            target = ra_sounds.install_clip(src, "connect", directory=folder)
            self.assertEqual(target.name, "connect.wav")
            self.assertEqual(target.read_bytes(), src.read_bytes())
            self.assertEqual(ra_sounds.validate_clip(target), src.stat().st_size)
            with self.assertRaises(FileExistsError):
                ra_sounds.install_clip(src, "connect", directory=folder)
            replacement = folder / "replacement.wav"
            make_wav(replacement, duration=0.1)
            ra_sounds.install_clip(replacement, "connect", directory=folder, replace=True)
            self.assertEqual(target.read_bytes(), replacement.read_bytes())
            untouched = folder / "achievement.wav"
            make_wav(untouched)
            self.assertTrue(ra_sounds.restore_default("connect", directory=folder))
            self.assertFalse(target.exists())
            self.assertTrue(untouched.exists())
            self.assertFalse(ra_sounds.restore_default("connect", directory=folder))

    def test_invalid_sound_never_creates_playable_destination(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for bad in (b"", b"not a WAV file", b"RIFF" + bytes(50)):
                source = directory / "bad.wav"
                source.write_bytes(bad)
                with self.assertRaises(ValueError):
                    ra_sounds.install_clip(source, "disconnect", directory=directory)
                self.assertFalse((directory / "disconnect.wav").exists())
            valid = directory / "oversized.wav"
            make_wav(valid, duration=16, width=1, channels=1, rate=16000)
            with self.assertRaises(ValueError):
                ra_sounds.install_clip(valid, "disconnect", directory=directory)
            self.assertFalse((directory / "disconnect.wav").exists())

    def test_truncated_samples_and_invalid_names_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            clip = folder / "truncated.wav"
            make_wav(clip, duration=0.5)
            clip.write_bytes(clip.read_bytes()[:60])
            with self.assertRaises(ValueError):
                ra_sounds.validate_clip(clip)
            for name in ("../apikey", "CON", "", "connect.wav"):
                with self.assertRaises(ValueError):
                    ra_sounds.sound_path(name, folder)
            self.assertEqual(ra_sounds.sound_path("achievement", folder).name, "achievement.wav")

    def test_live_service_blocks_changes_before_file_dialog_or_deletion(self):
        app = mock.Mock()
        app.is_running.return_value = True
        with mock.patch.object(gui.filedialog, "askopenfilename") as picker, \
             mock.patch.object(gui.messagebox, "showinfo"):
            gui.LauncherApp._set_ra_sound(app, "achievement")
            gui.LauncherApp._restore_ra_sound(app, "disconnect")
            picker.assert_not_called()
        app.is_running.assert_any_call("retroachievements")

    def test_managed_profile_directory_is_not_an_upstream_profile(self):
        with tempfile.TemporaryDirectory() as scratch:
            with mock.patch("launcher.achievements.profile_dir", return_value=Path(scratch) / "retroachievements"):
                folder = ra_sounds.sounds_directory()
            self.assertEqual(folder, Path(scratch) / "retroachievements" / "sounds")
            self.assertTrue(folder.is_dir())


if __name__ == "__main__":
    unittest.main()
