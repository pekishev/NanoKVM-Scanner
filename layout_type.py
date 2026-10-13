"""План набора текста на удалённом ПК через NanoKVM.

Символ переводится в физическую US-клавишу текущей раскладки.
Если её нет в этой раскладке, перед символом нужен Alt+Shift.
Набор идёт HID-отчётами: /api/hid/paste на устройстве молча пропускает кавычки.
"""

from __future__ import annotations

from dataclasses import dataclass

# Сколько символов за один /api/hid/paste. На устройстве ~50 мс на символ,
# а обработчик режет строку длиннее 1024 байт. Длинный набор идёт HID-ом.
PASTE_CHUNK = 40

# Короткий набор шлётся нажатиями с нашего HID, без /api/hid/paste.
KEY_TYPE_MAX = 200

# То, что с клавиатуры не приходит: кавычки, пробелы, тире → ближайшая клавиша.
CHAR_FOLD = {
    "\u00ab": '"',
    "\u00bb": '"',
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u00a0": " ",
    "\u202f": " ",
    "\u2007": " ",
    "\u2009": " ",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u00ad": "",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u2026": "...",
}
QUOTE_FOLD = CHAR_FOLD

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


def _skip_char(ch: str) -> bool:
    code = ord(ch)
    return (code < 32 and ch not in "\n\t") or code == 127


def plan_paste(value: str, start: Layout) -> list[Segment]:
    """Разбивает текст на куски одной раскладки.

    Символа нет на клавиатуре и в CHAR_FOLD — пропускаем, как /api/hid/paste.
    """
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

    def append_mapped(mapped: str, index: int) -> None:
        keys.append(mapped)
        src.append(index)

    for index, ch in enumerate(value):
        if ch == "\r" or _skip_char(ch):
            continue
        folded = CHAR_FOLD.get(ch, ch)
        if folded == "":
            continue
        if folded != ch:
            for piece in folded:
                mapped = key_for(layout, piece)
                if mapped is None:
                    other: Layout = "ru" if layout == "en" else "en"
                    mapped = key_for(other, piece)
                    if mapped is None:
                        continue
                    flush()
                    layout = other
                append_mapped(mapped, index)
            continue
        mapped = key_for(layout, ch)
        if mapped is None:
            other: Layout = "ru" if layout == "en" else "en"
            mapped = key_for(other, ch)
            if mapped is None:
                continue
            flush()
            layout = other
        append_mapped(mapped, index)
    flush()
    return segments


def _us_hid_table() -> dict[str, tuple[int, int]]:
    """US-символ → (modifier, HID keycode), как CharMap в paste.go."""
    table: dict[str, tuple[int, int]] = {}
    for index, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        table[ch] = (0, 4 + index)
        table[ch.upper()] = (2, 4 + index)
    for index, ch in enumerate("123456789"):
        table[ch] = (0, 30 + index)
    table["0"] = (0, 39)
    for ch, code in {
        "!": 30,
        "@": 31,
        "#": 32,
        "$": 33,
        "%": 34,
        "^": 35,
        "&": 36,
        "*": 37,
        "(": 38,
        ")": 39,
    }.items():
        table[ch] = (2, code)
    for ch, code in {
        "\n": 40,
        "\t": 43,
        " ": 44,
        "-": 45,
        "=": 46,
        "[": 47,
        "]": 48,
        "\\": 49,
        ";": 51,
        "'": 52,
        "`": 53,
        ",": 54,
        ".": 55,
        "/": 56,
    }.items():
        table[ch] = (0, code)
    for ch, code in {
        "_": 45,
        "+": 46,
        "{": 47,
        "}": 48,
        "|": 49,
        ":": 51,
        '"': 52,
        "~": 53,
        "<": 54,
        ">": 55,
        "?": 56,
    }.items():
        table[ch] = (2, code)
    return table


US_HID = _us_hid_table()


def us_hid(ch: str) -> tuple[int, int]:
    try:
        return US_HID[ch]
    except KeyError as exc:
        raise BadChar(ch) from exc


def progress_at(text: str, sent: int) -> tuple[int, int, str]:
    """Номер текущей строки (с 1), всего строк и её текст по числу напечатанных символов."""
    if not text:
        return 1, 1, ""
    pos = min(max(int(sent), 0), len(text))
    line = text.count("\n", 0, pos) + 1
    lines = text.count("\n") + 1
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return line, lines, text[start:end].replace("\r", "")


def chunk_segment(segment: Segment, size: int = PASTE_CHUNK) -> list[Segment]:
    if size < 1:
        raise ValueError(size)
    out: list[Segment] = []
    for start in range(0, len(segment.keys), size):
        end = start + size
        out.append(Segment(segment.layout, segment.keys[start:end], segment.src[start:end]))
    return out
