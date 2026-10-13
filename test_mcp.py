"""MCP-инструменты ходят в локальный сканер: короткий набор, снимок, ошибки."""

from __future__ import annotations

import io
import threading
import unittest
from http.server import ThreadingHTTPServer

from PIL import Image

import app
import layout_type
import mcp_server
import ocr


class ScannerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        mcp_server.SCANNER = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        app.kvm.latest_jpeg = None
        app.kvm.connected = False
        app.kvm.base_url = ""
        app.kvm.last_url = ""
        app.kvm.last_username = "admin"
        app.kvm.last_password = ""
        app.kvm.last_error = ""
        app.last_frame_box = None
        with app.type_job.lock:
            app.type_job.layout = "en"
            app.type_job.running = False
            app.type_job.sent = 0
            app.type_job.total = 0
            app.type_job._text = ""

    def test_type_status_reports_current_line(self) -> None:
        job = app.type_job
        job.begin("alpha\nbeta\ngamma", "en", [])
        try:
            with job.lock:
                job.sent = 6
            st = job.status()
            self.assertEqual(st["line"], 2)
            self.assertEqual(st["lines"], 3)
            self.assertEqual(st["line_text"], "beta")
            self.assertEqual(st["sent"], 6)
            self.assertEqual(st["total"], 16)
        finally:
            with job.lock:
                job.running = False
                job._text = ""
                job.sent = 0
                job.total = 0

    def test_short_text_rejects_kilobytes(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._type_keys("a" * (layout_type.KEY_TYPE_MAX + 1), "en")
        self.assertEqual(caught.exception.payload.get("error"), "too_long")

    def test_short_text_skips_copyright_without_kvm(self) -> None:
        result = mcp_server._type_keys("©", "en")
        self.assertEqual(result.get("sent"), 1)

    def test_short_text_needs_kvm(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._type_keys("hi", "en")
        self.assertIn("NanoKVM", str(caught.exception))

    def test_screenshot_without_frame(self) -> None:
        with self.assertRaises(mcp_server.ScannerError):
            mcp_server._screenshot()

    def test_screenshot_full_and_region(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        img.paste((200, 0, 0), (0, 0, 25, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()

        full, note = mcp_server._screenshot()
        self.assertTrue(full.startswith(b"\xff\xd8"))
        self.assertIn("весь экран", note)
        self.assertIn("100×40", note)
        self.assertIn("пиксели", note)

        part, part_note = mcp_server._screenshot(0, 0, 25, 40)
        self.assertTrue(part.startswith(b"\xff\xd8"))
        self.assertIn("25×40", part_note)
        self.assertIn("x=0 y=0", part_note)
        self.assertLess(len(part), len(full))
        shown = mcp_server.screenshot(0, 0, 25, 40)
        self.assertIsInstance(shown[1], mcp_server.Image)

    def test_region_outside_frame(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._screenshot(50, 20, 80, 32)
        self.assertIn("вне кадра", str(caught.exception))

    def test_tools_are_registered(self) -> None:
        import asyncio

        tools = asyncio.run(mcp_server.mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "type_text",
                "type_keys",
                "screenshot",
                "click",
                "press",
                "status",
                "connect",
                "read_terminal",
            },
        )

    def test_click_rejects_bad_point(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._click(150, 10, "left")
        self.assertIn("вне кадра", str(caught.exception))
        with self.assertRaises(mcp_server.ScannerError):
            mcp_server._click(10, 10, "other")

    def test_click_needs_frame(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._click(40, 20, "left")
        self.assertIn("кадр", str(caught.exception).lower())

    def test_click_needs_kvm(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._click(40, 24, "right")
        self.assertIn("HID", str(caught.exception))

    def test_click_maps_pixel_center(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        seen: dict = {}
        original = app.kvm.click_norm

        def fake(nx: float, ny: float, buttons: int = 1) -> None:
            seen["xy"] = (nx, ny)
            seen["buttons"] = buttons

        app.kvm.click_norm = fake  # type: ignore[method-assign]
        try:
            result = mcp_server._click(25, 10, "left")
        finally:
            app.kvm.click_norm = original  # type: ignore[method-assign]
        self.assertEqual(seen["xy"], (25.5 / 100, 10.5 / 40))
        self.assertEqual(seen["buttons"], 1)
        self.assertEqual(result["x"], 25)
        self.assertEqual(result["y"], 10)
        self.assertEqual(result["frame_width"], 100)
        self.assertEqual(result["frame_height"], 40)

    def test_press_unknown_key(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._press("Ctrl+Nope")
        self.assertIn("неизвестная", str(caught.exception))

    def test_press_needs_kvm(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._press("Ctrl+C")
        self.assertIn("NanoKVM", str(caught.exception))

    def test_status_reports_frame_pixels(self) -> None:
        img = Image.new("RGB", (1920, 1080), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        st = mcp_server._status()
        self.assertTrue(st["has_frame"])
        self.assertEqual(st["frame_width"], 1920)
        self.assertEqual(st["frame_height"], 1080)

    def test_web_click_still_uses_fractions(self) -> None:
        self.assertEqual(app.click_xy({"x": 0.4, "y": 0.6}), (0.4, 0.6))

    def test_status_reports_disconnected(self) -> None:
        st = mcp_server._status()
        self.assertFalse(st["connected"])
        self.assertFalse(st["has_frame"])
        self.assertEqual(st["frame_width"], 0)
        self.assertEqual(st["frame_height"], 0)
        self.assertEqual(st["layout"], "en")
        self.assertEqual(st["url"], "")

    def test_connect_without_saved_url(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._connect(wait_s=0)
        self.assertIn("адреса", str(caught.exception))

    def test_connect_reuses_last_credentials(self) -> None:
        captured: dict = {}

        def fake_connect(url: str, username: str, password: str) -> None:
            captured["args"] = (url, username, password)
            app.kvm.connected = True
            app.kvm.base_url = url

        app.kvm.last_url = "https://10.0.0.1"
        app.kvm.last_username = "admin"
        app.kvm.last_password = "secret"
        original = app.kvm.connect
        app.kvm.connect = fake_connect  # type: ignore[method-assign]
        try:
            st = mcp_server._connect(wait_s=0)
        finally:
            app.kvm.connect = original  # type: ignore[method-assign]
        self.assertEqual(captured["args"], ("https://10.0.0.1", "admin", "secret"))
        self.assertTrue(st["connected"])
        self.assertEqual(st["url"], "https://10.0.0.1")

    def test_quotes_go_out_as_hid_shift_quote(self) -> None:
        reports: list[tuple[int, int]] = []
        original_report = app.kvm.keyboard_report
        original_alt = app.kvm.alt_shift
        app.kvm.connected = True
        app.kvm.keyboard_report = lambda modifier=0, keycode=0: reports.append((modifier, keycode))
        app.kvm.alt_shift = lambda: None
        try:
            result = app.type_keys_now('grep "Failed"', "en")
        finally:
            app.kvm.keyboard_report = original_report
            app.kvm.alt_shift = original_alt
            app.kvm.connected = False
        self.assertEqual(result["sent"], 13)
        self.assertIn((2, 52), reports)

    def test_read_terminal_without_frame(self) -> None:
        with self.assertRaises(mcp_server.ScannerError) as caught:
            mcp_server._read_terminal()
        self.assertIn("no frame", str(caught.exception).lower())

    def test_read_terminal_uses_last_region(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        img.paste((200, 0, 0), (0, 0, 25, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        mcp_server._screenshot(0, 0, 25, 40)
        original = ocr.ollama_ocr_image

        def fake_ocr(png: bytes, **kwargs: object) -> str:
            self.assertTrue(png.startswith(b"\x89PNG"))
            return "FAIL: boom\n\"Failed\""

        ocr.ollama_ocr_image = fake_ocr  # type: ignore[assignment]
        try:
            result = mcp_server._read_terminal()
        finally:
            ocr.ollama_ocr_image = original  # type: ignore[assignment]
        self.assertEqual(result["text"], "FAIL: boom\n\"Failed\"")
        self.assertEqual(result["box"], [0, 0, 25, 40])
        self.assertEqual(result["frame_width"], 100)
        self.assertEqual(result["frame_height"], 40)


if __name__ == "__main__":
    unittest.main()
