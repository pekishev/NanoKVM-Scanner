"""План HID-набора: раскладка и физические клавиши."""

from __future__ import annotations

import unittest

import layout_type


class PlanPasteTests(unittest.TestCase):
    def test_english_stays_on_english(self) -> None:
        segments = layout_type.plan_paste("Hello, world!", "en")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].layout, "en")
        self.assertEqual(segments[0].keys, "Hello, world!")

    def test_russian_from_english_switches_once(self) -> None:
        segments = layout_type.plan_paste("привет", "en")
        self.assertEqual([(s.layout, s.keys) for s in segments], [("ru", "ghbdtn")])

    def test_capital_russian_uses_shift(self) -> None:
        segments = layout_type.plan_paste("Я", "en")
        self.assertEqual(segments[0].keys, "Z")
        self.assertEqual(layout_type.plan_paste("Ё", "ru")[0].keys, "~")

    def test_mixed_text_splits_on_layout(self) -> None:
        segments = layout_type.plan_paste("hi мир", "en")
        self.assertEqual([(s.layout, s.keys) for s in segments], [("en", "hi "), ("ru", "vbh")])

    def test_space_does_not_leave_russian(self) -> None:
        segments = layout_type.plan_paste("привет мир", "en")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].layout, "ru")
        self.assertEqual(segments[0].keys, "ghbdtn vbh")

    def test_russian_punctuation_stays_on_russian(self) -> None:
        segments = layout_type.plan_paste("а,б", "ru")
        self.assertEqual(segments[0].layout, "ru")
        self.assertEqual(segments[0].keys, "f?,")

    def test_digits_do_not_switch(self) -> None:
        segments = layout_type.plan_paste("12", "ru")
        self.assertEqual(segments[0].layout, "ru")
        self.assertEqual(segments[0].keys, "12")

    def test_cr_is_skipped_and_newline_is_enter(self) -> None:
        segments = layout_type.plan_paste("a\r\nb", "en")
        self.assertEqual(segments[0].keys, "a\nb")
        self.assertEqual(segments[0].src, (0, 2, 3))

    def test_unknown_character_is_skipped(self) -> None:
        segments = layout_type.plan_paste("цена €", "ru")
        self.assertEqual("".join(s.keys for s in segments), "wtyf ")

    def test_copyright_is_skipped(self) -> None:
        segments = layout_type.plan_paste("Copyright © 2024", "en")
        self.assertEqual("".join(s.keys for s in segments), "Copyright  2024")

    def test_nbsp_becomes_space(self) -> None:
        segments = layout_type.plan_paste("a\u00a0b", "en")
        self.assertEqual(segments[0].keys, "a b")

    def test_chunks_keep_source_indexes(self) -> None:
        segment = layout_type.plan_paste("abcd", "en")[0]
        parts = layout_type.chunk_segment(segment, 2)
        self.assertEqual([p.keys for p in parts], ["ab", "cd"])
        self.assertEqual([p.src for p in parts], [(0, 1), (2, 3)])

    def test_every_planned_key_has_hid_code(self) -> None:
        text = "Hello, мир!\n\t0123 №"
        for segment in layout_type.plan_paste(text, "en"):
            for ch in segment.keys:
                modifier, code = layout_type.us_hid(ch)
                self.assertGreaterEqual(code, 4)
                self.assertIn(modifier, (0, 2))

    def test_us_hid_matches_paste_map(self) -> None:
        self.assertEqual(layout_type.us_hid("a"), (0, 4))
        self.assertEqual(layout_type.us_hid("A"), (2, 4))
        self.assertEqual(layout_type.us_hid("1"), (0, 30))
        self.assertEqual(layout_type.us_hid("!"), (2, 30))
        self.assertEqual(layout_type.us_hid("\n"), (0, 40))
        self.assertEqual(layout_type.us_hid(" "), (0, 44))

    def test_ascii_quotes_stay_on_english(self) -> None:
        segments = layout_type.plan_paste('grep "Failed"', "en")
        self.assertEqual(segments[0].keys, 'grep "Failed"')
        self.assertEqual(layout_type.us_hid('"'), (2, 52))
        self.assertEqual(layout_type.us_hid("'"), (0, 52))

    def test_unicode_quotes_become_ascii(self) -> None:
        segments = layout_type.plan_paste("grep \u201cFailed\u201d", "en")
        self.assertEqual(segments[0].keys, 'grep "Failed"')
        singles = layout_type.plan_paste("it\u2019s", "en")
        self.assertEqual(singles[0].keys, "it's")

    def test_progress_stays_on_first_line(self) -> None:
        self.assertEqual(layout_type.progress_at("hello\nworld", 3), (1, 2, "hello"))

    def test_progress_moves_after_newline(self) -> None:
        self.assertEqual(layout_type.progress_at("hello\nworld", 6), (2, 2, "world"))

    def test_progress_empty_middle_line(self) -> None:
        self.assertEqual(layout_type.progress_at("a\n\nb", 2), (2, 3, ""))

    def test_progress_empty_text(self) -> None:
        self.assertEqual(layout_type.progress_at("", 0), (1, 1, ""))

    def test_progress_crlf_uses_source_index(self) -> None:
        self.assertEqual(layout_type.progress_at("a\r\nb", 1), (1, 2, "a"))
        self.assertEqual(layout_type.progress_at("a\r\nb", 3), (2, 2, "b"))


if __name__ == "__main__":
    unittest.main()
