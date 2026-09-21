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
import ssl
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

OCR_PROMPT = """# Распознавание скриншотов
Не загружай все JPEG в контекст сразу — иначе сессия раздуется.

## Как читать кадры

Смотри картинки **сам** инструментом чтения файлов (зрение модели).
**Не используй OCR**
Текст, таблицы и код снимай с кадра глазами и сразу пиши markdown.

## Задача

1. Прочитай `manifest.json` (число страниц, порядок файлов).
2. Открывай **по 1–3 JPEG за раз**, распознавай содержимое, дописывай в результирующий файл, затем следующие кадры.
3. Имя файла — по заголовку документа с первой страницы, расширение `.md`.
   Убери из имени символы, недопустимые в имени файла Windows. Пока заголовок неизвестен — пиши во `_draft.md`, затем переименуй.
4. Сохраняй структуру оригинала: заголовки, списки, таблицы, ссылки, код.
5. Игнорируй хром браузера, курсор, панель NanoKVM, обои, часы, обрезанные поля.
6. Если страница дублирует предыдущую или обрезана — коротко пометь в документе.
7. В конце добавь краткое оглавление.

## После распознавания

Источник истины — получившийся `.md` (не JPEG).
В следующих сессиях **не прикладывай картинки**, достаточно markdown.

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


def frame_sig(jpeg: bytes, size: int = 48) -> bytes | None:
    if Image is None:
        return None
    img = Image.open(io.BytesIO(jpeg)).convert("L").resize((size, size), Image.BILINEAR)
    return bytes(img.getdata())


def similarity(a: bytes | None, b: bytes | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    diff = sum(abs(x - y) for x, y in zip(a, b))
    return 1.0 - diff / (len(a) * 255.0)


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
    cropped.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def hid_key_report(keycode: int, down: bool) -> bytes:
    report = bytearray(8)
    if down:
        report[2] = keycode
    return bytes(report)


def hid_abs_xy(nx: float, ny: float) -> tuple[int, int]:
    x = int(round(max(0.0, min(1.0, nx)) * 32767))
    y = int(round(max(0.0, min(1.0, ny)) * 32767))
    return x, y


def hid_mouse_abs(nx: float, ny: float, buttons: int = 0) -> bytes:
    x, y = hid_abs_xy(nx, ny)
    return bytes([buttons & 0xFF, x & 0xFF, (x >> 8) & 0xFF, y & 0xFF, (y >> 8) & 0xFF, 0])


def hid_click_at(nx: float, ny: float) -> list[bytes]:
    return [hid_mouse_abs(nx, ny, 1), hid_mouse_abs(nx, ny, 0)]


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
        self.latest_jpeg: bytes | None = None
        self.frame_id = 0
        self._stop = threading.Event()
        self._ws: websocket.WebSocket | None = None
        self._mjpeg_thread: threading.Thread | None = None
        self._hb_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None

    def headers(self) -> dict[str, str]:
        h = {"Accept": "*/*"}
        if self.token and self.token != "disabled":
            h["Authorization"] = f"Bearer {self.token}"
            h["Cookie"] = f"nano-kvm-token={self.token}"
        return h

    def connect(self, url: str, username: str, password: str) -> None:
        self.disconnect()
        base = url.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        token = self._login(base, username, password)
        with self.lock:
            self.base_url = base
            self.token = token
            self.last_error = ""
            self.connected = True
            self._stop.clear()
        self._open_ws()
        self._mjpeg_thread = threading.Thread(target=self._mjpeg_loop, daemon=True)
        self._mjpeg_thread.start()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
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

    def click_norm(self, nx: float, ny: float) -> None:
        for report in hid_click_at(nx, ny):
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
                self.last_error = f"mjpeg: {exc}"
                time.sleep(1)

    def _read_mjpeg(self) -> None:
        req = urllib.request.Request(self.base_url + "/api/stream/mjpeg", headers=self.headers())
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
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
        self.duplicate_threshold = 0.988
        self.crop = dict(DEFAULT_CROP)
        self.auto_running = False
        self.auto_stop = threading.Event()
        self.log: list[str] = []
        self.last_hash: bytes | None = None

    def note(self, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
        with self.lock:
            self.log.append(line)
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
        with self.lock:
            self.name = name
            self.dir = folder
            self.pages = []
            self.created = utc_now()
            self.page_key = settings.get("page_key") or "PageDown"
            self.delay_ms = int(settings.get("delay_ms") or 900)
            self.max_pages = int(settings.get("max_pages") or 300)
            self.stop_on_duplicate = bool(settings.get("stop_on_duplicate", True))
            self.duplicate_threshold = float(settings.get("duplicate_threshold") or 0.988)
            self.crop = parse_crop(settings.get("crop"))
            self.last_hash = None
        self._write_sidecar()
        self.note(f"сессия {name} -> {folder}")
        return folder

    def save_frame(self, jpeg: bytes) -> dict[str, Any] | None:
        if self.dir is None:
            raise RuntimeError("сначала создайте сессию")
        jpeg = crop_jpeg(jpeg, self.crop)
        bits = frame_sig(jpeg)
        if self.last_hash is not None and bits is not None:
            sim = similarity(self.last_hash, bits)
            if self.stop_on_duplicate and sim >= self.duplicate_threshold:
                self.note(f"этот кадр уже есть ({sim:.1%})")
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
        self.note(f"снято {filename} ({len(jpeg)} байт)")
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
        session.duplicate_threshold = float(body["duplicate_threshold"] or 0.988)
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
    session.note("автоскан запущен")
    try:
        kvm.focus_center()
        session.note("фокус в области съёма")
        time.sleep(0.15)
        while not session.auto_stop.is_set():
            if len(session.pages) >= session.max_pages:
                session.note("достигнут лимит страниц")
                break
            park_mouse()
            jpeg, _ = kvm.snapshot()
            page = session.save_frame(jpeg)
            if page is None:
                session.note("повтор кадра — автоскан остановлен")
                break
            if session.auto_stop.is_set():
                break
            turn_page()
            wait_after_page()
            jpeg, _ = kvm.snapshot()
            same, sim = session.same_as_last(jpeg)
            if same:
                session.note(f"после листания кадр не изменился ({sim:.1%}) — конец документа")
                break
    except Exception as exc:
        session.note(f"автоскан ошибка: {exc}")
    finally:
        session.auto_running = False
        session.note("автоскан остановлен")


# Поллинг UI и MJPEG — иначе консоль run.cmd забивается каждые ~700 мс.
_QUIET_ACCESS_LOG = ("/api/status", "/api/preview")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        request = str(args[0] if args else "")
        if any(path in request for path in _QUIET_ACCESS_LOG):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

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
            st["kvm"] = {
                "connected": kvm.connected,
                "url": kvm.base_url,
                "frame_id": kvm.frame_id,
                "has_frame": kvm.latest_jpeg is not None,
                "error": kvm.last_error,
                "pillow": Image is not None,
            }
            st["default_scans"] = str(DEFAULT_SCANS)
            return self._json(st)
        if path == "/api/preview.jpg":
            jpeg = kvm.latest_jpeg
            if not jpeg:
                return self._send(503, b"no frame", "text/plain")
            return self._send(200, jpeg, "image/jpeg")
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
                kvm.connect(body.get("url") or "", body.get("username") or "admin", body.get("password") or "")
                session.note(f"подключено к {kvm.base_url}")
                return self._json({"ok": True, "url": kvm.base_url})
            if path == "/api/disconnect":
                kvm.disconnect()
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
                session.note(f"клавиша {key}")
                return self._json({"ok": True})
            if path == "/api/capture-next":
                ensure_session(body)
                result = capture_current()
                turn_page()
                return self._json({"ok": True, "paged": True, **result})
            if path == "/api/focus":
                kvm.focus_center()
                session.note("клик в статью, курсор убран из области съёма")
                return self._json({"ok": True})
            if path == "/api/auto/start":
                ensure_session(body)
                if len(session.pages) >= session.max_pages:
                    session.max_pages = len(session.pages) + 100
                    session.note(f"лимит увеличен до {session.max_pages}, продолжаю")
                if session.auto_running:
                    return self._json({"ok": True, "already": True})
                threading.Thread(target=auto_loop, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/auto/stop":
                session.auto_stop.set()
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            session.note(str(exc))
            self._json({"ok": False, "error": str(exc)}, 400)

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

    httpd = Server((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"NanoKVM Scanner: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
    finally:
        kvm.disconnect()
        httpd.server_close()


if __name__ == "__main__":
    main()
