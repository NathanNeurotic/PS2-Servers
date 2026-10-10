"""Safe custom WAV controls for the existing managed RA sound player.

Sound clips live in the PS2-Servers private RA profile, not in upstream's
independent xeRAbora/Caduceus installations. Files are loaded on engine start.
"""
import os
from pathlib import Path
import shutil
import tempfile
import wave

SOUND_NAMES = ("connect", "disconnect", "achievement")
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 15


def sounds_directory():
    from .achievements import profile_dir
    root = profile_dir() / "sounds"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def sound_path(name, directory=None):
    if name not in SOUND_NAMES:
        raise ValueError("Unknown RetroAchievements sound.")
    return Path(directory) / (name + ".wav") if directory is not None else sounds_directory() / (name + ".wav")


def validate_clip(source):
    """Only bounded, uncompressed 8/16-bit PCM mono/stereo WAV is accepted."""
    source = Path(source)
    size = source.stat().st_size
    if not 44 <= size <= MAX_BYTES:
        raise ValueError("Sound must be a WAV file no larger than 2 MiB.")
    try:
        with wave.open(str(source), "rb") as wav:
            rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            frames = wav.getnframes()
            if (wav.getcomptype() != "NONE" or channels not in (1, 2)
                    or width not in (1, 2) or not 8000 <= rate <= 48000
                    or frames <= 0 or frames > MAX_SECONDS * rate):
                raise ValueError("Use a mono/stereo PCM WAV, 8/16-bit, 8–48 kHz, up to 15 seconds.")
            # Check actual encoded data: truncated sample files must not pass.
            if len(wav.readframes(frames)) != frames * channels * width:
                raise ValueError("Sound contains truncated PCM samples.")
    except (wave.Error, EOFError, OSError) as error:
        raise ValueError("File is not a supported PCM WAV.") from error
    return size


def install_clip(source, name, directory=None, replace=False):
    """Validate temp copy and atomically publish without a partial playable WAV."""
    destination = sound_path(name, directory)
    if os.path.lexists(destination) and not replace:
        raise FileExistsError("A custom sound already exists. Confirm replacement first.")
    source = Path(source)
    if source.resolve() == destination.resolve():
        validate_clip(source)
        return destination
    validate_clip(source)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, path = tempfile.mkstemp(prefix=".sound-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as stream:
            shutil.copyfileobj(stream, out, 1024 * 1024)
        os.chmod(path, 0o600)
        validate_clip(path)
        # Re-check; callers never silently replace someone else's prior file.
        if os.path.lexists(destination) and not replace:
            raise FileExistsError("A custom sound appeared before saving.")
        os.replace(path, destination)
        return destination
    finally:
        if os.path.exists(path):
            os.unlink(path)


def restore_default(name, directory=None):
    """Only delete the selected managed sound, not the rest of the profile."""
    path = sound_path(name, directory)
    if not path.exists() and not path.is_symlink():
        return False
    path.unlink()
    return True
