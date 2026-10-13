#!/usr/bin/env python3
"""NanoKVM Scanner — снимает страницы с удаленного компьютера через NanoKVM Pro.

Подключается к MJPEG-потоку и HID WebSocket устройства, сохраняет JPEG
в локальную папку сессии и пишет промпт для распознавания кадров.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import hotkeys
import layout_type
import ocr

try:
    import websocket
except ImportError:
    sys.stderr.write("Нужен пакет websocket-client:  pip install -r requirements.txt\n")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[misc, assignment]

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_SCANS = ROOT / "scans"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8765

# Доля кадра: вкладки+адресная строка сверху, панель задач снизу (замер 1920x1080).
DEFAULT_CROP = {"top": 0.10, "right": 0.0, "bottom": 0.05, "left": 0.32}
MAX_SIDE_CROP = 0.95
MIN_KEEP = 0.04

# Сравнение кадров: бинарная карта чернил, не среднее серое 48×48.
# На белом фоне с кодом/диффом даунскейл в серое сглаживает буквы в одинаковую кашу.
SIG_WIDTH = 256
SIG_HEIGHT = 144
INK_WHITE_ABOVE = 200
DEFAULT_DUPLICATE_THRESHOLD = 0.988

# NanoKVM Pro: POST /api/stream/quality, 100 = пункт «Без потерь» для MJPEG.
# Текущий видеорежим API не отдаёт. После загрузки сервер и веб-клиент ставят h264-webrtc.
LOSSLESS_QUALITY = 100
DEFAULT_VIDEO_MODE = "h264-webrtc"
DEFAULT_VIDEO_QUALITY = 80

# Таблица яркости IJG для quality=50, построчный порядок. В файле она лежит zigzag.
_LUMA_Q50 = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)
_ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
)

SSL_CTX = ssl._create_unverified_context()

WS_HEARTBEAT = 0
WS_KEYBOARD = 1
WS_MOUSE = 2

HID_KEYS = {
    "PageDown": 0x4E,
    "PageUp": 0x4B,
    "ArrowDown": 0x51,
    "ArrowUp": 0x52,
    "Space": 0x2C,
    "Enter": 0x28,
    "Escape": 0x29,
    "Home": 0x4A,
    "End": 0x4D,
    "Tab": 0x2B,
}

OCR_PROMPT = """# Распознанные страницы

JPEG и одноимённый markdown лежат рядом: `0001.jpg` ↔ `0001.md`.
`_index.md` — оглавление: страница, кадр, текст, заголовок или путь файла.
OCR может ошибаться в символах; при сомнении смотри JPEG.

1. Открой `_index.md`, найди нужную страницу.
2. Читай `NNNN.md` — весь распознанный текст кадра (вики, статья, diff).
3. Если OCR врёт — сверяй с соответствующим JPEG.

Сессия: {session_name}
Страниц: {page_count}
Снято: {created}
Клавиша перелистывания: {page_key}
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def frame_sig(jpeg: bytes, width: int = SIG_WIDTH, height: int = SIG_HEIGHT) -> bytes | None:
    """Карта чернил 0/1. Буквы остаются точками, а не серым пятном."""
    if Image is None:
        return None
    img = Image.open(io.BytesIO(jpeg)).convert("L")
    img = img.resize((width, height), Image.Resampling.LANCZOS)
    return bytes(0 if pixel > INK_WHITE_ABOVE else 1 for pixel in img.getdata())


