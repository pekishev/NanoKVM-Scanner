"""Локальное OCR сканов: полосы 12–20 строк → Ollama OCR-VLM → сшивка страниц."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[misc, assignment]

MIN_STRIP_LINES = 12
MAX_STRIP_LINES = 20
DEFAULT_STRIP_LINES = 16
STRIP_OVERLAP_LINES = 2
PAGE_OVERLAP_WINDOW = 64
OUTPUT_NAME = "_recognized.md"
INDEX_NAME = "_index.md"
INDEX_JSON = "_index.json"
OCR_DIRNAME = "ocr"
DEFAULT_MODEL = "glm-ocr"
OCR_NUM_PREDICT = 1536
OCR_OPTIONS = {
    "temperature": 0,
    "num_predict": OCR_NUM_PREDICT,
    "num_ctx": 4096,
    "repeat_penalty": 1.18,
    "repeat_last_n": 128,
}

GENERIC_PROMPT = (
    "Transcribe the visible text exactly, top to bottom, one output line per visible line. "
    "Keep leading +, -, @@, spaces, punctuation and indentation as shown. "
    "Do not add markdown, code fences, headings, or commentary. "
    "Do not repeat any line or block. Stop after the last visible line."
)
GLM_PROMPT = "Text Recognition:\n" + GENERIC_PROMPT
TERMINAL_PROMPT = (
    "Text Recognition:\n"
    "Transcribe the terminal or console text exactly, top to bottom. "
    "Keep FAIL, errors, quotes, punctuation, spaces and line breaks as shown. "
    "Do not add markdown, code fences, or commentary."
)


def ollama_base() -> str:
    host = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    parsed = urllib.parse.urlparse(host)
    hostname = parsed.hostname or "127.0.0.1"
    if hostname in ("0.0.0.0", "::", "[::]"):
        hostname = "127.0.0.1"
    port = parsed.port or 11434
    scheme = parsed.scheme or "http"
    return f"{scheme}://{hostname}:{port}"


def clamp_strip_lines(n: int | None) -> int:
    try:
        value = int(n or DEFAULT_STRIP_LINES)
    except (TypeError, ValueError):
        value = DEFAULT_STRIP_LINES
    return max(MIN_STRIP_LINES, min(MAX_STRIP_LINES, value))


def model_installed(installed: list[str]) -> bool:
    target = DEFAULT_MODEL.lower()
    for name in installed:
        n = name.lower()
        if n == target or n.startswith(target + ":") or n.split(":")[0] == target:
            return True
    return False


def _ollama_json(path: str, payload: dict[str, Any] | None = None, timeout: float = 8) -> Any:
    url = ollama_base() + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"нет связи с Ollama ({ollama_base()}): {exc.reason}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def list_installed_models() -> list[str]:
    data = _ollama_json("/api/tags", timeout=5)
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def require_model() -> None:
    if not model_installed(list_installed_models()):
        raise RuntimeError(f"модель {DEFAULT_MODEL} не найдена в Ollama. ollama pull {DEFAULT_MODEL}")


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    idx = int(round((len(ys) - 1) * p / 100.0))
    return ys[max(0, min(len(ys) - 1, idx))]


def _moving_avg(xs: list[float], k: int) -> list[float]:
    if k <= 1 or len(xs) <= 1:
        return list(xs)
    half = max(1, k // 2)
    n = len(xs)
    acc = [0.0]
    for x in xs:
        acc.append(acc[-1] + x)
    out: list[float] = []
    for i in range(n):
        a = max(0, i - half)
        b = min(n, i + half + 1)
        out.append((acc[b] - acc[a]) / (b - a))
    return out


def detect_line_boxes(img: Image.Image) -> list[tuple[int, int]]:
    gray = img.convert("L")
    w, h = gray.size
    if w < 8 or h < 8:
        return [(0, h)]
    raw = gray.tobytes()
    darkness: list[float] = []
    for y in range(h):
        row = raw[y * w : (y + 1) * w]
        darkness.append(255.0 - (sum(row) / w))
    smooth = _moving_avg(darkness, 3)
    lo = _percentile(smooth, 18)
    hi = _percentile(smooth, 86)
    if hi <= lo + 4:
        return []
    thresh = lo + 0.22 * (hi - lo)
    text = [v > thresh for v in smooth]
    boxes: list[tuple[int, int]] = []
    y = 0
    while y < h:
        if not text[y]:
            y += 1
            continue
        y0 = y
        while y < h and text[y]:
            y += 1
        boxes.append((y0, y))
    merged: list[tuple[int, int]] = []
    for box in boxes:
        if merged and box[0] - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], box[1])
        else:
            merged.append(box)
    cleaned: list[tuple[int, int]] = []
    for y0, y1 in merged:
        height = y1 - y0
        if height < 3:
            continue
        if height > 96:
            # Слишком высокий блок — режем по типичной высоте строки.
            typical = 22
            if cleaned:
                typical = max(10, int(sum(b[1] - b[0] for b in cleaned[-8:]) / min(8, len(cleaned))))
            yy = y0
            while yy < y1:
                cleaned.append((yy, min(y1, yy + typical)))
                yy += typical
            continue
        cleaned.append((y0, y1))
    return cleaned


def _fallback_bands(height: int, lines_per_strip: int) -> list[tuple[int, int]]:
    line_h = 22
    body = max(80, lines_per_strip * line_h)
    step = max(40, (lines_per_strip - STRIP_OVERLAP_LINES) * line_h)
    bands: list[tuple[int, int]] = []
    y = 0
    while y < height:
        y1 = min(height, y + body)
        bands.append((y, y1))
        if y1 >= height:
            break
        y += step
    return bands or [(0, height)]


def group_line_strips(
    boxes: list[tuple[int, int]],
    height: int,
    lines_per_strip: int,
    overlap: int = STRIP_OVERLAP_LINES,
) -> list[tuple[int, int]]:
    if not boxes:
        return _fallback_bands(height, lines_per_strip)
    n = max(1, lines_per_strip)
    ov = max(0, min(overlap, n - 1))
    step = max(1, n - ov)
    pad = 4
    if boxes:
        median = sorted(y1 - y0 for y0, y1 in boxes)[len(boxes) // 2]
        pad = max(3, min(12, median // 4))
    strips: list[tuple[int, int]] = []
    i = 0
    while i < len(boxes):
        chunk = boxes[i : i + n]
        y0 = max(0, chunk[0][0] - pad)
        y1 = min(height, chunk[-1][1] + pad)
        if y1 > y0:
            strips.append((y0, y1))
        if i + n >= len(boxes):
            break
        i += step
    return strips or [(0, height)]


def _encode_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def crop_jpeg_to_png(
    jpeg: bytes, box: tuple[float, float, float, float] | None = None
) -> tuple[bytes, int, int]:
    """Вырезает область кадра в PNG. Мелкую полосу увеличивает — так glm-ocr лучше читает 3 строки."""
    if Image is None:
        raise RuntimeError("для OCR нужен Pillow")
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    width, height = img.size
    if box is not None:
        x, y, rw, rh = box
        left = max(0, min(width - 1, int(round(width * x))))
        top = max(0, min(height - 1, int(round(height * y))))
        right = max(left + 1, min(width, int(round(width * (x + rw)))))
        bottom = max(top + 1, min(height, int(round(height * (y + rh)))))
        img = img.crop((left, top, right, bottom))
    w, h = img.size
    if h < 120:
        scale = 120 / h
        img = img.resize((max(1, int(round(w * scale))), 120), Image.Resampling.NEAREST)
    return _encode_png(img), img.size[0], img.size[1]


def read_frame_text(
    jpeg: bytes,
    box: tuple[float, float, float, float] | None = None,
    *,
    timeout: float = 90,
) -> tuple[str, int, int]:
    png, width, height = crop_jpeg_to_png(jpeg, box)
    text = ollama_ocr_image(png, timeout=timeout, prompt=TERMINAL_PROMPT)
    return text, width, height


def slice_page(jpeg: bytes, lines_per_strip: int = DEFAULT_STRIP_LINES) -> list[bytes]:
    if Image is None:
        raise RuntimeError("нужен Pillow: pip install Pillow")
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    n = clamp_strip_lines(lines_per_strip)
    boxes = detect_line_boxes(img)
    bands = group_line_strips(boxes, h, n)
    strips: list[bytes] = []
    for y0, y1 in bands:
        y0 = max(0, min(h - 1, y0))
        y1 = max(y0 + 1, min(h, y1))
        crop = img.crop((0, y0, w, y1))
        strips.append(_encode_png(crop))
    return strips or [_encode_png(img)]


def encode_index_image(jpeg: bytes, max_width: int = 1600) -> bytes:
    if Image is None:
        raise RuntimeError("нужен Pillow: pip install Pillow")
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    if w > max_width:
        nh = max(1, round(h * max_width / w))
        img = img.resize((max_width, nh), Image.Resampling.LANCZOS)
    return _encode_png(img)


def _norm_line(line: str) -> str:
    return " ".join(line.strip().split())


def _block_ratio(left: list[str], right: list[str]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    scores = [SequenceMatcher(None, a, b, autojunk=False).ratio() for a, b in zip(left, right)]
    return sum(scores) / len(scores)


def split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def overlap_match(
    prev: list[str],
    nxt: list[str],
    *,
    window: int = PAGE_OVERLAP_WINDOW,
    min_k: int = 2,
) -> tuple[int, float, int]:
    """Вернуть (сколько строк отрезать у nxt, ratio, число совпавших непустых строк)."""
    if not prev or not nxt:
        return 0, 0.0, 0
    prev_idx = [i for i, ln in enumerate(prev) if _norm_line(ln)][-window:]
    nxt_idx = [i for i, ln in enumerate(nxt) if _norm_line(ln)][:window]
    if len(prev_idx) < min_k or len(nxt_idx) < min_k:
        return 0, 0.0, 0
    a = [_norm_line(prev[i]) for i in prev_idx]
    b = [_norm_line(nxt[i]) for i in nxt_idx]
    for k in range(min(len(a), len(b)), min_k - 1, -1):
        ratio = _block_ratio(a[-k:], b[:k])
        thresh = 0.92 if k <= 2 else 0.76 if k <= 5 else 0.68
        if ratio >= thresh:
            skip = nxt_idx[k - 1] + 1
            return skip, ratio, k
    return 0, 0.0, 0


def overlap_k(
    prev: list[str],
    nxt: list[str],
    *,
    window: int = PAGE_OVERLAP_WINDOW,
    min_k: int = 2,
) -> tuple[int, float]:
    skip, ratio, _k = overlap_match(prev, nxt, window=window, min_k=min_k)
    return skip, ratio


FENCE_LINE_RE = re.compile(r"^```[ \t]*[A-Za-z0-9_+.-]*[ \t]*$")
HUNK_HEADER_RE = re.compile(
    r"^@{1,2}[ \t]+-(\d+)(?:,(\d+))?[ \t]+\+(\d+)(?:,(\d+))?[ \t]*(?:@@)?[ \t]*(.*)$"
)
CS_GET_SET_RE = re.compile(r"\bget,\s*set\b")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}|[A-Za-z0-9_./\\-]{8,}")


def _id_tokens(lines: list[str]) -> set[str]:
    return {m.group(0) for m in IDENTIFIER_RE.finditer("\n".join(lines))}


def looks_like_diff(lines: list[str]) -> bool:
    blob = "\n".join(lines)
    return bool(re.search(r"(?m)^(diff --git |index [0-9a-f]|\+\+\+ b/|--- a/|@@ )", blob))


def looks_like_continuation(prev: list[str], nxt: list[str]) -> bool:
    a = _id_tokens(prev[-40:])
    b = _id_tokens(nxt[:40])
    if a and b:
        inter = a & b
        if len(inter) >= 5 or (len(a | b) and len(inter) / len(a | b) >= 0.12):
            return True
    return looks_like_diff(prev[-25:]) and looks_like_diff(nxt[:25])


def maybe_join_wrapped_line(prev: list[str], nxt: list[str]) -> tuple[list[str], list[str]]:
    if not prev or not nxt:
        return prev, nxt
    left, right = prev[-1], nxt[0]
    if not left.strip() or not right.strip():
        return prev, nxt
    if FENCE_LINE_RE.match(left.strip()) or FENCE_LINE_RE.match(right.strip()):
        return prev, nxt
    head = right.lstrip()
    if head.startswith(("diff --git", "index ", "@@", "--- ", "+++ ", "#", "//", "using ")):
        return prev, nxt
    if head[:1] in ".,)]}":
        return prev[:-1] + [left.rstrip() + head], nxt[1:]
    if len(left) >= 90 and not re.search(r"[;\"'.)\]]$", left.rstrip()):
        return prev[:-1] + [left.rstrip() + " " + head], nxt[1:]
    return prev, nxt


def stitch_line_lists(
    prev: list[str],
    nxt: list[str],
    *,
    window: int = PAGE_OVERLAP_WINDOW,
    break_mark: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    if not prev:
        return list(nxt), {"overlap": 0, "ratio": 0.0, "break": False}
    skip, ratio, k = overlap_match(prev, nxt, window=window)
    if k:
        return prev + nxt[skip:], {"overlap": k, "ratio": round(ratio, 4), "break": False}
    prev2, nxt2 = maybe_join_wrapped_line(prev, nxt)
    if looks_like_continuation(prev2, nxt2):
        extra: list[str] = []
        if prev2 and nxt2 and prev2[-1].strip() and nxt2[0].strip():
            if nxt2[0].startswith("diff --git") or nxt2[0].startswith("# "):
                extra = [""]
        return prev2 + extra + nxt2, {
            "overlap": 0,
            "ratio": round(ratio, 4),
            "break": False,
            "continuation": True,
        }
    extra = [""]
    if break_mark:
        extra.append(break_mark)
        extra.append("")
    return prev + extra + nxt, {"overlap": 0, "ratio": round(ratio, 4), "break": True}


def stitch_strips(texts: list[str]) -> str:
    acc: list[str] = []
    for text in texts:
        lines = split_lines(clean_ocr_text(text))
        if not acc:
            acc = lines
            continue
        skip, _ratio, k = overlap_match(acc, lines, window=12)
        if k:
            acc = acc + lines[skip:]
            continue
        if acc and acc[-1].strip() and lines and lines[0].strip():
            acc.append("")
        acc.extend(lines)
    return "\n".join(acc).strip("\n")


def stitch_pages(page_texts: list[str]) -> tuple[str, list[dict[str, Any]]]:
    acc: list[str] = []
    joins: list[dict[str, Any]] = []
    for idx, text in enumerate(page_texts, start=1):
        lines = split_lines(clean_ocr_text(text))
        if idx == 1:
            acc = lines
            continue
        mark = f"<!-- разрыв {idx - 1} → {idx} -->"
        acc, meta = stitch_line_lists(acc, lines, break_mark=mark)
        meta["from"] = idx - 1
        meta["to"] = idx
        joins.append(meta)
    return "\n".join(acc).strip() + "\n", joins


def collapse_repetitive_line(s: str, limit: int = 360) -> str:
    if len(s) <= limit:
        return s
    sample = s[:8000]
    grams = [sample[i : i + 8] for i in range(0, max(1, len(sample) - 8), 4)]
    if grams and (1.0 - len(set(grams)) / len(grams)) >= 0.65:
        return s[:limit]
    return s[:800]


def collapse_repeated_blocks(lines: list[str], min_repeats: int = 3) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        best_period = 0
        best_span = 0
        max_period = min(24, n - i)
        for period in range(1, max_period + 1):
            block = lines[i : i + period]
            if not any(x.strip() for x in block):
                continue
            repeats = 1
            j = i + period
            while j + period <= n:
                nxt = lines[j : j + period]
                if _block_ratio([_norm_line(x) for x in nxt], [_norm_line(x) for x in block]) < 0.9:
                    break
                repeats += 1
                j += period
            span = repeats * period
            if repeats >= min_repeats and span > best_span:
                best_period = period
                best_span = span
        if best_period:
            out.extend(lines[i : i + best_period])
            i += best_span
            continue
        out.append(lines[i])
        i += 1
    return out


def strip_fence_markers(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        if FENCE_LINE_RE.match(ln.strip()):
            continue
        had_fence = "```" in ln
        cleaned = re.sub(r"```[A-Za-z0-9_+.-]*\s*$", "", ln)
        cleaned = re.sub(r"^(\s*(?:[+\-] )?)\s*```[A-Za-z0-9_+.-]*", r"\1", cleaned)
        cleaned = cleaned.rstrip()
        if FENCE_LINE_RE.match(cleaned.strip()):
            continue
        if had_fence and cleaned.strip() in ("+", "-", "+ ", "- "):
            continue
        out.append(cleaned)
    return out


def absorb_rewritten_duplicates(lines: list[str]) -> list[str]:
    acc: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        nxt = _norm_line(lines[i])
        if nxt and len(nxt) >= 20:
            recent = [_norm_line(x) for x in acc[-40:]]
            if nxt in recent:
                i += 1
                continue
            if len(nxt) >= 24 and any(
                nxt in prev and nxt != prev for prev in recent[-12:] if len(prev) > len(nxt) + 8
            ):
                i += 1
                continue
        skip, _ratio, k = overlap_match(acc, lines[i:], window=32, min_k=3)
        if k and skip > 0:
            i += skip
            continue
        acc.append(lines[i])
        i += 1
    return acc


def fix_diff_syntax(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("@"):
            m = HUNK_HEADER_RE.match(stripped)
            if m:
                old_n = m.group(2) or "1"
                new_n = m.group(4) or "1"
                rest = (m.group(5) or "").strip()
                rebuilt = f"@@ -{m.group(1)},{old_n} +{m.group(3)},{new_n} @@"
                if rest:
                    rebuilt += " " + rest
                line = line[: len(line) - len(stripped)] + rebuilt
        line = CS_GET_SET_RE.sub("get; set", line)
        out.append(line)
    return out


def clean_ocr_text(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    s = re.sub(r"<\|[^>]+\|>", "", s)
    s = s.strip()
    fenced = re.fullmatch(r"```(?:markdown|md|text)?\s*\n(.*)\n```", s, flags=re.S | re.I)
    if fenced:
        s = fenced.group(1).strip()
    s = re.sub(r"^(Text Recognition|OCR|Table Recognition|Formula Recognition):\s*", "", s, flags=re.I)
    stripped = s.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            for key in ("markdown", "markdown_result", "text", "content"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    s = val.strip()
                    break
    lines = [_rstrip_keep_indent(collapse_repetitive_line(line)) for line in split_lines(s)]
    lines = strip_fence_markers(lines)
    lines = collapse_repeated_blocks(lines)
    lines = absorb_rewritten_duplicates(lines)
    lines = fix_diff_syntax(lines)
    deduped: list[str] = []
    for line in lines:
        key = _norm_line(line)
        if deduped and key and len(key) >= 12 and _norm_line(deduped[-1]) == key:
            continue
        deduped.append(line)
    lines = deduped
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    collapsed: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank <= 2:
                collapsed.append("")
            continue
        blank = 0
        collapsed.append(line)
    return "\n".join(collapsed)


def _rstrip_keep_indent(line: str) -> str:
    return line.rstrip()


def safe_title_filename(title: str) -> str:
    s = title.strip()
    s = re.sub(r'[<>:"/\\|?*]', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:120] or OUTPUT_NAME[:-3]


def title_from_markdown(text: str) -> str | None:
    lines = split_lines(text)
    if looks_like_diff(lines[:30]):
        return None
    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip().lstrip("#").strip()
            if title:
                return title
    for line in lines:
        s = line.strip()
        if s and not s.startswith("<!--") and not s.startswith("*⋯"):
            return s[:80]
    return None


def page_heading(text: str) -> str:
    return (title_from_markdown(text) or "").strip()


def page_preview(text: str, *, limit: int = 160) -> str:
    parts: list[str] = []
    for line in split_lines(text or ""):
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        parts.append(s)
        if len(" ".join(parts)) >= limit:
            break
    blob = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(blob) > limit:
        return blob[: max(1, limit - 1)].rstrip() + "…"
    return blob


def page_markdown_rel(filename: str, index: int) -> str:
    stem = Path(filename).stem if filename else ""
    return f"{stem}.md" if stem else f"{index:04d}.md"


def write_page_markdown(folder: Path, filename: str, index: int, text: str) -> str:
    """Пишет 0001.md рядом с 0001.jpg и копию в ocr/0001.md."""
    body = (text or "").rstrip() + "\n"
    rel = page_markdown_rel(filename, index)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / rel).write_text(body, encoding="utf-8")
    ocr_dir = folder / OCR_DIRNAME
    ocr_dir.mkdir(parents=True, exist_ok=True)
    (ocr_dir / f"{index:04d}.md").write_text(body, encoding="utf-8")
    return rel


def read_page_markdown(folder: Path, filename: str, index: int) -> str | None:
    sibling = folder / page_markdown_rel(filename, index)
    numbered = folder / OCR_DIRNAME / f"{index:04d}.md"
    for path in (sibling, numbered):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def ollama_ocr_image(png: bytes, *, timeout: float = 300, prompt: str | None = None) -> str:
    b64 = base64.b64encode(png).decode("ascii")
    prompt = prompt or GLM_PROMPT
    last_error = ""
    for with_think in (True, False):
        payload: dict[str, Any] = {
            "model": DEFAULT_MODEL,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "keep_alive": "30m",
            "options": dict(OCR_OPTIONS),
        }
        if with_think:
            payload["think"] = False
        try:
            data = _ollama_json("/api/generate", payload, timeout=timeout)
            text = str(data.get("response") or "")
            if text.strip():
                return clean_ocr_text(text)
            last_error = "пустой ответ модели"
            break
        except RuntimeError as exc:
            last_error = str(exc)
            if with_think and ("think" in last_error.lower() or "400" in last_error):
                continue
            break
    raise RuntimeError(last_error or f"Ollama не распознала кадр моделью {DEFAULT_MODEL}")


NoteFn = Callable[..., None]
ProgressFn = Callable[[dict[str, Any]], None]


def recognize_session(
    folder: Path,
    pages: list[dict[str, Any]],
    *,
    lines_per_strip: int = DEFAULT_STRIP_LINES,
    stop: threading.Event | None = None,
    note: NoteFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    if Image is None:
        raise RuntimeError("нужен Pillow: pip install Pillow")
    if not pages:
        raise RuntimeError("нет страниц для распознавания")
    n_lines = clamp_strip_lines(lines_per_strip)
    require_model()
    stop = stop or threading.Event()
    ocr_dir = folder / OCR_DIRNAME
    strips_dir = ocr_dir / "strips"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    strips_dir.mkdir(parents=True, exist_ok=True)

    def emit(key: str, **fields: Any) -> None:
        if note:
            note(key, **fields)

    def progress(**fields: Any) -> None:
        if on_progress:
            on_progress(fields)

    page_texts: list[str] = []
    page_meta: list[dict[str, Any]] = []
    total_pages = len(pages)

    for pi, page in enumerate(pages, start=1):
        if stop.is_set():
            raise RuntimeError("остановлено")
        filename = str(page.get("file") or "")
        path = folder / filename
        if not path.is_file():
            raise RuntimeError(f"нет файла {filename}")
        jpeg = path.read_bytes()
        strips = slice_page(jpeg, n_lines)
        progress(page=pi, pages=total_pages, strip=0, strips=len(strips), file=filename)
        emit("ocr_page", page=pi, pages=total_pages, strips=len(strips), file=filename)
        strip_texts: list[str] = []
        strip_files: list[str] = []
        for si, png in enumerate(strips, start=1):
            if stop.is_set():
                raise RuntimeError("остановлено")
            rel = f"strips/{pi:04d}-{si:02d}.png"
            (ocr_dir / rel).write_bytes(png)
            strip_files.append(f"{OCR_DIRNAME}/{rel}")
            progress(page=pi, pages=total_pages, strip=si, strips=len(strips), file=filename)
            text = ollama_ocr_image(png)
            strip_texts.append(text)
        page_text = stitch_strips(strip_texts)
        page_texts.append(page_text)
        (ocr_dir / f"{pi:04d}.md").write_text(page_text + "\n", encoding="utf-8")
        page_meta.append(
            {
                "index": pi,
                "file": filename,
                "strips": len(strips),
                "strip_files": strip_files,
            }
        )

    document, joins = stitch_pages(page_texts)
    for join in joins:
        if join.get("break"):
            emit("ocr_break", a=join["from"], b=join["to"])
        else:
            emit("ocr_overlap", a=join["from"], b=join["to"], n=join["overlap"], sim=f"{join['ratio']:.0%}")

    out_path = folder / OUTPUT_NAME
    out_path.write_text(document, encoding="utf-8")
    titled = title_from_markdown(document)
    extra = ""
    if titled:
        named = folder / f"{safe_title_filename(titled)}.md"
        if named.name != OUTPUT_NAME:
            named.write_text(document, encoding="utf-8")
            extra = named.name

    report = {
        "model": DEFAULT_MODEL,
        "lines_per_strip": n_lines,
        "page_count": total_pages,
        "pages": page_meta,
        "joins": joins,
        "output": OUTPUT_NAME,
        "titled": extra,
        "title": titled or "",
        "breaks": sum(1 for j in joins if j.get("break")),
    }
    (ocr_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    emit("ocr_done", file=OUTPUT_NAME, pages=total_pages, breaks=report["breaks"])
    progress(page=total_pages, pages=total_pages, strip=0, strips=0, file=OUTPUT_NAME, done=True)
    return report


def reprocess_ocr_dir(folder: Path) -> dict[str, Any]:
    ocr_dir = folder / OCR_DIRNAME
    files = sorted(p for p in ocr_dir.glob("*.md") if re.fullmatch(r"\d{4}\.md", p.name))
    if not files:
        raise RuntimeError("нет страниц OCR для пересшивки")
    page_texts: list[str] = []
    for path in files:
        cleaned = clean_ocr_text(path.read_text(encoding="utf-8"))
        path.write_text(cleaned + "\n", encoding="utf-8")
        page_texts.append(cleaned)
    document, joins = stitch_pages(page_texts)
    (folder / OUTPUT_NAME).write_text(document, encoding="utf-8")
    titled = title_from_markdown(document)
    extra = ""
    if titled:
        named = folder / f"{safe_title_filename(titled)}.md"
        if named.name != OUTPUT_NAME:
            named.write_text(document, encoding="utf-8")
            extra = named.name
    report = {
        "model": DEFAULT_MODEL,
        "reprocessed": True,
        "page_count": len(page_texts),
        "joins": joins,
        "output": OUTPUT_NAME,
        "titled": extra,
        "title": titled or "",
        "breaks": sum(1 for j in joins if j.get("break")),
    }
    (ocr_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


_PATH_EXT = (
    r"cs|json|xml|props|sql|yml|yaml|md|txt|csproj|html|js|ts|tsx|css|"
    r"config|cshtml|resx|Designer\.cs"
)
_DIFF_GIT_RE = re.compile(r"diff\s+--git\s+a/(\S+)(?:\s+b/(\S+))?", re.I)
_PLUSPLUS_RE = re.compile(r"^\+\+\+\s+[ab]/(\S+)", re.M)
_MINUSMINUS_RE = re.compile(r"^---\s+[ab]/(\S+)", re.M)
_PATH_RE = re.compile(
    rf"(?:^|[\s\"'`])((?:[A-Za-z0-9_.-]+/){{1,}}[A-Za-z0-9_.-]+\.(?:{_PATH_EXT}))\b"
)


def _looks_like_file(path: str) -> bool:
    if not path or path.endswith("/"):
        return False
    return bool(Path(path).suffix)


def normalize_repo_path(raw: str) -> str | None:
    p = (raw or "").strip().strip("`\"'“”«»").replace("\\", "/")
    p = p.rstrip(")]}>.,;\"'`")
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    if not p or p in ("/dev/null", "dev/null"):
        return None
    if ".." in p or p.startswith("/"):
        return None
    if not _looks_like_file(p):
        return None
    return p


def extract_paths(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        path = normalize_repo_path(raw)
        if not path or path in seen:
            return
        seen.add(path)
        found.append(path)

    for match in _DIFF_GIT_RE.finditer(text or ""):
        a = match.group(1)
        b = match.group(2)
        if b and normalize_repo_path(b) and b not in ("/dev/null", "dev/null"):
            add(b)
        else:
            add(a)
    for match in _PLUSPLUS_RE.finditer(text or ""):
        add(match.group(1))
    for match in _MINUSMINUS_RE.finditer(text or ""):
        add(match.group(1))
    for match in _PATH_RE.finditer(text or ""):
        add(match.group(1))
    return found


def display_path_name(path: str, all_paths: list[str]) -> str:
    base = Path(path).name
    clash = sum(1 for item in all_paths if Path(item).name == base) > 1
    if clash:
        parts = Path(path).parts
        return "/".join(parts[-2:]) if len(parts) >= 2 else base
    return base


def page_ranges(nums: list[int]) -> str:
    if not nums:
        return ""
    ordered = sorted(set(nums))
    chunks: list[str] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        chunks.append(f"{start}–{prev}" if start != prev else str(start))
        start = prev = n
    chunks.append(f"{start}–{prev}" if start != prev else str(start))
    return ", ".join(chunks)


def _md_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def entry_summary(entry: dict[str, Any], all_paths: list[str]) -> str:
    names: list[str] = []
    for path in entry.get("paths") or []:
        label = display_path_name(path, all_paths)
        names.append(f"{label} *(продолж.)*" if entry.get("continued") else label)
    if names:
        return ", ".join(names)
    heading = str(entry.get("heading") or "").strip()
    if heading:
        return f"{heading} *(продолж.)*" if entry.get("continued") else heading
    return str(entry.get("preview") or "").strip() or "—"


def build_index_entries(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """pages: [{index, file, md, paths, heading, preview}]. Continued — с предыдущей страницы."""
    last_paths: list[str] = []
    last_heading = ""
    out: list[dict[str, Any]] = []
    for page in pages:
        index = int(page.get("index") or 0)
        jpeg = str(page.get("file") or "")
        md = str(page.get("md") or page_markdown_rel(jpeg, index))
        paths = [p for p in (page.get("paths") or []) if p]
        heading = str(page.get("heading") or "").strip()
        preview = str(page.get("preview") or "").strip()
        continued = False
        shown_paths = list(paths)
        shown_heading = heading
        if not shown_paths and last_paths:
            shown_paths = list(last_paths)
            continued = True
        elif not shown_paths and not shown_heading and last_heading:
            shown_heading = last_heading
            continued = True
        if shown_paths:
            last_paths = [shown_paths[-1]]
        if heading:
            last_heading = heading
        entry = {
            "index": index,
            "file": jpeg,
            "md": md,
            "paths": shown_paths,
            "new_paths": paths,
            "heading": shown_heading,
            "preview": preview,
            "continued": continued,
            "source": str(page.get("source") or ""),
        }
        out.append(entry)
    return out


def render_index_markdown(entries: list[dict[str, Any]]) -> str:
    all_paths = [p for e in entries for p in e.get("paths") or []]
    lines = [
        "# Индекс сессии",
        "",
        "Каждая страница: JPEG (кадр) ↔ одноимённый markdown с распознанным текстом.",
        "OCR может ошибаться в символах; при сомнении сверяйте с JPEG.",
        "",
        "| Стр. | Кадр | Текст | Содержание |",
        "| ---: | --- | --- | --- |",
    ]
    for entry in entries:
        jpeg = str(entry.get("file") or "")
        md = str(entry.get("md") or "")
        jpeg_link = f"[{jpeg}]({jpeg})" if jpeg else ""
        md_link = f"[{md}]({md})" if md else ""
        summary = _md_cell(entry_summary(entry, all_paths))
        lines.append(f"| {entry.get('index')} | {jpeg_link} | {md_link} | {summary} |")
    by_file: dict[str, list[int]] = {}
    for entry in entries:
        for path in entry.get("paths") or []:
            by_file.setdefault(path, []).append(int(entry["index"]))
    if by_file:
        lines.extend(["", "## По файлам", ""])
        for path in sorted(by_file):
            pages = page_ranges(by_file[path])
            lines.append(f"- `{path}` — стр. {pages}")
    lines.append("")
    return "\n".join(lines)


def write_index(folder: Path, entries: list[dict[str, Any]], *, model: str = "") -> dict[str, Any]:
    text = render_index_markdown(entries)
    (folder / INDEX_NAME).write_text(text, encoding="utf-8")
    payload = {
        "model": model,
        "page_count": len(entries),
        "output": INDEX_NAME,
        "pages": entries,
    }
    (folder / INDEX_JSON).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def index_session(
    folder: Path,
    pages: list[dict[str, Any]],
    *,
    stop: threading.Event | None = None,
    note: NoteFn | None = None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    if not pages:
        raise RuntimeError("нет страниц для индекса")
    stop = stop or threading.Event()
    total_pages = len(pages)
    raw_pages: list[dict[str, Any]] = []

    def emit(key: str, **fields: Any) -> None:
        if note:
            note(key, **fields)

    def progress(**fields: Any) -> None:
        if on_progress:
            on_progress(fields)

    def has_page_md(index: int, item: dict[str, Any]) -> bool:
        return read_page_markdown(folder, str(item.get("file") or ""), index) is not None

    need_model = any(not has_page_md(i, item) for i, item in enumerate(pages, start=1))

    if need_model:
        if Image is None:
            raise RuntimeError("нужен Pillow: pip install Pillow")
        require_model()

    for pi, page in enumerate(pages, start=1):
        if stop.is_set():
            raise RuntimeError("остановлено")
        filename = str(page.get("file") or "")
        progress(page=pi, pages=total_pages, strip=0, strips=1, file=filename)
        emit("ocr_page", page=pi, pages=total_pages, strips=1, file=filename)
        existing = read_page_markdown(folder, filename, pi)
        if existing is not None:
            text = existing
            source = "ocr"
        else:
            path = folder / filename
            if not path.is_file():
                raise RuntimeError(f"нет файла {filename}")
            progress(page=pi, pages=total_pages, strip=1, strips=1, file=filename)
            png = encode_index_image(path.read_bytes())
            text = ollama_ocr_image(png)
            source = "image"
        md = write_page_markdown(folder, filename, pi, text)
        raw_pages.append(
            {
                "index": pi,
                "file": filename,
                "md": md,
                "paths": extract_paths(text),
                "heading": page_heading(text),
                "preview": page_preview(text),
                "source": source,
            }
        )
    entries = build_index_entries(raw_pages)
    report = write_index(folder, entries, model=DEFAULT_MODEL if need_model else "ocr-md")
    emit("ocr_done", file=INDEX_NAME, pages=total_pages, breaks=0)
    progress(page=total_pages, pages=total_pages, strip=0, strips=0, file=INDEX_NAME, done=True)
    return report
