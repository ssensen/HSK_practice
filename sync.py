"""Saves this PC's changes to GitHub when the app quits. Start HSK Practice.bat pulls them."""
import os
import socket
import subprocess
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 60


def git(*args, folder=APP_DIR):
    # No terminal to type a password into, so fail instead of waiting for one.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *args], cwd=folder, capture_output=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError("Git isn't installed, or isn't on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {args[0]} took too long. Check the internet connection.")
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed:\n{(result.stderr or result.stdout).strip()}")
    return result.stdout


def push_changes(folder=APP_DIR):
    """git add, commit and push everything in the app folder."""
    git("add", "-A", folder=folder)
    if git("status", "--porcelain", folder=folder).strip():
        message = f"Progress from {socket.gethostname()} {time.strftime('%Y-%m-%d %H:%M')}"
        git("commit", "-m", message, folder=folder)
    # Also sends commits left over from an earlier quit that couldn't push.
    git("push", folder=folder)
