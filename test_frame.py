"""Сравнение кадров: разные скрины патча не должны считаться одним кадром."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import app


def _jpeg(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _font() -> ImageFont.ImageFont:
    for name in ("arial.ttf", "C:\\Windows\\Fonts\\arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 16)
        except OSError:
            continue
    return ImageFont.load_default()


def _code_page(seed: str, shift_y: int = 0, size: tuple[int, int] = (960, 540)) -> bytes:
    """Белый кадр с «диффом»: одинаковая вёрстка, разный текст."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = _font()
    draw.rectangle((0, 0, size[0], 28), fill=(248, 248, 248))
    draw.rectangle((0, 0, 72, size[1]), fill=(252, 252, 252))
    y = 40 - shift_y
    lines = [
        f"diff --git a/Services/{seed}.cs b/Services/{seed}.cs",
        f"index {seed[:6]}aa..{seed[:6]}ff 100644",
        f"--- a/Services/{seed}.cs",
        f"+++ b/Services/{seed}.cs",
        f"@@ -12,8 +12,10 @@ namespace Demo.{seed}",
    ]
    for i in range(24):
        token = f"{seed}-{i:02d}-" + ("x" if i % 2 == 0 else "FieldName")
        prefix = "+" if i % 3 == 0 else ("-" if i % 3 == 1 else " ")
        lines.append(f"{prefix}    public string {token} {{ get; set; }}")
    for i, line in enumerate(lines):
        yy = y + i * 18
        if yy < 30 or yy > size[1] - 8:
            continue
        color = (22, 22, 22)
        if line.startswith("+"):
            color = (0, 110, 40)
        elif line.startswith("-"):
            color = (160, 20, 20)
        draw.text((80, yy), f"{100 + i:4d}  {line}", fill=color, font=font)
    return _jpeg(img)


class FrameSigTests(unittest.TestCase):
    def test_identical_frames_are_duplicates(self) -> None:
        jpeg = _code_page("Alpha")
        sim = app.similarity(app.frame_sig(jpeg), app.frame_sig(jpeg))
        self.assertGreaterEqual(sim, app.DEFAULT_DUPLICATE_THRESHOLD)

    def test_jpeg_noise_still_counts_as_same_frame(self) -> None:
        src = _code_page("Alpha")
        img = Image.open(io.BytesIO(src))
        noisy = _jpeg(img, quality=55)
        sim = app.similarity(app.frame_sig(src), app.frame_sig(noisy))
        self.assertGreaterEqual(sim, app.DEFAULT_DUPLICATE_THRESHOLD)

    def test_different_patch_screens_are_not_duplicates(self) -> None:
        a = _code_page("FeedbackDTO")
        b = _code_page("FormAgencyDTO")
        sim = app.similarity(app.frame_sig(a), app.frame_sig(b))
        self.assertLess(sim, app.DEFAULT_DUPLICATE_THRESHOLD)
        self.assertLess(sim, 0.97)

    def test_small_scroll_is_a_new_frame(self) -> None:
        a = _code_page("ScrollMe", shift_y=0)
        b = _code_page("ScrollMe", shift_y=16)
        sim = app.similarity(app.frame_sig(a), app.frame_sig(b))
        self.assertLess(sim, app.DEFAULT_DUPLICATE_THRESHOLD)

    def test_save_frame_keeps_distinct_patch_pages(self) -> None:
        session = app.ScanSession()
        with tempfile.TemporaryDirectory() as tmp:
            session.dir = Path(tmp)
            session.crop = {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
            session.stop_on_duplicate = True
            session.note = lambda *args, **kwargs: None
            first = session.save_frame(_code_page("PageOne"))
            second = session.save_frame(_code_page("PageTwo"))
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(second["index"], 2)


if __name__ == "__main__":
    unittest.main()
