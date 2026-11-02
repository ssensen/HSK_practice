"""Asks Claude about the current question through the logged-in Claude Code CLI."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 180


def find_claude():
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    return str(fallback) if fallback.exists() else None


def system_prompt(question, chosen):
    options = "\n".join(f"- {option}" for option in question.options)
    if chosen is None:
        status = ("The learner has NOT answered yet. Do not reveal or confirm the correct "
                  "option. Explain the concepts and give hints so they can work it out.")
    else:
        verdict = "correct" if chosen == question.answer else "wrong"
        status = (f"The learner chose: {chosen} ({verdict}). "
                  f"The correct answer is: {question.answer}. "
                  f"Explanation shown to them: {question.explain}")
    return (
        "You are a patient Chinese tutor helping an English-speaking learner prepare for the "
        "HSK 3 exam. They are working at their PC and asked for help with a practice question, "
        "so keep answers short and to the point. Include pinyin and an English translation "
        "with any Chinese example, and keep examples to HSK 3 vocabulary where possible. "
        "Reply in plain text with no markdown, since this appears in a small plain text box.\n\n"
        f"The question:\n{question.prompt}\n{question.hint}\n\n"
        f"The options:\n{options}\n\n{status}"
    )


def ask(question, chosen, history):
    """Send the conversation so far. history is a list of (speaker, text); returns the reply."""
    claude = find_claude()
    if not claude:
        raise RuntimeError("Could not find the Claude CLI. Check that Claude Code is installed.")
    transcript = "\n\n".join(f"{speaker}: {text}" for speaker, text in history)
    env = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}
    command = [claude, "-p", "--system-prompt", system_prompt(question, chosen),
               "--tools", "", "--no-session-persistence", "--strict-mcp-config",
               "--setting-sources", ""]
    try:
        result = subprocess.run(
            command, input=transcript, capture_output=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, cwd=tempfile.gettempdir(), env=env,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude took too long to answer. Please try again.")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Claude could not answer: {detail or 'unknown error'}")
    return result.stdout.strip()
