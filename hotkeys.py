"""Сочетания клавиш в HID-отчёты, как виртуальная клавиатура NanoKVM.

Модификатор — бит в первом байте отчёта, обычная клавиша — keycode.
Ctrl+C, Alt+Tab, Backspace и Delete задаются теми же именами, что на клавиатуре.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REPEAT_MAX = 40
SEQUENCE_MAX = 32

# Левые модификаторы по умолчанию. Правые — отдельными именами.
_MODIFIERS: dict[str, tuple[int, str]] = {
    "ctrl": (0x01, "Ctrl"),
    "control": (0x01, "Ctrl"),
    "lctrl": (0x01, "Ctrl"),
    "leftctrl": (0x01, "Ctrl"),
    "controlleft": (0x01, "Ctrl"),
    "rctrl": (0x10, "RCtrl"),
    "rightctrl": (0x10, "RCtrl"),
    "controlright": (0x10, "RCtrl"),
    "shift": (0x02, "Shift"),
    "lshift": (0x02, "Shift"),
    "leftshift": (0x02, "Shift"),
    "shiftleft": (0x02, "Shift"),
    "rshift": (0x20, "RShift"),
    "rightshift": (0x20, "RShift"),
    "shiftright": (0x20, "RShift"),
    "alt": (0x04, "Alt"),
    "lalt": (0x04, "Alt"),
    "leftalt": (0x04, "Alt"),
    "altleft": (0x04, "Alt"),
    "ralt": (0x40, "RAlt"),
    "rightalt": (0x40, "RAlt"),
    "altright": (0x40, "RAlt"),
    "altgr": (0x40, "RAlt"),
    "win": (0x08, "Win"),
    "windows": (0x08, "Win"),
    "meta": (0x08, "Win"),
    "cmd": (0x08, "Win"),
    "super": (0x08, "Win"),
    "gui": (0x08, "Win"),
    "winleft": (0x08, "Win"),
    "metaleft": (0x08, "Win"),
    "winright": (0x80, "RWin"),
    "metaright": (0x80, "RWin"),
    "rwin": (0x80, "RWin"),
}

_MOD_ORDER = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)
_MOD_CANON = {
    0x01: "Ctrl",
    0x02: "Shift",
    0x04: "Alt",
    0x08: "Win",
    0x10: "RCtrl",
    0x20: "RShift",
    0x40: "RAlt",
    0x80: "RWin",
}


def _letters() -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for index, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        out[ch] = (0x04 + index, ch.upper())
        out[f"key{ch}"] = (0x04 + index, ch.upper())
    return out


def _digits() -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for index, ch in enumerate("123456789"):
        out[ch] = (0x1E + index, ch)
        out[f"digit{ch}"] = (0x1E + index, ch)
    out["0"] = (0x27, "0")
    out["digit0"] = (0x27, "0")
    return out


def _named() -> dict[str, tuple[int, str]]:
    rows = {
        "esc": (0x29, "Esc"),
        "escape": (0x29, "Esc"),
        "tab": (0x2B, "Tab"),
        "enter": (0x28, "Enter"),
        "return": (0x28, "Enter"),
        "space": (0x2C, "Space"),
        "spacebar": (0x2C, "Space"),
        "backspace": (0x2A, "Backspace"),
        "bksp": (0x2A, "Backspace"),
        "bs": (0x2A, "Backspace"),
        "delete": (0x4C, "Delete"),
        "del": (0x4C, "Delete"),
        "insert": (0x49, "Insert"),
        "ins": (0x49, "Insert"),
        "home": (0x4A, "Home"),
        "end": (0x4D, "End"),
        "pageup": (0x4B, "PageUp"),
        "pgup": (0x4B, "PageUp"),
        "pagedown": (0x4E, "PageDown"),
        "pgdn": (0x4E, "PageDown"),
        "up": (0x52, "Up"),
        "arrowup": (0x52, "Up"),
        "down": (0x51, "Down"),
        "arrowdown": (0x51, "Down"),
        "left": (0x50, "Left"),
        "arrowleft": (0x50, "Left"),
        "right": (0x4F, "Right"),
        "arrowright": (0x4F, "Right"),
        "capslock": (0x39, "CapsLock"),
        "caps": (0x39, "CapsLock"),
        "menu": (0x65, "Menu"),
        "apps": (0x65, "Menu"),
        "contextmenu": (0x65, "Menu"),
        "printscreen": (0x46, "PrintScreen"),
        "prtsc": (0x46, "PrintScreen"),
        "scrolllock": (0x47, "ScrollLock"),
        "pause": (0x48, "Pause"),
        "numlock": (0x53, "NumLock"),
        "`": (0x35, "`"),
        "backquote": (0x35, "`"),
        "grave": (0x35, "`"),
        "-": (0x2D, "-"),
        "minus": (0x2D, "-"),
        "=": (0x2E, "="),
        "equal": (0x2E, "="),
        "[": (0x2F, "["),
        "bracketleft": (0x2F, "["),
        "]": (0x30, "]"),
        "bracketright": (0x30, "]"),
        "\\": (0x31, "\\"),
        "backslash": (0x31, "\\"),
        ";": (0x33, ";"),
        "semicolon": (0x33, ";"),
        "'": (0x34, "'"),
        "quote": (0x34, "'"),
        ",": (0x36, ","),
        "comma": (0x36, ","),
        ".": (0x37, "."),
        "period": (0x37, "."),
        "/": (0x38, "/"),
        "slash": (0x38, "/"),
    }
    for index in range(1, 13):
        rows[f"f{index}"] = (0x3A + index - 1, f"F{index}")
    for index in range(10):
        rows[f"numpad{index}"] = (0x62 if index == 0 else 0x58 + index, f"Num{index}")
    rows["numpadenter"] = (0x58, "NumEnter")
    rows["numpadadd"] = (0x57, "Num+")
    rows["numpadsubtract"] = (0x56, "Num-")
    rows["numpadmultiply"] = (0x55, "Num*")
    rows["numpaddivide"] = (0x54, "Num/")
    rows["numpaddecimal"] = (0x63, "Num.")
    return rows


KEYS: dict[str, tuple[int, str]] = {}
KEYS.update(_letters())
KEYS.update(_digits())
KEYS.update(_named())


@dataclass(frozen=True)
class Chord:
    modifier: int
    keycode: int
    label: str


def _token(raw: str) -> str:
    text = raw.strip().lower()
    compact = re.sub(r"[\s_\-]+", "", text)
    return compact or text


def parse_chord(text: str) -> Chord:
    raw = text.strip()
    if not raw:
        raise ValueError("пустая комбинация")
    parts = [part.strip() for part in raw.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"непонятное сочетание «{raw}»")
    modifier = 0
    keycode = 0
    key_label = ""
    seen_mod: list[int] = []
    for part in parts:
        name = _token(part)
        if name in _MODIFIERS:
            bit, _label = _MODIFIERS[name]
            modifier |= bit
            if bit not in seen_mod:
                seen_mod.append(bit)
            continue
        if keycode:
            raise ValueError(f"в сочетании одна клавиша, не две: {raw}")
        found = KEYS.get(name)
        if found is None:
            raise ValueError(f"неизвестная клавиша «{part}»")
        keycode, key_label = found
    if not modifier and not keycode:
        raise ValueError(f"непонятное сочетание «{raw}»")
    mods = [ _MOD_CANON[bit] for bit in _MOD_ORDER if modifier & bit ]
    label = "+".join([*mods, key_label] if key_label else mods)
    return Chord(modifier, keycode, label)


def split_sequence(text: str) -> list[str]:
    """Несколько сочетаний: с новой строки, через «;» или через «, »."""
    chunks = re.split(r"[\n;]+", text.strip())
    out: list[str] = []
    for chunk in chunks:
        buf = ""
        index = 0
        body = chunk.strip()
        while index < len(body):
            if body[index] == "," and buf and not buf.rstrip().endswith("+"):
                rest = body[index + 1 :].strip()
                if rest:
                    out.append(buf.strip())
                    buf = ""
                    index += 1
                    while index < len(body) and body[index] == " ":
                        index += 1
                    continue
            buf += body[index]
            index += 1
        if buf.strip():
            out.append(buf.strip())
    return [item for item in out if item]


def parse_keys(value: str | list[str]) -> list[Chord]:
    parts: list[str] = []
    if isinstance(value, list):
        for item in value:
            parts.extend(split_sequence(str(item)))
    else:
        parts = split_sequence(str(value))
    if not parts:
        raise ValueError("пустая комбинация")
    if len(parts) > SEQUENCE_MAX:
        raise ValueError(f"не больше {SEQUENCE_MAX} сочетаний за раз")
    return [parse_chord(part) for part in parts]