def similarity(a: bytes | None, b: bytes | None) -> float:
    """Доля совпавших пикселей карты чернил (1 = кадры одинаковые)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    mismatched = sum(x ^ y for x, y in zip(a, b))
    return 1.0 - mismatched / len(a)


def _fit_crop_axis(lo: float, hi: float) -> tuple[float, float]:
    lo = max(0.0, min(MAX_SIDE_CROP, lo))
    hi = max(0.0, min(MAX_SIDE_CROP, hi))
    keep = 1.0 - lo - hi
    if keep >= MIN_KEEP:
        return lo, hi
    extra = MIN_KEEP - keep
    span = lo + hi
    if span <= 0:
        return 0.0, 0.0
    lo = max(0.0, lo - extra * (lo / span))
    hi = max(0.0, hi - extra * (hi / span))
    return lo, hi


def parse_crop(crop: dict[str, Any] | None) -> dict[str, float]:
    src = crop or {}
    out = dict(DEFAULT_CROP)
    for key in out:
        if key in src and src[key] is not None and src[key] != "":
            out[key] = float(src[key])
    out["left"], out["right"] = _fit_crop_axis(out["left"], out["right"])
    out["top"], out["bottom"] = _fit_crop_axis(out["top"], out["bottom"])
    return out


def crop_jpeg(jpeg: bytes, crop: dict[str, float]) -> bytes:
    if Image is None:
        return jpeg
    crop = parse_crop(crop)
    top, right, bottom, left = (crop[k] for k in ("top", "right", "bottom", "left"))
    if top == right == bottom == left == 0:
        return jpeg
    img = Image.open(io.BytesIO(jpeg))
    w, h = img.size
    box = (
        int(w * left),
        int(h * top),
        int(w * (1 - right)),
        int(h * (1 - bottom)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return jpeg
    cropped = img.crop(box)
    out = io.BytesIO()
    cropped.convert("RGB").save(
        out, format="JPEG", quality=LOSSLESS_QUALITY, subsampling=0, optimize=True
    )
    return out.getvalue()


def _ijg_table(quality: int) -> tuple[int, ...]:
    scale = 5000 // quality if quality < 50 else 200 - quality * 2
    out: list[int] = []
    for base in _LUMA_Q50:
        value = (base * scale + 50) // 100
        if value < 1:
            value = 1
        elif value > 255:
            value = 255
        out.append(value)
    return tuple(out[i] for i in _ZIGZAG)


def _jpeg_luma_dqt(jpeg: bytes) -> tuple[int, ...] | None:
    i = 0
    size = len(jpeg)
    fallback: tuple[int, ...] | None = None
    while i + 1 < size:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        while i < size and jpeg[i] == 0xFF:
            i += 1
        if i >= size:
            break
        marker = jpeg[i]
        i += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > size:
            break
        length = int.from_bytes(jpeg[i : i + 2], "big")
        if length < 2 or i + length > size:
            break
        if marker == 0xDB:
            payload = jpeg[i + 2 : i + length]
            pos = 0
            while pos < len(payload):
                info = payload[pos]
                pos += 1
                bits16 = info >> 4
                table_id = info & 0x0F
                count = 128 if bits16 else 64
                if pos + count > len(payload):
                    break
                if not bits16:
                    table = tuple(payload[pos : pos + 64])
                    if table_id == 0:
                        return table
                    if fallback is None:
                        fallback = table
                pos += count
        if marker == 0xDA:
            break
        i += length
    return fallback


def estimate_jpeg_quality(jpeg: bytes) -> int | None:
    table = _jpeg_luma_dqt(jpeg)
    if table is None:
        return None
    best_q = 0
    best_err = 10**9
    for quality in range(1, 101):
        err = sum(abs(a - b) for a, b in zip(table, _ijg_table(quality)))
        if err < best_err:
            best_err = err
            best_q = quality
    if best_err > 64:
        return None
    return best_q


def hid_key_report(keycode: int, down: bool) -> bytes:
    report = bytearray(8)
    if down:
        report[2] = keycode
    return bytes(report)


def hid_abs_xy(nx: float, ny: float) -> tuple[int, int]:
    x = int(round(max(0.0, min(1.0, nx)) * 32767))
    y = int(round(max(0.0, min(1.0, ny)) * 32767))
    return x, y


def hid_mouse_abs(nx: float, ny: float, buttons: int = 0, wheel: int = 0) -> bytes:
    x, y = hid_abs_xy(nx, ny)
    w = max(-127, min(127, int(wheel))) & 0xFF
    return bytes([buttons & 0xFF, x & 0xFF, (x >> 8) & 0xFF, y & 0xFF, (y >> 8) & 0xFF, w])


def hid_click_at(nx: float, ny: float, buttons: int = 1) -> list[bytes]:
    return [hid_mouse_abs(nx, ny, buttons), hid_mouse_abs(nx, ny, 0)]


def hid_wheel(ticks: int) -> bytes:
    wheel = max(-127, min(127, int(ticks)))
    return bytes([0, 0, 0, wheel & 0xFF])


class KvmClient:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.base_url = ""
        self.token = ""
        self.connected = False
        self.last_error = ""
        self.last_url = ""
        self.last_username = "admin"
        self.last_password = ""
        self.latest_jpeg: bytes | None = None
        self.frame_id = 0
        self._stop = threading.Event()
        self._ws: websocket.WebSocket | None = None
        self._mjpeg_thread: threading.Thread | None = None
        self._mjpeg_resp: Any = None
        self._hb_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None
        self._video_lock = threading.Lock()
        self._saved_video: dict[str, Any] | None = None

    def headers(self) -> dict[str, str]:
        h = {"Accept": "*/*"}
        if self.token and self.token != "disabled":
            h["Authorization"] = f"Bearer {self.token}"
            h["Cookie"] = f"nano-kvm-token={self.token}"
        return h

    def connect(self, url: str, username: str, password: str) -> None:
        url = (url or "").strip()
        username = (username or "").strip() or "admin"
        if not url:
            raise RuntimeError("нет адреса NanoKVM: укажите url или подключитесь из интерфейса")
        self.disconnect()
        base = url.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        token = self._login(base, username, password)
        with self.lock:
            self.base_url = base
            self.token = token
            self.last_url = base
            self.last_username = username
            self.last_password = password
            self.last_error = ""
            self.connected = True
            self._stop.clear()
        self._open_ws()
        self._mjpeg_thread = threading.Thread(target=self._mjpeg_loop, daemon=True)
        self._mjpeg_thread.start()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        self._enable_lossless()

    def disconnect(self) -> None:
        self._stop.set()
        self._close_mjpeg()
        thread = self._mjpeg_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._restore_video()
        with self.lock:
            self.connected = False
            ws = self._ws
            self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self.latest_jpeg = None

    def video_override(self) -> dict[str, Any] | None:
        with self._video_lock:
            saved = self._saved_video
            if not saved or not saved.get("active"):
                return None
            return {"mode": saved["mode"], "quality": saved["quality"]}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("нет адреса NanoKVM")
        headers = self.headers()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{path} HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"{path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{path}: неожиданный ответ")
        if data.get("code", 0) not in (0, None):
            raise RuntimeError(data.get("msg") or f"{path}: ошибка")
        return data

    def keyboard_report(self, modifier: int = 0, keycode: int = 0) -> None:
        report = bytes([modifier & 0xFF, 0, keycode & 0xFF, 0, 0, 0, 0, 0])
        self._send_ws(bytes([WS_KEYBOARD]) + report)

    def alt_shift(self) -> None:
        # Как веб-клиент: сначала Alt, затем Shift, потом отпустить оба.
        self.keyboard_report(0x04)
        time.sleep(0.03)
        self.keyboard_report(0x04 | 0x02)
        time.sleep(0.05)
        self.keyboard_report(0)

    def paste_text(self, content: str) -> None:
        if not content:
            return
        headers = self.headers()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        req = urllib.request.Request(
            self.base_url + "/api/hid/paste",
            data=json.dumps({"content": content}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"/api/hid/paste HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"/api/hid/paste: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("/api/hid/paste: неожиданный ответ")
        if data.get("code", 0) not in (0, None):
            raise RuntimeError(data.get("msg") or "/api/hid/paste: ошибка")

    def _set_video(self, mode: str | None = None, quality: int | None = None) -> None:
        if quality is not None:
            self._post_json("/api/stream/quality", {"quality": int(quality)})
        if mode:
            self._post_json("/api/stream/mode", {"mode": mode})

    def _wait_frame(self, prev_id: int, timeout: float) -> tuple[bytes, int] | None:
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            with self.lock:
                jpeg = self.latest_jpeg
                fid = self.frame_id
            if jpeg and fid > prev_id:
                return jpeg, fid
            time.sleep(0.05)
        return None

    def _enable_lossless(self) -> None:
        with self._video_lock:
            if self._stop.is_set():
                return
            self._saved_video = {
                "mode": DEFAULT_VIDEO_MODE,
                "quality": DEFAULT_VIDEO_QUALITY,
                "restore_mode": True,
                "restore_quality": False,
                "active": False,
            }
        frame = self._wait_frame(0, 8)
        measured = estimate_jpeg_quality(frame[0]) if frame else None
        quality = measured if measured is not None else DEFAULT_VIDEO_QUALITY
        prev_id = frame[1] if frame else 0
        with self._video_lock:
            saved = self._saved_video
            if self._stop.is_set() or saved is None:
                return
            saved["quality"] = quality
            try:
                self._set_video(quality=LOSSLESS_QUALITY)
                saved["restore_quality"] = True
                self._set_video(mode="mjpeg")
                saved["active"] = True
            except Exception as exc:
                self.last_error = f"видео: {exc}"
                session.note("error", error=str(exc))
                return
        self._wait_frame(prev_id, 3)

    def _restore_video(self) -> None:
        with self._video_lock:
            saved = self._saved_video
            self._saved_video = None
        if not saved:
            return
        mode = str(saved["mode"]) if saved.get("restore_mode") else None
        quality = int(saved["quality"]) if saved.get("restore_quality") else None
        if mode is None and quality is None:
            return
        try:
            self._set_video(mode=mode, quality=quality)
        except Exception as exc:
            with self._video_lock:
                if self._saved_video is None:
                    self._saved_video = saved
            self.last_error = f"видео: {exc}"
            session.note("error", error=str(exc))
            return
        session.note(
            "video_restored",
            mode=mode or saved["mode"],
            quality=quality if quality is not None else "—",
        )

    def _close_mjpeg(self) -> None:
        with self.lock:
            resp = self._mjpeg_resp
        if resp is None:
            return
        try:
            resp.close()
        except Exception:
            pass

    def _login(self, base: str, username: str, password: str) -> str:
        payload = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(
            base + "/api/auth/login",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"логин HTTP {exc.code}") from exc
        except Exception as exc:
            raise RuntimeError(f"нет связи с NanoKVM: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError("неожиданный ответ логина")
        if data.get("code", 0) not in (0, None):
            raise RuntimeError(data.get("msg") or "ошибка логина")
        token = (data.get("data") or {}).get("token") or data.get("token")
        if not token:
            raise RuntimeError("в ответе нет token")
        return token

    def _ws_url(self) -> str:
        parsed = urllib.parse.urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc
        return f"{scheme}://{netloc}/api/ws"

    def _open_ws(self) -> None:
        headers = []
        if self.token and self.token != "disabled":
            headers.append(f"Cookie: nano-kvm-token={self.token}")
            headers.append(f"Authorization: Bearer {self.token}")
        ws = websocket.create_connection(
            self._ws_url(),
            header=headers,
            sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
            timeout=15,
        )
        ws.settimeout(1)
        with self.lock:
            self._ws = ws

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._send_ws(bytes([WS_HEARTBEAT]))
            except Exception as exc:
                self.last_error = f"ws heartbeat: {exc}"
                try:
                    self._open_ws()
                except Exception:
                    pass
            for _ in range(20):
                if self._stop.is_set():
                    return
                time.sleep(0.5)

    def _send_ws(self, payload: bytes) -> None:
        with self.lock:
            ws = self._ws
        if ws is None:
            raise RuntimeError("HID WebSocket не подключён")
        ws.send_binary(payload)

    def wheel(self, ticks: int) -> None:
        self._send_ws(bytes([WS_MOUSE]) + hid_wheel(ticks))

    def wheel_at(self, nx: float, ny: float, ticks: int) -> None:
        # Как веб-клиент NanoKVM в absolute mode: 6-байтный HID, колесо в последнем байте.
        payload = bytes([WS_MOUSE]) + hid_mouse_abs(nx, ny, 0, ticks)
        self._send_ws(payload)

    def tap_key(self, name: str, hold_ms: int = 40) -> None:
        if name == "WheelDown":
            self.wheel(-4)
            time.sleep(0.05)
            self.wheel(-4)
            return
        if name == "WheelUp":
            self.wheel(4)
            return
        code = HID_KEYS.get(name)
        if code is None:
            raise RuntimeError(f"неизвестная клавиша: {name}")
        self._send_ws(bytes([WS_KEYBOARD]) + hid_key_report(code, True))
        time.sleep(hold_ms / 1000)
        self._send_ws(bytes([WS_KEYBOARD]) + hid_key_report(code, False))

    def click_norm(self, nx: float, ny: float, buttons: int = 1) -> None:
        for report in hid_click_at(nx, ny, buttons):
            self._send_ws(bytes([WS_MOUSE]) + report)
            time.sleep(0.04)

    def move_norm(self, nx: float, ny: float) -> None:
        payload = bytes([WS_MOUSE]) + hid_mouse_abs(nx, ny, 0)
        self._send_ws(payload)
        time.sleep(0.03)
        self._send_ws(payload)

    def focus_center(self) -> None:
        nx, ny = content_click_point()
        self.click_norm(nx, ny)
        time.sleep(0.08)
        park_mouse()

    def _mjpeg_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_mjpeg()
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.last_error = f"mjpeg: {exc}"
                time.sleep(1)

    def _read_mjpeg(self) -> None:
        req = urllib.request.Request(self.base_url + "/api/stream/mjpeg", headers=self.headers())
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            with self.lock:
                self._mjpeg_resp = resp
            try:
                self._consume_mjpeg(resp)
            finally:
                with self.lock:
                    if self._mjpeg_resp is resp:
                        self._mjpeg_resp = None

    def _consume_mjpeg(self, resp: Any) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            chunk = resp.read(16384)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > 8_000_000:
                buf.clear()
                continue
            while True:
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start > 0:
                        del buf[:start]
                    break
                jpeg = bytes(buf[start : end + 2])
                del buf[: end + 2]
                with self.lock:
                    self.latest_jpeg = jpeg
                    self.frame_id += 1

    def snapshot(self) -> tuple[bytes, int]:
        with self.lock:
            jpeg = self.latest_jpeg
            fid = self.frame_id
        if not jpeg:
            raise RuntimeError("кадр ещё не получен, подождите превью")
        return jpeg, fid

    def wait_new_frame(self, prev_id: int, timeout: float) -> tuple[bytes, int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            jpeg, fid = self.snapshot()
            if fid > prev_id:
                return jpeg, fid
            time.sleep(0.05)
        return self.snapshot()


_LOG_RU = {
    "session": "сессия {name} -> {folder}",
    "session_opened": "сессия {name} открыта ({count} стр.) -> {folder}",
    "duplicate_frame": "этот кадр уже есть ({sim})",
    "captured": "снято {file} ({bytes} байт)",
    "auto_started": "автоскан запущен",
    "auto_focus": "фокус в области съёма",
    "page_limit": "достигнут лимит страниц",
    "duplicate_stop": "повтор кадра — автоскан остановлен",
    "end_of_document": "после листания кадр не изменился ({sim}) — конец документа",
    "auto_error": "автоскан ошибка: {error}",
    "auto_stopped": "автоскан остановлен",
    "connected": "подключено к {url}",
    "video_lossless": "MJPEG без потерь (вернуться к {mode}, качество {quality})",
    "video_restored": "видео восстановлено: {mode}, качество {quality}",
    "key": "клавиша {key}",
    "focused": "клик в статью, курсор убран из области съёма",
    "click": "клик {x}, {y}",
    "limit_raised": "лимит увеличен до {max_pages}, продолжаю",
    "error": "{error}",
    "shutdown": "выход",
    "ocr_started": "индекс {model}",
    "ocr_page": "индекс {page}/{pages} ({file})",
    "ocr_strip": "OCR полоса {strip}/{strips} страницы {page}",
    "ocr_overlap": "сшивка {a}→{b}: снято {n} строк перехлёста ({sim})",
    "ocr_break": "разрыв между страницами {a} и {b} — нет перехлёста",
    "ocr_done": "индекс готов: {file} ({pages} стр.)",
    "ocr_error": "индекс ошибка: {error}",
    "ocr_stopped": "индекс остановлен",
    "type_started": "набор {chars} симв., раскладка {layout}",
    "type_switch": "Alt+Shift → {layout}",
    "type_done": "напечатано {sent} симв.",
    "type_stopped": "набор остановлен ({sent}/{total})",
    "type_error": "набор ошибка: {error}",
    "type_bad_char": "нельзя напечатать «{char}»",
    "type_altshift": "Alt+Shift → {layout}",
    "type_enter": "Enter",
    "press": "нажато {keys}",
    "type_keys": "клавиши {chars} симв., раскладка {layout}",
    "type_keys_done": "клавиши напечатаны ({sent}), раскладка {layout}",
}


def _format_log_ru(entry: dict[str, Any]) -> str:
    tmpl = _LOG_RU.get(str(entry.get("key") or ""), "{key}")
    try:
        msg = tmpl.format_map({k: v for k, v in entry.items() if k not in ("t", "key")})
    except Exception:
        msg = str(entry.get("error") or entry.get("key") or entry)
    return f"{entry.get('t', '')}  {msg}".strip()


def pages_from_folder(folder: Path) -> tuple[list[dict[str, Any]], str]:
    pages: list[dict[str, Any]] = []
    created = ""
    manifest_path = folder / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            created = str(data.get("created") or "")
            raw_pages = data.get("pages") or []
            if isinstance(raw_pages, list):
                for item in raw_pages:
                    if not isinstance(item, dict) or not item.get("file"):
                        continue
                    path = folder / Path(str(item["file"])).name
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}:
                        page = dict(item)
                        page["file"] = path.name
                        pages.append(page)
    if pages:
        return pages, created
    files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}],
        key=lambda p: p.name,
    )
    for index, path in enumerate(files, start=1):
        payload = path.read_bytes()
        pages.append(
            {
                "index": index,
                "file": path.name,
                "bytes": len(payload),
                "md5": hashlib.md5(payload).hexdigest(),
                "saved_at": utc_now(),
            }
        )
    return pages, created


class ScanSession:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.name = ""
        self.dir: Path | None = None
        self.pages: list[dict[str, Any]] = []
        self.created = ""
        self.page_key = "PageDown"
        self.delay_ms = 900
        self.max_pages = 300
        self.stop_on_duplicate = True
        self.duplicate_threshold = DEFAULT_DUPLICATE_THRESHOLD
        self.crop = dict(DEFAULT_CROP)
        self.auto_running = False
        self.auto_stop = threading.Event()
        self.log: list[dict[str, Any]] = []
        self.last_hash: bytes | None = None

    def note(self, key: str, **fields: Any) -> None:
        entry: dict[str, Any] = {"t": datetime.now().strftime("%H:%M:%S"), "key": key}
        for name, value in fields.items():
            entry[name] = str(value) if isinstance(value, Path) else value
        line = _format_log_ru(entry)
        with self.lock:
            self.log.append(entry)
            self.log = self.log[-80:]
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii"), flush=True)

    def start(self, name: str, output_dir: str, settings: dict[str, Any]) -> Path:
        name = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in name).strip("-") or session_stamp()
        base = Path(output_dir).expanduser() if output_dir else DEFAULT_SCANS
        if not base.is_absolute():
            base = ROOT / base
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)
        existing, created = pages_from_folder(folder)
        last_hash = None
        if existing:
            last_path = folder / str(existing[-1]["file"])
            try:
                last_hash = frame_sig(last_path.read_bytes())
            except Exception:
                last_hash = None
        with self.lock:
            self.name = name
            self.dir = folder
            self.pages = existing
            self.created = created or utc_now()
            self.page_key = settings.get("page_key") or "PageDown"
            self.delay_ms = int(settings.get("delay_ms") or 900)
            self.max_pages = int(settings.get("max_pages") or 300)
            self.stop_on_duplicate = bool(settings.get("stop_on_duplicate", True))
            self.duplicate_threshold = float(settings.get("duplicate_threshold") or DEFAULT_DUPLICATE_THRESHOLD)
            self.crop = parse_crop(settings.get("crop"))
            self.last_hash = last_hash
        self._write_sidecar()
        if existing:
            self.note("session_opened", name=name, folder=folder, count=len(existing))
        else:
            self.note("session", name=name, folder=folder)
        return folder

    def save_frame(self, jpeg: bytes) -> dict[str, Any] | None:
        if self.dir is None:
            raise RuntimeError("сначала создайте сессию")
        jpeg = crop_jpeg(jpeg, self.crop)
        bits = frame_sig(jpeg)
        if self.last_hash is not None and bits is not None:
            sim = similarity(self.last_hash, bits)
            if self.stop_on_duplicate and sim >= self.duplicate_threshold:
                self.note("duplicate_frame", sim=f"{sim:.1%}")
                return None
        index = len(self.pages) + 1
        filename = f"{index:04d}.jpg"
        path = self.dir / filename
        path.write_bytes(jpeg)
        page = {
            "index": index,
            "file": filename,
            "bytes": len(jpeg),
            "md5": hashlib.md5(jpeg).hexdigest(),
            "saved_at": utc_now(),
        }
        with self.lock:
            self.pages.append(page)
            self.last_hash = bits
        self._write_sidecar()
        self.note("captured", file=filename, bytes=len(jpeg))
        return page

    def same_as_last(self, jpeg: bytes) -> tuple[bool, float]:
        cropped = crop_jpeg(jpeg, self.crop)
        bits = frame_sig(cropped)
        if self.last_hash is None or bits is None:
            return False, 0.0
        sim = similarity(self.last_hash, bits)
        return sim >= self.duplicate_threshold, sim

    def _write_sidecar(self) -> None:
        if self.dir is None:
            return
        manifest = {
            "session": self.name,
            "created": self.created,
            "updated": utc_now(),
            "page_key": self.page_key,
            "delay_ms": self.delay_ms,
            "crop": self.crop,
            "page_count": len(self.pages),
            "pages": self.pages,
        }
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        prompt = OCR_PROMPT.format(
            session_name=self.name,
            page_count=len(self.pages),
            created=self.created,
            page_key=self.page_key,
        )
        (self.dir / "OCR_PROMPT.md").write_text(prompt, encoding="utf-8")

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "name": self.name,
                "dir": str(self.dir) if self.dir else "",
                "page_count": len(self.pages),
                "pages": list(self.pages),
                "auto_running": self.auto_running,
                "log": list(self.log[-12:]),
                "page_key": self.page_key,
                "delay_ms": self.delay_ms,
                "max_pages": self.max_pages,
            }


kvm = KvmClient()
session = ScanSession()


class OcrJob:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.stop = threading.Event()
        self.model = ocr.DEFAULT_MODEL
        self.lines_per_strip = ocr.DEFAULT_STRIP_LINES
        self.page = 0
        self.pages = 0
        self.strip = 0
        self.strips = 0
        self.file = ""
        self.output = ""
        self.titled = ""
        self.error = ""

    def reset(self) -> None:
        with self.lock:
            self.running = True
            self.stop.clear()
            self.model = ocr.DEFAULT_MODEL
            self.lines_per_strip = ocr.DEFAULT_STRIP_LINES
            self.page = 0
            self.pages = 0
            self.strip = 0
            self.strips = 0
            self.file = ""
            self.output = ""
            self.titled = ""
            self.error = ""

    def set_progress(self, fields: dict[str, Any]) -> None:
        with self.lock:
            if "page" in fields:
                self.page = int(fields["page"] or 0)
            if "pages" in fields:
                self.pages = int(fields["pages"] or 0)
            if "strip" in fields:
                self.strip = int(fields["strip"] or 0)
            if "strips" in fields:
                self.strips = int(fields["strips"] or 0)
            if "file" in fields:
                self.file = str(fields["file"] or "")

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "model": self.model,
                "lines_per_strip": self.lines_per_strip,
                "page": self.page,
                "pages": self.pages,
                "strip": self.strip,
                "strips": self.strips,
                "file": self.file,
                "output": self.output,
                "titled": self.titled,
                "error": self.error,
            }


ocr_job = OcrJob()


class TypeJob:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.stop = threading.Event()
        self.layout = "en"
        self.sent = 0
        self.total = 0
        self.error = ""
        self.seq = 0
        self._segments: list[layout_type.Segment] = []
        self._text = ""

    def begin(self, text: str, layout: str, segments: list[layout_type.Segment]) -> None:
        with self.lock:
            self.running = True
            self.stop.clear()
            self.layout = layout
            self.sent = 0
            self.total = len(text)
            self.seq += 1
            self.error = ""
            self._segments = segments
            self._text = text

    def status(self) -> dict[str, Any]:
        with self.lock:
            line, lines, line_text = layout_type.progress_at(self._text, self.sent)
            return {
                "running": self.running,
                "layout": self.layout,
                "sent": self.sent,
                "total": self.total,
                "line": line,
                "lines": lines,
                "line_text": line_text,
                "error": self.error,
                "seq": self.seq,
            }


type_job = TypeJob()
hid_type_lock = threading.Lock()
last_frame_box: tuple[float, float, float, float] | None = None


def remember_frame_box(box: tuple[float, float, float, float] | None) -> None:
    global last_frame_box
    last_frame_box = box


# 12 мс удержание + 13 мс пауза ≈ 40 символов/с. Paste NanoKVM — 50 мс и без кавычек.
KEY_HOLD_S = 0.012
KEY_GAP_S = 0.013


def _layout_name(value: Any) -> str:
    layout = str(value or "en")
    if layout not in ("en", "ru"):
        raise RuntimeError("неизвестная раскладка")
    return layout


def type_loop() -> None:
    if not hid_type_lock.acquire(timeout=30):
        with type_job.lock:
            type_job.error = "клавиатура занята"
            type_job.running = False
        session.note("type_error", error="клавиатура занята")
        return
    try:
        _type_long()
    finally:
        hid_type_lock.release()


def _hid_char(ch: str) -> None:
    modifier, code = layout_type.us_hid(ch)
    kvm.keyboard_report(modifier, code)
    time.sleep(KEY_HOLD_S)
    kvm.keyboard_report(0)
    time.sleep(KEY_GAP_S)


def _type_segments(
    segments: list[layout_type.Segment],
    layout: str,
    *,
    stop: threading.Event | None = None,
    on_progress: Any | None = None,
) -> tuple[int, str, bool]:
    current = layout
    sent = 0
    for segment in segments:
        if (stop and stop.is_set()) or not kvm.connected:
            return sent, current, True
        if segment.layout != current:
            kvm.alt_shift()
            time.sleep(0.1)
            current = segment.layout
            session.note("type_switch", layout=current)
            if on_progress:
                on_progress(sent, current)
        for ch, index in zip(segment.keys, segment.src):
            if (stop and stop.is_set()) or not kvm.connected:
                return sent, current, True
            _hid_char(ch)
            sent = index + 1
            if on_progress and (sent % 8 == 0 or ch == "\n"):
                on_progress(sent, current)
    return sent, current, False


def _type_long() -> None:
    sent = 0
    stopped = False
    try:
        with type_job.lock:
            segments = list(type_job._segments)
            layout = type_job.layout
            text = type_job._text
        session.note("type_started", chars=len(text), layout=layout)

        def progress(done: int, current: str) -> None:
            with type_job.lock:
                type_job.sent = done
                type_job.layout = current

        sent, current, stopped = _type_segments(
            segments, layout, stop=type_job.stop, on_progress=progress
        )
        if not stopped:
            sent = len(text)
        with type_job.lock:
            type_job.sent = sent
            type_job.layout = current
        if stopped:
            session.note("type_stopped", sent=sent, total=len(text))
        else:
            session.note("type_done", sent=sent)
    except Exception as exc:
        with type_job.lock:
            type_job.error = str(exc)
            type_job.sent = sent
        session.note("type_error", error=str(exc))
    finally:
        try:
            kvm.keyboard_report(0)
        except Exception:
            pass
        with type_job.lock:
            type_job.running = False


def type_keys_now(text: str, layout: str) -> dict[str, Any]:
    """Короткий текст отдельными HID-отчётами."""
    if len(text) > layout_type.KEY_TYPE_MAX:
        raise RuntimeError("too_long")
    segments = layout_type.plan_paste(text, layout)
    if not any(segment.keys for segment in segments):
        return {"sent": len(text), "layout": layout}
    if not kvm.connected:
        raise RuntimeError("нет связи с NanoKVM")
    if type_job.running or not hid_type_lock.acquire(blocking=False):
        raise RuntimeError("busy")
    try:
        if type_job.running or not kvm.connected:
            raise RuntimeError("busy" if type_job.running else "нет связи с NanoKVM")
        session.note("type_keys", chars=len(text), layout=layout)
        sent, current, stopped = _type_segments(segments, layout)
        if not stopped:
            sent = len(text)
        with type_job.lock:
            type_job.layout = current
        session.note("type_keys_done", sent=sent, layout=current)
        return {"sent": sent, "layout": current}
    finally:
        try:
            kvm.keyboard_report(0)
        except Exception:
            pass
        hid_type_lock.release()


def press_chords(chords: list[hotkeys.Chord], repeat: int = 1) -> dict[str, Any]:
    """Сочетание: модификаторы вниз, клавиша, затем отпустить всё."""
    if repeat < 1 or repeat > hotkeys.REPEAT_MAX:
        raise ValueError(f"repeat от 1 до {hotkeys.REPEAT_MAX}")
    if not kvm.connected:
        raise RuntimeError("нет связи с NanoKVM")
    if type_job.running or not hid_type_lock.acquire(blocking=False):
        raise RuntimeError("busy")
    label = ", ".join(chord.label for chord in chords)
    if repeat > 1:
        label = f"{label} ×{repeat}"
    try:
        if type_job.running or not kvm.connected:
            raise RuntimeError("busy" if type_job.running else "нет связи с NanoKVM")
        for _ in range(repeat):
            for chord in chords:
                if chord.modifier:
                    kvm.keyboard_report(chord.modifier, 0)
                    time.sleep(0.03)
                if chord.keycode:
                    kvm.keyboard_report(chord.modifier, chord.keycode)
                    time.sleep(0.04)
                else:
                    time.sleep(0.05)
                kvm.keyboard_report(0)
                time.sleep(0.03)
        session.note("press", keys=label)
        return {"keys": label, "repeat": repeat}
    finally:
        try:
            kvm.keyboard_report(0)
        except Exception:
            pass
        hid_type_lock.release()


def jpeg_size(jpeg: bytes | None) -> tuple[int, int]:
    if not jpeg or Image is None:
        return 0, 0
    try:
        img = Image.open(io.BytesIO(jpeg))
        width, height = img.size
    except Exception:
        return 0, 0
    return int(width), int(height)


def _flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _query_flag(params: dict[str, list[str]], name: str) -> bool:
    vals = params.get(name) or []
    return bool(vals) and _flag(vals[0])


def box_to_px(
    box: tuple[float, float, float, float] | list[float] | None, width: int, height: int
) -> list[int] | None:
    if box is None or width < 1 or height < 1:
        return None
    x, y, w, h = box
    return [
        int(round(x * width)),
        int(round(y * height)),
        int(round(w * width)),
        int(round(h * height)),
    ]


def parse_frame_box(
    params: dict[str, list[str]], size: tuple[int, int] | None = None
) -> tuple[float, float, float, float] | None:
    keys = ("x", "y", "w", "h")
    present = [key for key in keys if params.get(key) and params[key][0] != ""]
    if not present:
        return None
    px = _query_flag(params, "px")
    if len(present) != 4:
        raise ValueError(
            "нужны x, y, w и h — пиксели кадра" if px else "нужны x, y, w и h — доли кадра от 0 до 1"
        )
    if px:
        fw, fh = size or (0, 0)
        if fw < 1 or fh < 1:
            raise ValueError("нет кадра")
        try:
            x, y, w, h = (int(round(float(params[key][0]))) for key in keys)
        except ValueError as exc:
            raise ValueError("x, y, w и h должны быть целыми пикселями") from exc
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > fw or y + h > fh:
            raise ValueError("область вне кадра")
        return x / fw, y / fh, w / fw, h / fh
    try:
        x, y, w, h = (float(params[key][0]) for key in keys)
    except ValueError as exc:
        raise ValueError("x, y, w и h должны быть числами") from exc
    if min(x, y, w, h) < 0 or x >= 1 or y >= 1 or w <= 0 or h <= 0:
        raise ValueError("область вне кадра")
    if x + w > 1.001 or y + h > 1.001:
        raise ValueError("область вне кадра")
    return x, y, min(w, 1 - x), min(h, 1 - y)


def box_from_body(
    body: dict[str, Any], size: tuple[int, int] | None = None
) -> tuple[float, float, float, float] | None:
    query = {
        "x": [str(body["x"])] if body.get("x") is not None else [],
        "y": [str(body["y"])] if body.get("y") is not None else [],
        "w": [str(body.get("w") if body.get("w") is not None else body.get("width"))]
        if body.get("w") is not None or body.get("width") is not None
        else [],
        "h": [str(body.get("h") if body.get("h") is not None else body.get("height"))]
        if body.get("h") is not None or body.get("height") is not None
        else [],
    }
    if _flag(body.get("px")):
        query["px"] = ["1"]
    return parse_frame_box(query, size)


def click_xy(body: dict[str, Any]) -> tuple[float, float]:
    x = float(body.get("x") if body.get("x") is not None else 0.5)
    y = float(body.get("y") if body.get("y") is not None else 0.5)
    if not _flag(body.get("px")):
        return x, y
    fw, fh = jpeg_size(kvm.latest_jpeg)
    if fw < 1 or fh < 1:
        raise ValueError("нет кадра")
    ix, iy = int(round(x)), int(round(y))
    if ix < 0 or iy < 0 or ix >= fw or iy >= fh:
        raise ValueError("точка вне кадра")
    return (ix + 0.5) / fw, (iy + 0.5) / fh


def frame_region(jpeg: bytes, box: tuple[float, float, float, float] | None) -> tuple[bytes, int, int]:
    if Image is None:
        if box is not None:
            raise RuntimeError("для части кадра нужен Pillow")
        return jpeg, 0, 0
    img = Image.open(io.BytesIO(jpeg))
    width, height = img.size
    if box is None:
        return jpeg, width, height
    x, y, rw, rh = box
    left = max(0, min(width - 1, int(round(width * x))))
    top = max(0, min(height - 1, int(round(height * y))))
    right = max(left + 1, min(width, int(round(width * (x + rw)))))
    bottom = max(top + 1, min(height, int(round(height * (y + rh)))))
    cropped = img.crop((left, top, right, bottom)).convert("RGB")
    out = io.BytesIO()
    cropped.save(out, format="JPEG", quality=90, subsampling=0)
    return out.getvalue(), cropped.size[0], cropped.size[1]


def apply_scan_settings(body: dict[str, Any]) -> None:
    if body.get("page_key"):
        session.page_key = str(body["page_key"])
    if "delay_ms" in body:
        session.delay_ms = int(body["delay_ms"] or 900)
    if "max_pages" in body:
        session.max_pages = int(body["max_pages"] or 300)
    if "stop_on_duplicate" in body:
        session.stop_on_duplicate = bool(body["stop_on_duplicate"])
    if "duplicate_threshold" in body:
        session.duplicate_threshold = float(body["duplicate_threshold"] or DEFAULT_DUPLICATE_THRESHOLD)
    if "crop" in body:
        session.crop = parse_crop(body.get("crop"))


def ensure_session(body: dict[str, Any] | None = None) -> None:
    if session.dir is not None:
        if body:
            apply_scan_settings(body)
        return
    data = body or {}
    session.start(
        data.get("name") or f"scan-{session_stamp()}",
        data.get("output_dir") or str(DEFAULT_SCANS),
        data,
    )


def capture_current() -> dict[str, Any]:
    ensure_session()
    park_mouse()
    jpeg, _ = kvm.snapshot()
    page = session.save_frame(jpeg)
    if page is None:
        return {"duplicate": True, "page": None}
    return {"duplicate": False, "page": page}


def _clamp01(n: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, n))


def point_in_crop(fx: float, fy: float) -> tuple[float, float]:
    crop = session.crop
    width = max(MIN_KEEP, 1.0 - crop["left"] - crop["right"])
    height = max(MIN_KEEP, 1.0 - crop["top"] - crop["bottom"])
    nx = crop["left"] + width * fx
    ny = crop["top"] + height * fy
    return _clamp01(nx), _clamp01(ny)


def content_click_point() -> tuple[float, float]:
    # Только кнопка «Фокус»: верх-лево области, обычно заголовок.
    return point_in_crop(0.16, 0.10)


def mouse_park_point() -> tuple[float, float]:
    crop = session.crop
    candidates = [
        (crop["left"], (crop["left"] * 0.4, 0.06)),
        (crop["top"], (0.06, crop["top"] * 0.45)),
        (crop["right"], (1.0 - crop["right"] * 0.4, 0.06)),
        (crop["bottom"], (0.06, 1.0 - crop["bottom"] * 0.4)),
    ]
    size, pt = max(candidates, key=lambda item: item[0])
    if size >= 0.04:
        return _clamp01(pt[0]), _clamp01(pt[1])
    return 0.012, 0.012


def park_mouse() -> None:
    nx, ny = mouse_park_point()
    kvm.move_norm(nx, ny)
    prev = kvm.frame_id
    time.sleep(0.12)
    try:
        kvm.wait_new_frame(prev, timeout=0.7)
    except Exception:
        pass


def turn_page() -> None:
    if str(session.page_key).startswith("Wheel"):
        nx, ny = content_click_point()
        kvm.move_norm(nx, ny)
        time.sleep(0.05)
        kvm.tap_key(session.page_key)
        park_mouse()
        return
    kvm.tap_key(session.page_key)


def wait_after_page() -> None:
    prev_id = kvm.frame_id
    time.sleep(session.delay_ms / 1000)
    try:
        kvm.wait_new_frame(prev_id, timeout=max(1.5, session.delay_ms / 500))
    except Exception:
        pass
    park_mouse()


def auto_loop() -> None:
    session.auto_running = True
    session.auto_stop.clear()
    session.note("auto_started")
    try:
        kvm.focus_center()
        session.note("auto_focus")
        time.sleep(0.15)
        while not session.auto_stop.is_set():
            if len(session.pages) >= session.max_pages:
                session.note("page_limit")
                break
            park_mouse()
            jpeg, _ = kvm.snapshot()
            page = session.save_frame(jpeg)
            if page is None:
                session.note("duplicate_stop")
                break
            if session.auto_stop.is_set():
                break
            turn_page()
            wait_after_page()
            jpeg, _ = kvm.snapshot()
            same, sim = session.same_as_last(jpeg)
            if same:
                session.note("end_of_document", sim=f"{sim:.1%}")
                break
    except Exception as exc:
        session.note("auto_error", error=str(exc))
    finally:
        session.auto_running = False
        session.note("auto_stopped")


def ocr_loop() -> None:
    try:
        if session.dir is None:
            raise RuntimeError("сначала создайте сессию")
        if session.auto_running:
            raise RuntimeError("дождитесь конца автоскана")
        pages = list(session.pages)
        session.note("ocr_started", model=ocr.DEFAULT_MODEL)
        report = ocr.index_session(
            session.dir,
            pages,
            stop=ocr_job.stop,
            note=session.note,
            on_progress=ocr_job.set_progress,
        )
        with ocr_job.lock:
            ocr_job.output = str(report.get("output") or ocr.INDEX_NAME)
            ocr_job.titled = ""
    except Exception as exc:
        with ocr_job.lock:
            ocr_job.error = str(exc)
        if str(exc) == "остановлено":
            session.note("ocr_stopped")
        else:
            session.note("ocr_error", error=str(exc))
    finally:
        with ocr_job.lock:
            ocr_job.running = False


# Поллинг UI и MJPEG — иначе консоль run.cmd забивается каждые ~700 мс.
_QUIET_ACCESS_LOG = ("/api/status", "/api/preview", "/api/screenshot")
_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def _client_gone(exc: BaseException) -> bool:
    if isinstance(exc, _CLIENT_GONE):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054):
        return True
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        request = str(args[0] if args else "")
        if any(path in request for path in _QUIET_ACCESS_LOG):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            if _client_gone(exc):
                return
            raise

    def _json(self, payload: Any, status: int = 200) -> None:
        code, body, ctype = json_bytes(payload, status)
        self._send(code, body, ctype)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())):
                return self._json({"error": "forbidden"}, 403)
            ctype = {
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
            }.get(target.suffix, "application/octet-stream")
            return self._file(target, ctype)
        if path == "/api/status":
            st = session.status()
            fw, fh = jpeg_size(kvm.latest_jpeg)
            st["kvm"] = {
                "connected": kvm.connected,
                "url": kvm.base_url,
                "frame_id": kvm.frame_id,
                "has_frame": kvm.latest_jpeg is not None,
                "frame_width": fw,
                "frame_height": fh,
                "error": kvm.last_error,
                "pillow": Image is not None,
                "video": kvm.video_override(),
            }
            st["default_scans"] = str(DEFAULT_SCANS)
            st["ocr"] = ocr_job.status()
            st["type"] = type_job.status()
            st["read"] = {"box": list(last_frame_box) if last_frame_box else None}
            if session.dir is not None and not st["ocr"]["output"]:
                if (session.dir / ocr.INDEX_NAME).is_file():
                    st["ocr"]["output"] = ocr.INDEX_NAME
                elif (session.dir / ocr.OUTPUT_NAME).is_file():
                    st["ocr"]["output"] = ocr.OUTPUT_NAME
            return self._json(st)
        if path == "/api/ocr/result":
            if session.dir is None:
                return self._json({"error": "нет сессии"}, 404)
            preferred = ocr_job.titled or ocr_job.output or ocr.INDEX_NAME
            target = session.dir / Path(preferred).name
            if not target.is_file():
                target = session.dir / ocr.INDEX_NAME
            if not target.is_file():
                target = session.dir / ocr.OUTPUT_NAME
            if not target.is_file():
                return self._json({"error": "нет индекса"}, 404)
            return self._file(target, "text/markdown; charset=utf-8")
        if path == "/api/preview.jpg":
            jpeg = kvm.latest_jpeg
            if not jpeg:
                return self._send(503, b"no frame", "text/plain")
            return self._send(200, jpeg, "image/jpeg")
        if path == "/api/screenshot.jpg":
            jpeg = kvm.latest_jpeg
            if not jpeg:
                return self._send(503, b"no frame", "text/plain")
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                size = jpeg_size(jpeg)
                box = parse_frame_box(query, size)
                data, width, height = frame_region(jpeg, box)
                remember_frame_box(box)
            except (ValueError, RuntimeError) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            extra = {}
            if width:
                extra["X-Image-Width"] = str(width)
            if height:
                extra["X-Image-Height"] = str(height)
            if size[0] and size[1]:
                extra["X-Frame-Width"] = str(size[0])
                extra["X-Frame-Height"] = str(size[1])
            return self._send(200, data, "image/jpeg", extra)
        if path == "/api/preview.mjpeg":
            return self._preview_mjpeg()
        if path.startswith("/api/page/"):
            name = Path(path.split("/")[-1]).name
            if session.dir is None:
                return self._json({"error": "нет сессии"}, 404)
            file = session.dir / name
            if not file.is_file():
                return self._json({"error": "нет файла"}, 404)
            return self._file(file, "image/jpeg")
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/connect":
                url = str(body.get("url") or "").strip()
                username = str(body.get("username") or "").strip()
                password = str(body.get("password") or "")
                if not url:
                    url = kvm.last_url
                    username = username or kvm.last_username
                    if "password" not in body:
                        password = kvm.last_password
                kvm.connect(url, username or "admin", password)
                session.note("connected", url=kvm.base_url)
                video = kvm.video_override()
                if video:
                    session.note("video_lossless", mode=video["mode"], quality=video["quality"])
                return self._json({"ok": True, "url": kvm.base_url})
            if path == "/api/disconnect":
                type_job.stop.set()
                kvm.disconnect()
                return self._json({"ok": True})
            if path == "/api/shutdown":
                session.auto_stop.set()
                ocr_job.stop.set()
                type_job.stop.set()
                session.note("shutdown")

                def _stop() -> None:
                    kvm.disconnect()
                    time.sleep(0.2)
                    print("stop", flush=True)
                    os._exit(0)

                threading.Thread(target=_stop, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/session/start":
                folder = session.start(
                    body.get("name") or f"scan-{session_stamp()}",
                    body.get("output_dir") or str(DEFAULT_SCANS),
                    body,
                )
                return self._json({"ok": True, "dir": str(folder), "name": session.name})
            if path == "/api/capture":
                ensure_session(body)
                return self._json({"ok": True, **capture_current()})
            if path == "/api/page":
                key = body.get("key") or session.page_key
                kvm.tap_key(key)
                session.note("key", key=key)
                return self._json({"ok": True})
            if path == "/api/capture-next":
                ensure_session(body)
                result = capture_current()
                turn_page()
                return self._json({"ok": True, "paged": True, **result})
            if path == "/api/focus":
                kvm.focus_center()
                session.note("focused")
                return self._json({"ok": True})
            if path == "/api/click":
                nx, ny = click_xy(body)
                js_btn = int(body.get("button") or 0)
                hid_btn = {0: 1, 1: 4, 2: 2}.get(js_btn, 1)
                kvm.click_norm(nx, ny, hid_btn)
                session.note("click", x=f"{nx:.3f}", y=f"{ny:.3f}")
                fw, fh = jpeg_size(kvm.latest_jpeg)
                payload: dict[str, Any] = {"ok": True}
                if _flag(body.get("px")):
                    payload["x"] = int(round(float(body["x"])))
                    payload["y"] = int(round(float(body["y"])))
                    payload["frame_width"] = fw
                    payload["frame_height"] = fh
                return self._json(payload)
            if path == "/api/wheel":
                nx = float(body.get("x") if body.get("x") is not None else 0.5)
                ny = float(body.get("y") if body.get("y") is not None else 0.5)
                ticks = int(body.get("ticks") or 0)
                if ticks:
                    kvm.wheel_at(nx, ny, ticks)
                return self._json({"ok": True})
            if path == "/api/auto/start":
                ensure_session(body)
                if len(session.pages) >= session.max_pages:
                    session.max_pages = len(session.pages) + 100
                    session.note("limit_raised", max_pages=session.max_pages)
                if session.auto_running:
                    return self._json({"ok": True, "already": True})
                threading.Thread(target=auto_loop, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/auto/stop":
                session.auto_stop.set()
                ocr_job.stop.set()
                return self._json({"ok": True})
            if path == "/api/ocr/start":
                if session.dir is None:
                    ensure_session(body)
                if session.dir is not None and not session.pages:
                    existing, created = pages_from_folder(session.dir)
                    if existing:
                        with session.lock:
                            session.pages = existing
                            if created:
                                session.created = created
                if session.auto_running:
                    return self._json({"ok": False, "error": "дождитесь конца автоскана"}, 409)
                if ocr_job.running:
                    return self._json({"ok": True, "already": True})
                if not session.pages:
                    session.note("ocr_error", error="нет страниц для распознавания")
                    return self._json({"ok": False, "error": "нет страниц для распознавания"}, 400)
                ocr_job.reset()
                threading.Thread(target=ocr_loop, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/ocr/stop":
                ocr_job.stop.set()
                return self._json({"ok": True})
            if path == "/api/read_terminal":
                jpeg = kvm.latest_jpeg
                if not jpeg:
                    return self._json({"ok": False, "error": "no frame"}, 503)
                fw, fh = jpeg_size(jpeg)
                given = any(body.get(key) is not None for key in ("x", "y", "w", "h", "width", "height"))
                box = box_from_body(body, (fw, fh)) if given else last_frame_box
                text, width, height = ocr.read_frame_text(jpeg, box)
                remember_frame_box(box)
                box_out: list[int] | list[float] | None = list(box) if box else None
                if _flag(body.get("px")):
                    box_out = box_to_px(box, fw, fh)
                return self._json(
                    {
                        "ok": True,
                        "text": text,
                        "box": box_out,
                        "width": width,
                        "height": height,
                        "frame_width": fw,
                        "frame_height": fh,
                    }
                )
            if path == "/api/type/keys":
                text = str(body.get("text") or "")
                layout = _layout_name(body.get("layout"))
                if len(text) > layout_type.KEY_TYPE_MAX:
                    return self._json(
                        {"ok": False, "error": "too_long", "max": layout_type.KEY_TYPE_MAX},
                        400,
                    )
                try:
                    result = type_keys_now(text, layout)
                except layout_type.BadChar as exc:
                    session.note("type_bad_char", char=exc.char)
                    return self._json({"ok": False, "error": "bad_char", "char": exc.char}, 400)
                except RuntimeError as exc:
                    if str(exc) == "busy":
                        return self._json({"ok": False, "error": "busy"}, 409)
                    raise
                return self._json({"ok": True, **result})
            if path == "/api/press":
                raw = body.get("keys")
                if raw is None:
                    raw = body.get("combo") or ""
                try:
                    repeat = int(body.get("repeat") or 1)
                    chords = hotkeys.parse_keys(raw if isinstance(raw, list) else str(raw))
                    result = press_chords(chords, repeat)
                except ValueError as exc:
                    return self._json({"ok": False, "error": "bad_keys", "msg": str(exc)}, 400)
                except RuntimeError as exc:
                    if str(exc) == "busy":
                        return self._json({"ok": False, "error": "busy"}, 409)
                    raise
                return self._json({"ok": True, **result})
            if path == "/api/type/start":
                text = str(body.get("text") or "")
                layout = _layout_name(body.get("layout"))
                try:
                    segments = layout_type.plan_paste(text, layout)
                except layout_type.BadChar as exc:
                    session.note("type_bad_char", char=exc.char)
                    return self._json({"ok": False, "error": "bad_char", "char": exc.char}, 400)
                if not kvm.connected:
                    raise RuntimeError("нет связи с NanoKVM")
                if type_job.running:
                    return self._json({"ok": True, "already": True})
                if not any(segment.keys for segment in segments):
                    return self._json({"ok": True, "empty": True, "layout": layout})
                type_job.begin(text, layout, segments)
                threading.Thread(target=type_loop, daemon=True).start()
                return self._json({"ok": True, "layout": layout, "seq": type_job.status()["seq"]})
            if path == "/api/type/stop":
                type_job.stop.set()
                return self._json({"ok": True})
            if path == "/api/type/altshift":
                if not kvm.connected:
                    raise RuntimeError("нет связи с NanoKVM")
                if type_job.running:
                    return self._json({"ok": False, "error": "busy"}, 409)
                layout = _layout_name(body.get("layout"))
                kvm.alt_shift()
                layout = "ru" if layout == "en" else "en"
                with type_job.lock:
                    type_job.layout = layout
                session.note("type_altshift", layout=layout)
                return self._json({"ok": True, "layout": layout})
            if path == "/api/type/enter":
                if not kvm.connected:
                    raise RuntimeError("нет связи с NanoKVM")
                if type_job.running:
                    return self._json({"ok": False, "error": "busy"}, 409)
                kvm.tap_key("Enter")
                session.note("type_enter")
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            if _client_gone(exc):
                return
            session.note("error", error=str(exc))
            try:
                self._json({"ok": False, "error": str(exc)}, 400)
            except Exception as send_exc:
                if not _client_gone(send_exc):
                    raise

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            return self._json({"error": f"нет файла {path.name}"}, 404)
        self._send(200, path.read_bytes(), content_type)

    def _preview_mjpeg(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        last = -1
        try:
            while True:
                jpeg, fid = kvm.latest_jpeg, kvm.frame_id
                if jpeg and fid != last:
                    last = fid
                    header = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                    self.wfile.write(header.encode("ascii") + jpeg + b"\r\n")
                    self.wfile.flush()
                else:
                    time.sleep(0.04)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def _chromium_exes() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    names = [
        pf / "Microsoft/Edge/Application/msedge.exe",
        pf86 / "Microsoft/Edge/Application/msedge.exe",
        pf / "Google/Chrome/Application/chrome.exe",
        local / "Google/Chrome/Application/chrome.exe",
        pf / "BraveSoftware/Brave-Browser/Application/brave.exe",
        local / "BraveSoftware/Brave-Browser/Application/brave.exe",
    ]
    found: list[Path] = []
    seen: set[str] = set()
    for path in names:
        key = str(path).lower()
        if path.is_file() and key not in seen:
            seen.add(key)
            found.append(path)
    for cmd in ("msedge", "chrome", "brave"):
        resolved = shutil.which(cmd)
        if resolved:
            path = Path(resolved)
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                found.append(path)
    return found


def open_ui(url: str) -> None:
    # --app даёт отдельное окно, которое страница может закрыть через window.close().
    for exe in _chromium_exes():
        try:
            subprocess.Popen(
                [str(exe), f"--app={url}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            continue
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="NanoKVM Scanner для NanoKVM Pro")
    parser.add_argument("--host", default=LISTEN_HOST)
    parser.add_argument("--port", type=int, default=LISTEN_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    DEFAULT_SCANS.mkdir(parents=True, exist_ok=True)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request: Any, client_address: Any) -> None:
            exc = sys.exc_info()[1]
            if exc is not None and _client_gone(exc):
                return
            super().handle_error(request, client_address)

    httpd = Server((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"NanoKVM Scanner: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: open_ui(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
    finally:
        kvm.disconnect()
        httpd.server_close()


if __name__ == "__main__":
    main()
