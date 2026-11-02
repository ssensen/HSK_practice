"""Builds data/deck.json from the Anki deck. Only rerun this if the deck changes."""
import html
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DECK_FILE = APP_DIR / "Mandarin_Vocabulary_HSK_1-5.apkg"
OUTPUT_FILE = APP_DIR / "data" / "deck.json"


def clean(text):
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def sound_file(field):
    found = re.search(r"\[sound:([^\]]+)\]", field)
    return found.group(1) if found else None


def read_notes(deck_file):
    with zipfile.ZipFile(deck_file) as deck, tempfile.TemporaryDirectory() as folder:
        media = {name: member for member, name in json.loads(deck.read("media")).items()}
        deck.extract("collection.anki21", folder)
        db = sqlite3.connect(Path(folder) / "collection.anki21")
        models = json.loads(db.execute("select models from col").fetchone()[0])
        field_names = {int(mid): [f["name"] for f in model["flds"]]
                       for mid, model in models.items()}
        rows = db.execute("select mid, flds, tags from notes").fetchall()
        db.close()
    return media, [(dict(zip(field_names[mid], flds.split("\x1f"))), tags)
                   for mid, flds, tags in rows]


def build_entry(fields, tags, media):
    level = re.search(r"\bHSK(\d)\b", tags)
    word_sound = sound_file(fields["Audio"])
    sentence_sound = sound_file(fields["SentenceAudio"])
    return {
        "key": fields["Key"],
        "level": int(level.group(1)) if level else None,
        "word": clean(fields["Simplified"]),
        "pinyin": clean(fields["Pinyin.1"]),
        "meaning": clean(fields["Meaning"]),
        "pos": clean(fields["Part of speech"]),
        "sentence": clean(fields["SentenceSimplified"]),
        "cloze": clean(fields["SentenceSimplifiedCloze"]),
        "sentence_pinyin": clean(fields["SentencePinyin.1"]),
        "sentence_meaning": clean(fields["SentenceMeaning"]),
        "word_audio": media.get(word_sound),
        "sentence_audio": media.get(sentence_sound),
    }


def main():
    media, notes = read_notes(DECK_FILE)
    entries = [build_entry(fields, tags, media) for fields, tags in notes]
    entries.sort(key=lambda entry: int(entry["key"]) if entry["key"].isdigit() else 0)
    OUTPUT_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"Wrote {len(entries)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
