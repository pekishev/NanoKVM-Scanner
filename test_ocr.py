"""Тесты нарезки полос и сшивки страниц без Ollama."""

from __future__ import annotations

import io
import unittest

from PIL import Image, ImageDraw

import ocr


def _bars_jpeg(lines: int = 40, line_h: int = 12, gap: int = 8) -> bytes:
    w = 320
    h = 16 + lines * (line_h + gap)
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    y = 8
    for _ in range(lines):
        draw.rectangle((16, y, w - 16, y + line_h), fill="black")
        y += line_h + gap
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


class StripTests(unittest.TestCase):
    def test_detects_text_lines(self) -> None:
        jpeg = _bars_jpeg(40)
        img = Image.open(io.BytesIO(jpeg))
        boxes = ocr.detect_line_boxes(img)
        self.assertGreaterEqual(len(boxes), 36)
        self.assertLessEqual(len(boxes), 44)

    def test_groups_twelve_to_twenty(self) -> None:
        jpeg = _bars_jpeg(40)
        strips = ocr.slice_page(jpeg, lines_per_strip=16)
        self.assertGreaterEqual(len(strips), 2)
        self.assertLessEqual(len(strips), 4)
        for png in strips:
            self.assertTrue(png.startswith(b"\x89PNG"))

    def test_crop_jpeg_to_png_region(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        img.paste((200, 0, 0), (0, 0, 25, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        png, width, height = ocr.crop_jpeg_to_png(buf.getvalue(), (0, 0, 0.25, 1))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(height, 120)
        self.assertGreater(width, 20)
        cropped = Image.open(io.BytesIO(png))
        red, _green, _blue = cropped.getpixel((2, 2))
        self.assertGreater(red, 150)


class StitchTests(unittest.TestCase):
    def test_removes_page_overlap(self) -> None:
        page1 = "\n".join(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])
        page2 = "\n".join(["delta", "echo", "foxtrot", "golf", "hotel"])
        text, joins = ocr.stitch_pages([page1, page2])
        self.assertEqual(len(joins), 1)
        self.assertFalse(joins[0]["break"])
        self.assertEqual(joins[0]["overlap"], 3)
        self.assertIn("golf", text)
        self.assertEqual(text.count("delta"), 1)
        self.assertNotIn("разрыв", text)

    def test_marks_break_when_no_overlap(self) -> None:
        page1 = "\n".join(["one", "two", "three", "four"])
        page2 = "\n".join(["alpha", "bravo", "charlie", "delta"])
        text, joins = ocr.stitch_pages([page1, page2])
        self.assertTrue(joins[0]["break"])
        self.assertIn("<!-- разрыв 1 → 2 -->", text)

    def test_tolerates_ocr_noise(self) -> None:
        prev = ["keep indent", "second line", "third line", "fourth line"]
        nxt = ["second line", "third line.", "fourth line", "fifth line"]
        skip, ratio = ocr.overlap_k(prev, nxt)
        self.assertGreaterEqual(skip, 3)
        self.assertGreater(ratio, 0.7)

    def test_strip_overlap_join(self) -> None:
        a = "line one\nline two\nline three"
        b = "line two\nline three\nline four"
        joined = ocr.stitch_strips([a, b])
        self.assertEqual(joined.splitlines(), ["line one", "line two", "line three", "line four"])

    def test_clean_unwraps_fence(self) -> None:
        raw = "```markdown\nHello\n```"
        self.assertEqual(ocr.clean_ocr_text(raw), "Hello")

    def test_joins_gitlab_diff_without_false_break(self) -> None:
        page1 = "\n".join(
            [
                "diff --git a/Services/PSB.CareerPortal.ContentApi.Db/DTO/FeedbackDTO.cs b/Services/PSB.CareerPortal.ContentApi.Db/DTO/FeedbackDTO.cs",
                "index fa366be8..135af1a8 100644",
                "--- a/Services/PSB.CareerPortal.ContentApi.Db/DTO/FeedbackDTO.cs",
                "+++ b/Services/PSB.CareerPortal.ContentApi.Db/DTO/FeedbackDTO.cs",
                "@@ -64,4 +79,8 @@ public class FeedbackDTO",
                "+        public DateTime? DepersonalizedAt { get; set; }",
            ]
        )
        page2 = "\n".join(
            [
                "diff --git a/Services/PSB.CareerPortal.ContentApi.Db/DTO/FormAgencyDTO.cs b/Services/PSB.CareerPortal.ContentApi.Db/DTO/FormAgencyDTO.cs",
                "index bc2621bb..9a5def58 100644",
                "--- a/Services/PSB.CareerPortal.ContentApi.Db/DTO/FormAgencyDTO.cs",
                "+++ b/Services/PSB.CareerPortal.ContentApi.Db/DTO/FormAgencyDTO.cs",
                "+        public DateTime? DepersonalizedAt { get; set; }",
            ]
        )
        text, joins = ocr.stitch_pages([page1, page2])
        self.assertFalse(joins[0]["break"])
        self.assertNotIn("разрыв", text)


class CleanTests(unittest.TestCase):
    def test_strips_language_fences_and_rewrites(self) -> None:
        raw = "\n".join(
            [
                "    _integrationApiDbOptions.UseNpgsql(integrationApiConnString);",
                "```csharp",
                "    _integrationApiDbOptions.UseNpgsql(integrationApiConnString);",
                "    _securedDataApiDbOptions = new DbContextOptionsBuilder<SecuredDataDbContext>();",
                "    _securedDataApiDbOptions.UseNpgsql(securedDataApiConnString);",
                "```",
            ]
        )
        cleaned = ocr.clean_ocr_text(raw)
        self.assertNotIn("```", cleaned)
        self.assertEqual(cleaned.count("_integrationApiDbOptions.UseNpgsql"), 1)
        self.assertIn("_securedDataApiDbOptions.UseNpgsql", cleaned)

    def test_collapses_generation_loop(self) -> None:
        block = "```json\n{\n    formConsentService = formConsentService;\n}\n```"
        raw = "using PSB.CareerPortal.ContentApi.Services.Consent;\n" + (block + "\n") * 12
        cleaned = ocr.clean_ocr_text(raw)
        self.assertLess(len(cleaned.splitlines()), 12)
        self.assertEqual(cleaned.count("formConsentService = formConsentService;"), 1)
        self.assertNotIn("```", cleaned)

    def test_fixes_hunk_header_and_csharp_accessors(self) -> None:
        raw = "@ -68,22 +69,19 @@ namespace PSB.CareerPortal.ContentApi.Tests\npublic string Phone { get, set; }"
        cleaned = ocr.clean_ocr_text(raw)
        self.assertIn("@@ -68,22 +69,19 @@ namespace PSB.CareerPortal.ContentApi.Tests", cleaned)
        self.assertIn("{ get; set; }", cleaned)

    def test_truncates_repetitive_base64_run(self) -> None:
        raw = '+  ""photo"": ""/9j/' + ("2wBDAAEBAQEB" * 400) + '""'
        cleaned = ocr.clean_ocr_text(raw)
        self.assertLess(len(cleaned), 500)

    def test_drops_rewritten_hunk_prefix(self) -> None:
        raw = "\n".join(
            [
                '    "AutomaticDatabaseUpgrade": true,',
                '    "ConnectionStrings": {',
                '-     "CandidateIntegrationDb": ""',
                "```diff",
                '"AutomaticDatabaseUpgrade": true,',
                '"ConnectionStrings": {',
                '-    "CandidateIntegrationDb": ""',
                '+    "CandidateIntegrationDb": "Host=localhost;Port=5432;Database=candidateintegrationdb;Username=postgres;Password=postgres",',
                "+    \"MessagingDb\": \"Host=localhost;Port=5432;Database=candidateintegrationdb;Username=postgres;Password=postgres\"",
                "},",
                "```",
            ]
        )
        cleaned = ocr.clean_ocr_text(raw)
        self.assertEqual(cleaned.count("AutomaticDatabaseUpgrade"), 1)
        self.assertIn("MessagingDb", cleaned)
        self.assertIn("Host=localhost", cleaned)

    def test_drops_wrapped_line_tail(self) -> None:
        long_line = (
            '+        [Option(\'C\', "createDatabases", Required = false, HelpText = '
            '"Create all 8 databases if they don\'t exist. Requires connection string to postgres database.")]'
        )
        raw = long_line + "\npublic bool CreateDatabases { get; set; } = false;\n\ndon't exist. Requires connection string to postgres database.\")]"
        cleaned = ocr.clean_ocr_text(raw)
        self.assertEqual(cleaned.count("Requires connection string"), 1)

    def test_strips_glued_fence_ticks(self) -> None:
        cleaned = ocr.clean_ocr_text("        }\n}```\n+```\nnext line")
        self.assertNotIn("```", cleaned)
        self.assertIn("next line", cleaned)


class SessionLoadTests(unittest.TestCase):
    def test_loads_manifest_jpegs(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        import app

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "0001.jpg").write_bytes(_bars_jpeg(8))
            (folder / "0002.jpg").write_bytes(_bars_jpeg(8))
            (folder / "manifest.json").write_text(
                json.dumps(
                    {
                        "created": "2026-01-01T00:00:00Z",
                        "pages": [
                            {"index": 1, "file": "0001.jpg"},
                            {"index": 2, "file": "0002.jpg"},
                            {"index": 3, "file": "missing.jpg"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pages, created = app.pages_from_folder(folder)
            self.assertEqual(created, "2026-01-01T00:00:00Z")
            self.assertEqual([p["file"] for p in pages], ["0001.jpg", "0002.jpg"])


class IndexTests(unittest.TestCase):
    def test_extracts_diff_and_plusplus_paths(self) -> None:
        text = "\n".join(
            [
                "diff --git a/Services/Foo/Bar.cs b/Services/Foo/Bar.cs",
                "--- a/Services/Foo/Bar.cs",
                "+++ b/Services/Foo/Bar.cs",
                "@@ -1,2 +1,3 @@",
            ]
        )
        self.assertEqual(ocr.extract_paths(text), ["Services/Foo/Bar.cs"])

    def test_extracts_truncated_diff_git_line(self) -> None:
        text = "\n".join(
            [
                "diff --git a/Services/PSB.CareerPortal.ContentApi/Controllers/ConsentController.cs",
                "new file mode 100644",
                "+++ b/Services/PSB.CareerPortal.ContentApi/Controllers/ConsentController.cs",
            ]
        )
        self.assertEqual(
            ocr.extract_paths(text),
            ["Services/PSB.CareerPortal.ContentApi/Controllers/ConsentController.cs"],
        )

    def test_drops_wrapped_dir_and_trailing_junk(self) -> None:
        text = "\n".join(
            [
                "diff --git a/Services/Foo/Migrations/AddTable.cs b/Services/Foo/Migrations/",
                "--- a/Services/Foo/Context/DbContext.cs\"}",
                "+++ b/Services/Foo/Context/DbContext.cs",
            ]
        )
        self.assertEqual(
            ocr.extract_paths(text),
            ["Services/Foo/Migrations/AddTable.cs", "Services/Foo/Context/DbContext.cs"],
        )

    def test_page_ranges_and_continued_file(self) -> None:
        self.assertEqual(ocr.page_ranges([19, 20, 21, 24]), "19–21, 24")
        entries = ocr.build_index_entries(
            [
                {"index": 1, "file": "0001.jpg", "paths": ["A/One.cs"]},
                {"index": 2, "file": "0002.jpg", "paths": []},
                {"index": 3, "file": "0003.jpg", "paths": ["B/Two.cs"]},
            ]
        )
        self.assertEqual(entries[1]["paths"], ["A/One.cs"])
        self.assertTrue(entries[1]["continued"])
        self.assertFalse(entries[2]["continued"])
        self.assertEqual(entries[0]["md"], "0001.md")
        md = ocr.render_index_markdown(entries)
        self.assertIn("One.cs *(продолж.)*", md)
        self.assertIn("`A/One.cs` — стр. 1–2", md)
        self.assertIn("[0001.jpg](0001.jpg)", md)
        self.assertIn("[0001.md](0001.md)", md)

    def test_wiki_pages_keep_heading_and_md_link(self) -> None:
        entries = ocr.build_index_entries(
            [
                {
                    "index": 1,
                    "file": "0001.jpg",
                    "paths": [],
                    "heading": "Приём на работу",
                    "preview": "Приём на работу Текст статьи",
                },
                {
                    "index": 2,
                    "file": "0002.jpg",
                    "paths": [],
                    "heading": "",
                    "preview": "",
                },
                {
                    "index": 3,
                    "file": "0003.jpg",
                    "paths": [],
                    "heading": "Отпуск",
                    "preview": "Отпуск оформляется заранее",
                },
            ]
        )
        self.assertEqual(entries[1]["heading"], "Приём на работу")
        self.assertTrue(entries[1]["continued"])
        self.assertFalse(entries[2]["continued"])
        md = ocr.render_index_markdown(entries)
        self.assertIn("[0001.md](0001.md)", md)
        self.assertIn("Приём на работу *(продолж.)*", md)
        self.assertIn("Отпуск", md)
        self.assertNotIn("Файлы на кадрах не найдены", md)
        self.assertNotIn("## По файлам", md)

    def test_index_session_uses_existing_md(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            ocr_dir = folder / "ocr"
            ocr_dir.mkdir()
            (ocr_dir / "0001.md").write_text(
                "diff --git a/Foo/ConsentController.cs b/Foo/ConsentController.cs\n",
                encoding="utf-8",
            )
            (ocr_dir / "0002.md").write_text("public class X {}\n", encoding="utf-8")
            pages = [
                {"index": 1, "file": "0001.jpg"},
                {"index": 2, "file": "0002.jpg"},
            ]
            report = ocr.index_session(folder, pages)
            self.assertEqual(report["output"], ocr.INDEX_NAME)
            self.assertEqual(report["page_count"], 2)
            text = (folder / ocr.INDEX_NAME).read_text(encoding="utf-8")
            self.assertIn("ConsentController.cs", text)
            self.assertIn("продолж", text)
            self.assertIn("[0001.md](0001.md)", text)
            self.assertTrue((folder / "0001.md").is_file())
            self.assertIn("ConsentController.cs", (folder / "0001.md").read_text(encoding="utf-8"))
            payload = json.loads((folder / ocr.INDEX_JSON).read_text(encoding="utf-8"))
            self.assertEqual(payload["pages"][1]["paths"], ["Foo/ConsentController.cs"])
            self.assertEqual(payload["pages"][0]["md"], "0001.md")

    def test_index_session_saves_wiki_page_markdown(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        wiki = "# Приём на работу\n\nСотрудник оформляется в отделе кадров."
        original_ocr = ocr.ollama_ocr_image
        original_require = ocr.require_model

        def fake_ocr(png: bytes, prompt: str | None = None, **kwargs: object) -> str:
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertIsNone(prompt)
            return wiki

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "0001.jpg").write_bytes(_bars_jpeg(8))
            ocr.require_model = lambda: None  # type: ignore[assignment]
            ocr.ollama_ocr_image = fake_ocr  # type: ignore[assignment]
            try:
                report = ocr.index_session(folder, [{"index": 1, "file": "0001.jpg"}])
            finally:
                ocr.ollama_ocr_image = original_ocr  # type: ignore[assignment]
                ocr.require_model = original_require
            self.assertEqual(report["pages"][0]["heading"], "Приём на работу")
            self.assertEqual(report["pages"][0]["md"], "0001.md")
            self.assertEqual(report["pages"][0]["paths"], [])
            page_md = (folder / "0001.md").read_text(encoding="utf-8")
            self.assertIn("Сотрудник оформляется", page_md)
            self.assertEqual((folder / "ocr" / "0001.md").read_text(encoding="utf-8"), page_md)
            index = (folder / ocr.INDEX_NAME).read_text(encoding="utf-8")
            self.assertIn("[0001.jpg](0001.jpg)", index)
            self.assertIn("[0001.md](0001.md)", index)
            self.assertIn("Приём на работу", index)
            payload = json.loads((folder / ocr.INDEX_JSON).read_text(encoding="utf-8"))
            self.assertIn("отделе кадров", payload["pages"][0]["preview"])


if __name__ == "__main__":
    unittest.main()
