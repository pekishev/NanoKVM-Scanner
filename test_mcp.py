"""Сканер ходит в MCP NanoKVM-Pro за набором; снимок и клик — свой HTTP."""

from __future__ import annotations

import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

import app
import ocr


class ScannerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

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
            app.type_job.error = ""

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            payload["_http"] = exc.code
        if not isinstance(payload, dict):
            self.fail(f"не JSON: {payload!r}")
        return payload

    def _bytes(self, path: str) -> bytes:
        req = urllib.request.Request(self.base + path, headers={"Accept": "image/jpeg"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def test_type_status_reports_current_line(self) -> None:
        job = app.type_job
        job.begin("alpha\nbeta\ngamma", "en")
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

    def test_progress_stays_on_first_line(self) -> None:
        self.assertEqual(app.progress_at("hello\nworld", 3), (1, 2, "hello"))
        self.assertEqual(app.progress_at("hello\nworld", 6), (2, 2, "world"))
        self.assertEqual(app.progress_at("a\n\nb", 2), (2, 3, ""))
        self.assertEqual(app.progress_at("", 0), (1, 1, ""))
        self.assertEqual(app.progress_at("a\r\nb", 1), (1, 2, "a"))
        self.assertEqual(app.progress_at("a\r\nb", 3), (2, 2, "b"))

    def test_short_text_rejects_kilobytes(self) -> None:
        payload = self._json("POST", "/api/type/keys", {"text": "a" * (app.KEY_TYPE_MAX + 1), "layout": "en"})
        self.assertEqual(payload.get("error"), "too_long")
        self.assertEqual(payload.get("_http"), 400)

    def test_short_text_needs_kvm(self) -> None:
        payload = self._json("POST", "/api/type/keys", {"text": "hi", "layout": "en"})
        self.assertIn("NanoKVM", str(payload.get("error") or ""))

    def test_screenshot_without_frame(self) -> None:
        req = urllib.request.Request(self.base + "/api/screenshot.jpg")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(caught.exception.code, 503)

    def test_screenshot_full_and_region(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        img.paste((200, 0, 0), (0, 0, 25, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()

        full = self._bytes("/api/screenshot.jpg")
        self.assertTrue(full.startswith(b"\xff\xd8"))

        part = self._bytes("/api/screenshot.jpg?x=0&y=0&w=25&h=40&px=1")
        self.assertTrue(part.startswith(b"\xff\xd8"))
        self.assertLess(len(part), len(full))

    def test_region_outside_frame(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        req = urllib.request.Request(self.base + "/api/screenshot.jpg?x=50&y=20&w=80&h=32&px=1")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(caught.exception.code, 400)
        payload = json.loads(caught.exception.read().decode("utf-8"))
        self.assertIn("вне кадра", str(payload.get("error") or ""))

    def test_click_rejects_bad_point(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        payload = self._json("POST", "/api/click", {"x": 150, "y": 10, "px": True})
        self.assertIn("вне кадра", str(payload.get("error") or ""))

    def test_click_needs_frame(self) -> None:
        payload = self._json("POST", "/api/click", {"x": 40, "y": 20, "px": True})
        self.assertIn("кадр", str(payload.get("error") or "").lower())

    def test_click_needs_kvm(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        payload = self._json("POST", "/api/click", {"x": 40, "y": 24, "button": 2, "px": True})
        self.assertIn("HID", str(payload.get("error") or ""))

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
            result = self._json("POST", "/api/click", {"x": 25, "y": 10, "px": True, "button": 0})
        finally:
            app.kvm.click_norm = original  # type: ignore[method-assign]
        self.assertEqual(seen["xy"], (25.5 / 100, 10.5 / 40))
        self.assertEqual(seen["buttons"], 1)
        self.assertEqual(result["x"], 25)
        self.assertEqual(result["y"], 10)
        self.assertEqual(result["frame_width"], 100)
        self.assertEqual(result["frame_height"], 40)

    def test_press_unknown_key(self) -> None:
        payload = self._json("POST", "/api/press", {"keys": "Ctrl+Nope"})
        self.assertEqual(payload.get("error"), "bad_keys")
        self.assertIn("неизвестная", str(payload.get("msg") or ""))

    def test_press_needs_kvm(self) -> None:
        payload = self._json("POST", "/api/press", {"keys": "Ctrl+C"})
        self.assertIn("NanoKVM", str(payload.get("error") or ""))

    def test_status_reports_frame_pixels(self) -> None:
        img = Image.new("RGB", (1920, 1080), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        st = self._json("GET", "/api/status")
        kvm = st.get("kvm") or {}
        self.assertTrue(kvm["has_frame"])
        self.assertEqual(kvm["frame_width"], 1920)
        self.assertEqual(kvm["frame_height"], 1080)

    def test_web_click_still_uses_fractions(self) -> None:
        self.assertEqual(app.click_xy({"x": 0.4, "y": 0.6}), (0.4, 0.6))

    def test_status_reports_disconnected(self) -> None:
        st = self._json("GET", "/api/status")
        kvm = st.get("kvm") or {}
        self.assertFalse(kvm["connected"])
        self.assertFalse(kvm["has_frame"])
        self.assertEqual(kvm["frame_width"], 0)
        self.assertEqual(kvm["frame_height"], 0)
        self.assertEqual(kvm["url"], "")

    def test_connect_without_saved_url(self) -> None:
        payload = self._json("POST", "/api/connect", {})
        self.assertIn("адреса", str(payload.get("error") or ""))

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
            st = self._json("POST", "/api/connect", {})
        finally:
            app.kvm.connect = original  # type: ignore[method-assign]
        self.assertEqual(captured["args"], ("https://10.0.0.1", "admin", "secret"))
        self.assertTrue(st["ok"])
        self.assertEqual(st["url"], "https://10.0.0.1")

    def test_quotes_go_to_pro_mcp(self) -> None:
        captured: dict = {}

        def fake_mcp(name: str, arguments: dict | None = None, timeout: float = 30) -> dict:
            captured["name"] = name
            captured["arguments"] = arguments or {}
            captured["timeout"] = timeout
            return {"ok": True, "sent": 13, "layout": "en"}

        original = app.kvm.mcp_call
        app.kvm.connected = True
        app.kvm.mcp_call = fake_mcp  # type: ignore[method-assign]
        try:
            result = app.type_keys_now('grep "Failed"', "en")
        finally:
            app.kvm.mcp_call = original  # type: ignore[method-assign]
            app.kvm.connected = False
        self.assertEqual(captured["name"], "type_keys")
        self.assertEqual(captured["arguments"]["text"], 'grep "Failed"')
        self.assertEqual(captured["arguments"]["layout"], "en")
        self.assertEqual(result["sent"], 13)

    def test_type_start_uses_type_text(self) -> None:
        captured: dict = {}
        started = threading.Event()

        def fake_mcp(name: str, arguments: dict | None = None, timeout: float = 30) -> dict:
            captured.setdefault("calls", []).append((name, arguments or {}))
            if name == "type_text":
                started.set()
                return {"ok": True, "sent": 5, "layout": "ru"}
            if name == "status":
                return {"ok": True, "type_running": True, "type_sent": 2, "type_total": 5, "layout": "en"}
            return {"ok": True}

        original = app.kvm.mcp_call
        app.kvm.connected = True
        app.kvm.mcp_call = fake_mcp  # type: ignore[method-assign]
        try:
            payload = self._json("POST", "/api/type/start", {"text": "hello", "layout": "en"})
            self.assertTrue(payload.get("ok"))
            self.assertTrue(started.wait(2))
            running = True
            layout = "en"
            sent = 0
            for _ in range(50):
                with app.type_job.lock:
                    running = app.type_job.running
                    layout = app.type_job.layout
                    sent = app.type_job.sent
                if not running:
                    break
                time.sleep(0.05)
        finally:
            app.kvm.mcp_call = original  # type: ignore[method-assign]
            app.kvm.connected = False
            with app.type_job.lock:
                app.type_job.running = False
        self.assertFalse(running)
        self.assertEqual(layout, "ru")
        self.assertEqual(sent, 5)
        self.assertEqual(captured["calls"][0][0], "type_text")
        self.assertEqual(captured["calls"][0][1]["text"], "hello")

    def test_read_terminal_without_frame(self) -> None:
        payload = self._json("POST", "/api/read_terminal", {})
        self.assertIn("no frame", str(payload.get("error") or "").lower())

    def test_read_terminal_uses_last_region(self) -> None:
        img = Image.new("RGB", (100, 40), "white")
        img.paste((200, 0, 0), (0, 0, 25, 40))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        app.kvm.latest_jpeg = buf.getvalue()
        self._bytes("/api/screenshot.jpg?x=0&y=0&w=25&h=40&px=1")
        original = ocr.ollama_ocr_image

        def fake_ocr(png: bytes, **kwargs: object) -> str:
            self.assertTrue(png.startswith(b"\x89PNG"))
            return "FAIL: boom\n\"Failed\""

        ocr.ollama_ocr_image = fake_ocr  # type: ignore[assignment]
        try:
            result = self._json("POST", "/api/read_terminal", {"px": True})
        finally:
            ocr.ollama_ocr_image = original  # type: ignore[assignment]
        self.assertEqual(result["text"], "FAIL: boom\n\"Failed\"")
        self.assertEqual(result["box"], [0, 0, 25, 40])
        self.assertEqual(result["frame_width"], 100)
        self.assertEqual(result["frame_height"], 40)


class FakeProHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []
    replies: dict[str, dict | str] = {}

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        req = json.loads(raw.decode("utf-8"))
        params = req.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        self.calls.append((name, arguments))
        reply = self.replies.get(name, {"ok": True})
        if isinstance(reply, str):
            result = {"content": [{"type": "text", "text": reply}], "isError": True}
        else:
            result = {"content": [{"type": "text", "text": json.dumps(reply, ensure_ascii=False)}]}
        body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProMcpClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FakeProHandler.calls = []
        FakeProHandler.replies = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeProHandler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        FakeProHandler.calls = []
        FakeProHandler.replies = {
            "type_keys": {"ok": True, "sent": 2, "layout": "en"},
            "status": {"ok": True, "connected": True, "layout": "en"},
        }
        app.kvm.connected = True
        app.kvm.base_url = f"http://127.0.0.1:{self.port}"
        app.kvm.token = "test-token"

    def tearDown(self) -> None:
        app.kvm.connected = False
        app.kvm.base_url = ""
        app.kvm.token = ""

    def test_mcp_call_posts_to_device(self) -> None:
        result = app.kvm.mcp_call("type_keys", {"text": "hi", "layout": "en"})
        self.assertEqual(result["sent"], 2)
        self.assertEqual(FakeProHandler.calls, [("type_keys", {"text": "hi", "layout": "en"})])

    def test_mcp_call_raises_tool_error(self) -> None:
        FakeProHandler.replies["type_keys"] = "keyboard is busy"
        with self.assertRaises(RuntimeError) as caught:
            app.kvm.mcp_call("type_keys", {"text": "hi", "layout": "en"})
        self.assertIn("busy", str(caught.exception).lower())

    def test_mcp_call_needs_connection(self) -> None:
        app.kvm.connected = False
        with self.assertRaises(RuntimeError) as caught:
            app.kvm.mcp_call("status")
        self.assertIn("NanoKVM", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
