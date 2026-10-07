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

    def test_unknown_character(self) -> None:
        with self.assertRaises(layout_type.BadChar) as caught:
            layout_type.plan_paste("цена €", "ru")
        self.assertEqual(caught.exception.char, "€")

    def test_chunks_keep_source_indexes(self) -> None:
        segment = layout_type.plan_paste("abcd", "en")[0]
        parts = layout_type.chunk_segment(segment, 2)
        self.assertEqual([p.keys for p in parts], ["ab", "cd"])
        self.assertEqual([p.src for p in parts], [(0, 1), (2, 3)])


if __name__ == "__main__":
    unittest.main()
