"""План набора текста на удалённом ПК через NanoKVM.

Как веб-клиент NanoKVM (paste): символ переводится в физическую US-клавишу
текущей раскладки. Если её нет в этой раскладке, перед символом нужен Alt+Shift.
POST /api/hid/paste принимает уже US-символы и сам жмёт HID.
"""

from __future__ import annotations

from dataclasses import dataclass

# Сколько символов за один /api/hid/paste. На устройстве ~50 мс на символ,
# а обработчик режет строку длиннее 1024 байт.
PASTE_CHUNK = 40

Layout = str

SHARED = set("0123456789!%*()\\-_+= \n\t")

EN_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789!@#$%^&*() \n\t-=[]\\;'`,./_+{}|:\"~<>?"
)

LETTER_MAP = {
    "ё": "`",
    "й": "q",
    "ц": "w",
    "у": "e",
    "к": "r",
    "е": "t",
    "н": "y",
    "г": "u",
    "ш": "i",
    "щ": "o",
    "з": "p",
    "х": "[",
    "ъ": "]",
    "ф": "a",
    "ы": "s",
    "в": "d",
    "а": "f",
    "п": "g",
    "р": "h",
    "о": "j",
    "л": "k",
    "д": "l",
    "ж": ";",
    "э": "'",
    "я": "z",
    "ч": "x",
    "с": "c",
    "м": "v",
    "и": "b",
    "т": "n",
    "ь": "m",
    "б": ",",
    "ю": ".",
}

PUNCT_MAP = {
    '"': "@",
    "№": "#",
    ";": "$",
    ":": "^",
    "?": "&",
    "/": "|",
    ".": "/",
    ",": "?",
}

SHIFT_MAP = {
    "`": "~",
    "-": "_",
    "=": "+",
    "[": "{",
    "]": "}",
    "\\": "|",
    ";": ":",
    "'": '"',
    ",": "<",
    ".": ">",
    "/": "?",
}


class BadChar(ValueError):
    def __init__(self, char: str) -> None:
        self.char = char
        super().__init__(char)


@dataclass(frozen=True)
class Segment:
    layout: Layout
    keys: str
    src: tuple[int, ...]


def _with_shift(key: str) -> str:
    if "a" <= key <= "z":
        return key.upper()
    return SHIFT_MAP.get(key, key)


def _en_key(ch: str) -> str | None:
    return ch if ch in EN_CHARS else None


def _ru_key(ch: str) -> str | None:
    if ch in SHARED:
        return ch
    lower = ch.lower()
    base = LETTER_MAP.get(lower)
    if base:
        return base if ch == lower else _with_shift(base)
    return PUNCT_MAP.get(ch)


def key_for(layout: Layout, ch: str) -> str | None:
    return _en_key(ch) if layout == "en" else _ru_key(ch)


def plan_paste(value: str, start: Layout) -> list[Segment]:
    """Разбивает текст на куски одной раскладки. Непечатный символ — BadChar."""
    if start not in ("en", "ru"):
        raise ValueError(start)
    layout = start
    segments: list[Segment] = []
    keys: list[str] = []
    src: list[int] = []

    def flush() -> None:
        if not keys:
            return
        segments.append(Segment(layout, "".join(keys), tuple(src)))
        keys.clear()
        src.clear()

    for index, ch in enumerate(value):
        if ch == "\r":
            continue
        mapped = key_for(layout, ch)
        if mapped is None:
            other: Layout = "ru" if layout == "en" else "en"
            mapped = key_for(other, ch)
            if mapped is None:
                raise BadChar(ch)
            flush()
            layout = other
        keys.append(mapped)
        src.append(index)
    flush()
    return segments


def chunk_segment(segment: Segment, size: int = PASTE_CHUNK) -> list[Segment]:
    if size < 1:
        raise ValueError(size)
    out: list[Segment] = []
    for start in range(0, len(segment.keys), size):
        end = start + size
        out.append(Segment(segment.layout, segment.keys[start:end], segment.src[start:end]))
    return out
