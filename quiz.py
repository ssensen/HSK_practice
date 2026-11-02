"""Question bank, spaced repetition and saved progress. No UI code here."""
import collections
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
PROGRESS_FILE = APP_DIR / "progress.json"
SETTINGS_FILE = APP_DIR / "settings.json"

DEFAULT_SETTINGS = {"interval_minutes": 30, "snooze_minutes": 10, "max_level": 3,
                    "autoplay": True, "gaming_mode": False}

# How long until a question returns, by Leitner box. A wrong answer resets to box 0.
RETRY_WRONG_SECONDS = 20 * 60
BOX_DAYS = {1: 1, 2: 3, 3: 7, 4: 21}
MAX_BOX = max(BOX_DAYS)

# Share of brand-new questions that use a full sentence rather than a single word.
SENTENCE_SHARE = 0.5
SENTENCE_KINDS = ("cloze", "sentence")


@dataclass
class Question:
    id: str
    kind: str  # "meaning", "word", "pinyin", "cloze" or "sentence"
    prompt: str
    answer: str
    options: list
    explain: str
    hint: str = ""
    word_audio: str = None
    sentence_audio: str = None

    @property
    def main_audio(self):
        """The sentence for sentence questions, otherwise the word."""
        if self.kind in SENTENCE_KINDS:
            return self.sentence_audio or self.word_audio
        return self.word_audio


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, value):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_settings():
    return {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}


def save_settings(settings):
    save_json(SETTINGS_FILE, settings)


def load_entries(max_level=3):
    return [entry for entry in load_json(DATA_DIR / "deck.json", [])
            if entry["level"] <= max_level]


def senses(entry):
    return {sense.strip().lower() for sense in entry["meaning"].split(";")}


def can_blank(entry):
    """The deck's blanks are only trusted if the word restores the exact sentence."""
    return ("[ ]" in entry["cloze"] and entry["sentence_meaning"]
            and entry["cloze"].replace("[ ]", entry["word"]) == entry["sentence"])


def describe(entry):
    lines = [f"{entry['word']} ({entry['pinyin']}): {entry['meaning']}"]
    if entry["sentence"]:
        lines += [entry["sentence"], entry["sentence_pinyin"], entry["sentence_meaning"]]
    return "\n".join(line for line in lines if line)


def pick_wrong(entries, entry, field, same_pos=False, avoid_text=""):
    """Three distractors that can't also be right: other words, with no shared sense."""
    pool = [other for other in entries
            if other["word"] != entry["word"] and other[field] and other[field] != entry[field]
            and not senses(other) & senses(entry) and other["word"] not in avoid_text]
    if same_pos:
        matching = [other for other in pool if other["pos"] == entry["pos"]]
        if len({other[field] for other in matching}) >= 3:
            pool = matching
    return random.sample(sorted({other[field] for other in pool}), 3)


def build_question(entries, entry, kind, question_id):
    word, pinyin, meaning = entry["word"], entry["pinyin"], entry["meaning"]
    hint = ""
    if kind == "meaning":
        prompt, answer = f"“{word}” 是什么意思？", meaning
        wrong = pick_wrong(entries, entry, "meaning")
    elif kind == "word":
        prompt, answer = f"Which word means “{meaning}”?", word
        wrong = pick_wrong(entries, entry, "word", same_pos=True)
    elif kind == "pinyin":
        prompt, answer = f"“{word}” 怎么读？", pinyin
        wrong = pick_wrong(entries, entry, "pinyin")
    elif kind == "cloze":
        prompt, answer = entry["cloze"].replace("[ ]", "___"), word
        hint = entry["sentence_meaning"]
        wrong = pick_wrong(entries, entry, "word", same_pos=True, avoid_text=entry["sentence"])
    else:
        prompt, answer = entry["sentence"], entry["sentence_meaning"]
        hint = "这句话是什么意思？"
        wrong = pick_wrong(entries, entry, "sentence_meaning")
    options = [answer] + wrong
    random.shuffle(options)
    return Question(question_id, kind, prompt, answer, options, describe(entry), hint,
                    entry["word_audio"], entry["sentence_audio"])


def load_bank(max_level=3):
    """Return every question id mapped to a function that builds it with fresh distractors."""
    entries = load_entries(max_level)
    counts = collections.Counter(entry["word"] for entry in entries)
    bank = {}
    for entry in entries:
        # A few words appear twice with different readings, like 长 (cháng, zhǎng).
        name = entry["word"] if counts[entry["word"]] == 1 else f"{entry['word']}#{entry['key']}"
        kinds = ["meaning", "word", "pinyin"]
        if can_blank(entry):
            kinds.append("cloze")
        if entry["sentence"] and entry["sentence_meaning"]:
            kinds.append("sentence")
        for kind in kinds:
            question_id = f"{kind}:{name}"
            bank[question_id] = lambda e=entry, k=kind, q=question_id: build_question(
                entries, e, k, q)
    return bank


class Progress:
    def __init__(self, path=PROGRESS_FILE):
        self.path = path
        self.items = load_json(path, {})

    def record(self, question_id, correct, now=None):
        now = time.time() if now is None else now
        item = self.items.setdefault(
            question_id, {"box": 0, "due": 0, "right": 0, "wrong": 0, "last": 0})
        item["last"] = now
        if correct:
            item["right"] += 1
            item["box"] = min(item["box"] + 1, MAX_BOX)
            item["due"] = now + BOX_DAYS[item["box"]] * 86400
        else:
            item["wrong"] += 1
            item["box"] = 0
            item["due"] = now + RETRY_WRONG_SECONDS
        save_json(self.path, self.items)

    def choose(self, bank, now=None):
        """Pick the question id to ask: overdue reviews first, then new, then soonest due."""
        now = time.time() if now is None else now
        seen = {qid: item for qid, item in self.items.items() if qid in bank}
        due = [qid for qid, item in seen.items() if item["due"] <= now]
        if due:
            return min(due, key=lambda qid: seen[qid]["due"])
        unseen = [qid for qid in bank if qid not in seen]
        if unseen:
            sentence = [qid for qid in unseen if qid.split(":")[0] in SENTENCE_KINDS]
            single = [qid for qid in unseen if qid.split(":")[0] not in SENTENCE_KINDS]
            use_sentence = sentence and (not single or random.random() < SENTENCE_SHARE)
            return random.choice(sentence if use_sentence else single)
        return min(seen, key=lambda qid: seen[qid]["due"])

    def stats(self, bank, now=None):
        now = time.time() if now is None else now
        right = sum(item["right"] for item in self.items.values())
        wrong = sum(item["wrong"] for item in self.items.values())
        total = right + wrong
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        return {
            "answered": total,
            "accuracy": round(100 * right / total) if total else 0,
            "today": sum(1 for item in self.items.values() if item["last"] >= midnight),
            "learned": sum(1 for qid, item in self.items.items()
                           if qid in bank and item["box"] >= 2),
            "total": len(bank),
        }
