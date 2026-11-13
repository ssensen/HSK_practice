"""Keeps the app folder in step with GitHub. Start HSK Practice.bat pulls, quitting pushes.

Both PCs save answers to progress.json, so the same file often changes on both between syncs.
Git can't merge that on its own, so this file is also registered with git as the merge driver
for progress.json (see .gitattributes), and combines the two versions question by question.

Run from the command line:
    python sync.py pull                       Get the latest version from GitHub.
    python sync.py merge-progress BASE OURS THEIRS
                                              Used by git itself, not by people.
"""
import json
import os
import socket
import subprocess
import sys
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


def merge_progress(base, ours, theirs):
    """Combine two PCs' progress. Each argument maps question id to its saved item."""
    merged = {}
    for question_id in {**ours, **theirs}:
        old, mine, other = base.get(question_id), ours.get(question_id), theirs.get(question_id)
        if mine is None or mine == old:
            merged[question_id] = other
        elif other is None or other == old:
            merged[question_id] = mine
        else:
            # Answered on both PCs: keep the schedule from the latest answer, and count
            # every answer from both.
            latest = dict(max(mine, other, key=lambda item: item["last"]))
            for count in ("right", "wrong"):
                latest[count] = mine[count] + other[count] - (old or {}).get(count, 0)
            merged[question_id] = latest
    return merged


def setup_merge_driver(folder=APP_DIR):
    """Tell this PC's git to use merge_progress. Git keeps this per PC, so set it every time."""
    command = (f'"{Path(sys.executable).with_name("python.exe").as_posix()}" '
               f'"{(APP_DIR / "sync.py").as_posix()}" merge-progress %O %A %B')
    git("config", "merge.progress.name", "HSK Practice progress", folder=folder)
    git("config", "merge.progress.driver", command, folder=folder)


def pull_changes(folder=APP_DIR):
    setup_merge_driver(folder)
    try:
        git("pull", "--no-edit", folder=folder)
    except RuntimeError:
        # Don't leave the folder half merged, with conflict markers in the files.
        subprocess.run(["git", "merge", "--abort"], cwd=folder, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        raise


def push_changes(folder=APP_DIR):
    """git add, commit, merge in the other PC's changes, and push everything."""
    git("add", "-A", folder=folder)
    if git("status", "--porcelain", folder=folder).strip():
        message = f"Progress from {socket.gethostname()} {time.strftime('%Y-%m-%d %H:%M')}"
        git("commit", "-m", message, folder=folder)
    # The other PC may have pushed since this one pulled, and GitHub refuses a push until
    # its changes are merged in here.
    pull_changes(folder)
    # Also sends commits left over from an earlier quit that couldn't push.
    git("push", folder=folder)


def main(args):
    if args[:1] == ["merge-progress"] and len(args) == 4:
        base, ours, theirs = (json.loads(Path(path).read_text(encoding="utf-8") or "{}")
                              for path in args[1:])
        # Git reads the result from the OURS file, as it is stored in git: with \n line
        # endings, not Windows ones, or every line would look changed.
        with open(args[2], "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(merge_progress(base, ours, theirs), ensure_ascii=False,
                                  indent=1))
        return 0
    if args == ["pull"]:
        try:
            pull_changes()
        except RuntimeError as error:
            print(error)
            return 1
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
