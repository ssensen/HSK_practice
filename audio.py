"""Plays the deck's pronunciation clips, reading each one straight from the .apkg file."""
import ctypes
import tempfile
import zipfile
from pathlib import Path

from build_deck import DECK_FILE

ALIAS = "hsk_clip"
CLIP_FILE = Path(tempfile.gettempdir()) / "hsk_practice_clip.mp3"

mci = ctypes.windll.winmm.mciSendStringW


def stop():
    mci(f"close {ALIAS}", None, 0, None)


def length_ms():
    """Length of the clip that was played last, or 0 if it isn't known."""
    buffer = ctypes.create_unicode_buffer(32)
    mci(f"set {ALIAS} time format milliseconds", None, 0, None)
    if mci(f"status {ALIAS} length", buffer, len(buffer), None) != 0:
        return 0
    return int(buffer.value) if buffer.value.isdigit() else 0


def play(member):
    """Play one clip by its name inside the deck. Returns False if it can't be played."""
    stop()
    try:
        with zipfile.ZipFile(DECK_FILE) as deck:
            CLIP_FILE.write_bytes(deck.read(member))
    except (OSError, KeyError, TypeError, zipfile.BadZipFile):
        return False
    if mci(f'open "{CLIP_FILE}" type mpegvideo alias {ALIAS}', None, 0, None) != 0:
        return False
    return mci(f"play {ALIAS}", None, 0, None) == 0
