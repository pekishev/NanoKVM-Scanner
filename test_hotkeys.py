"""Сочетания Ctrl/Alt/Win и спецклавиши виртуальной клавиатуры."""

from __future__ import annotations

import unittest

import hotkeys


class HotkeyTests(unittest.TestCase):
    def test_priority_combos(self) -> None:
        cases = {
            "Ctrl+C": (0x01, 0x06, "Ctrl+C"),
            "Ctrl+L": (0x01, 0x0F, "Ctrl+L"),
            "Ctrl+U": (0x01, 0x18, "Ctrl+U"),
            "Alt+Tab": (0x04, 0x2B, "Alt+Tab"),
            "Backspace": (0x00, 0x2A, "Backspace"),
            "Delete": (0x00, 0x4C, "Delete"),
            "Win+E": (0x08, 0x08, "Win+E"),
            "Ctrl+Shift+Esc": (0x01 | 0x02, 0x29, "Ctrl+Shift+Esc"),
        }
        for text, expected in cases.items():
            chord = hotkeys.parse_chord(text)
            self.assertEqual((chord.modifier, chord.keycode, chord.label), expected, text)

    def test_sequence_and_comma_key(self) -> None:
        chords = hotkeys.parse_keys("Ctrl+A, Delete")
        self.assertEqual([chord.label for chord in chords], ["Ctrl+A", "Delete"])
        comma = hotkeys.parse_chord("Ctrl+,")
        self.assertEqual(comma.label, "Ctrl+,")
        self.assertEqual(comma.keycode, 0x36)

    def test_unknown_key(self) -> None:
        with self.assertRaises(ValueError):
            hotkeys.parse_chord("Ctrl+Nope")

    def test_virtual_keyboard_row(self) -> None:
        for name in ("Esc", "F12", "Tab", "Enter", "Space", "CapsLock", "Up", "PageDown", "Menu"):
            chord = hotkeys.parse_chord(name)
            self.assertGreater(chord.keycode, 0)


if __name__ == "__main__":
    unittest.main()
