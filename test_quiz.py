import tempfile
import unittest
import zipfile
from pathlib import Path

import build_deck
import quiz


class BankTests(unittest.TestCase):
    def test_every_question_is_well_formed_at_every_level(self):
        for level in (1, 3, 5):
            bank = quiz.load_bank(level)
            self.assertTrue(bank)
            for question_id, build in bank.items():
                question = build()
                self.assertEqual(len(set(question.options)), 4, question_id)
                self.assertIn(question.answer, question.options, question_id)
                self.assertTrue(question.main_audio, question_id)

    def test_level_setting_limits_the_words(self):
        self.assertEqual({entry["level"] for entry in quiz.load_entries(3)}, {1, 2, 3})
        self.assertLess(len(quiz.load_bank(3)), len(quiz.load_bank(5)))

    def test_blanks_always_restore_the_real_sentence(self):
        entries = {entry["word"]: entry for entry in quiz.load_entries(5)}
        self.assertFalse(quiz.can_blank(entries["路"]))  # The deck blanks 长 on this card.
        self.assertTrue(quiz.can_blank(entries["猜"]))  # 猜猜 has two blanks and still matches.
        for build in quiz.load_bank(5).values():
            question = build()
            if question.kind == "cloze":
                self.assertIn("___", question.prompt)
                self.assertTrue(question.hint)

    def test_distractors_never_share_a_sense_with_the_answer(self):
        entries = quiz.load_entries(3)
        target = next(entry for entry in entries if entry["word"] == "我")
        for _ in range(100):
            wrong = quiz.pick_wrong(entries, target, "meaning")
            self.assertTrue(all(target["meaning"] != meaning for meaning in wrong))

    def test_words_with_two_readings_stay_separate(self):
        bank = quiz.load_bank(5)
        self.assertEqual(sum(1 for qid in bank if qid.startswith("pinyin:长#")), 2)

    def test_all_audio_clips_exist_in_the_deck(self):
        with zipfile.ZipFile(build_deck.DECK_FILE) as deck:
            members = set(deck.namelist())
        for entry in quiz.load_entries(5):
            self.assertIn(entry["word_audio"], members)
            if entry["sentence_audio"]:
                self.assertIn(entry["sentence_audio"], members)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "progress.json"
        self.progress = quiz.Progress(self.path)
        self.bank = {"meaning:a": None, "meaning:b": None, "cloze:a": None}

    def test_wrong_answer_returns_after_retry_delay(self):
        self.progress.record("cloze:a", False, now=1000)
        self.assertEqual(self.progress.items["cloze:a"]["due"], 1000 + quiz.RETRY_WRONG_SECONDS)

    def test_right_answers_space_out_and_wrong_resets(self):
        self.progress.record("cloze:a", True, now=0)
        self.assertEqual(self.progress.items["cloze:a"]["due"], 86400)
        self.progress.record("cloze:a", True, now=0)
        self.assertEqual(self.progress.items["cloze:a"]["due"], 3 * 86400)
        self.progress.record("cloze:a", False, now=0)
        self.assertEqual(self.progress.items["cloze:a"]["box"], 0)

    def test_overdue_review_beats_new_question(self):
        self.progress.record("cloze:a", False, now=0)
        self.assertEqual(self.progress.choose(self.bank, now=10 ** 6), "cloze:a")

    def test_not_yet_due_question_gives_way_to_new_one(self):
        self.progress.record("cloze:a", False, now=0)
        self.assertNotEqual(self.progress.choose(self.bank, now=1), "cloze:a")

    def test_progress_survives_reload(self):
        self.progress.record("cloze:a", True, now=0)
        self.assertEqual(quiz.Progress(self.path).items["cloze:a"]["right"], 1)


if __name__ == "__main__":
    unittest.main()
