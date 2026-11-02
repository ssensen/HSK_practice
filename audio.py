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
