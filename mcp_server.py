#!/usr/bin/env python3
"""MCP-сервер NanoKVM Scanner.

Нужен запущенный сканер (python app.py) и подключение к NanoKVM.
Текст печатается HID-нажатиями (~40 симв/с), кавычки не теряются.
Клик — абсолютная мышь в пикселях полного кадра HDMI.
Снимок — весь кадр HDMI или прямоугольник в пикселях того же кадра.
read_terminal — OCR последней области, без разбора JPEG.
status / connect — связь, кадр и текущая раскладка без скриншота.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

SCANNER = os.environ.get("WIKI_SCANNER_URL", "http://127.0.0.1:8765").rstrip("/")
MCP_HOST = "127.0.0.1"
MCP_PORT = 8766

mcp = FastMCP(
    "nanokvm-scanner",
    host=MCP_HOST,
    port=MCP_PORT,
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class ScannerError(RuntimeError):
    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def _message(payload: dict, code: int) -> str:
    err = payload.get("error")
    if err == "bad_char":
        return f"нельзя напечатать «{payload.get('char') or '?'}»"
    if err == "too_long":
        return (
            f"короткий набор — не больше {payload.get('max')} символов; "
            "длинный текст печатайте инструментом type_text"
        )
    if err == "busy":
        return "клавиатура занята другим набором"
    if err == "bad_keys":
        return str(payload.get("msg") or "неизвестная клавиша")
    return str(err or payload.get("msg") or f"HTTP {code}")


def _open(req: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw or f"HTTP {exc.code}"}
        if not isinstance(payload, dict):
            payload = {"error": str(payload)}
        raise ScannerError(_message(payload, exc.code), payload) from exc
    except urllib.error.URLError as exc:
        raise ScannerError(
            "сканер не запущен или недоступен: python app.py, затем Подключить. "
            f"({exc.reason})"
        ) from exc


def _json(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(SCANNER + path, data=data, headers=headers, method=method)
    with _open(req, timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ScannerError("неожиданный ответ сканера")
    if payload.get("ok") is False:
        raise ScannerError(_message(payload, 400), payload)
    return payload


def _type_text(text: str, layout: str = "en") -> dict:
    if layout not in ("en", "ru"):
        raise ScannerError("раскладка: en или ru")
    if not text:
        return {"ok": True, "empty": True, "layout": layout, "sent": 0, "total": 0}
    started = _json("POST", "/api/type/start", {"text": text, "layout": layout}, timeout=30)
    if started.get("empty"):
        return {"ok": True, "empty": True, "layout": layout, "sent": 0, "total": 0}
    if started.get("already"):
        raise ScannerError("набор уже идёт")
    seq = started.get("seq")
    deadline = time.time() + max(45.0, len(text) * 0.04 + 12)
    last: dict = {}
    while time.time() < deadline:
        status = _json("GET", "/api/status", timeout=15)
        job = status.get("type") or {}
        if not isinstance(job, dict):
            job = {}
        last = job
        if seq is not None and job.get("seq") != seq:
            time.sleep(0.3)
            continue
        if not job.get("running"):
            if job.get("error"):
                raise ScannerError(str(job["error"]))
            return {
                "ok": True,
                "sent": job.get("sent") or 0,
                "total": job.get("total") or len(text),
                "layout": job.get("layout") or layout,
            }
        time.sleep(0.4)
    sent = last.get("sent") or 0
    total = last.get("total") or len(text)
    raise ScannerError(f"набор не закончился вовремя, напечатано {sent}/{total}")


def _type_keys(text: str, layout: str = "en") -> dict:
    if layout not in ("en", "ru"):
        raise ScannerError("раскладка: en или ru")
    timeout = max(20.0, len(text) * 0.05 + 5)
    return _json("POST", "/api/type/keys", {"text": text, "layout": layout}, timeout=timeout)


def _px(value: int | float) -> int:
    return int(round(value))


def _box_px(box: list | tuple, width: int, height: int) -> list[int]:
    x, y, w, h = box
    return [
        int(round(float(x) * width)),
        int(round(float(y) * height)),
        int(round(float(w) * width)),
        int(round(float(h) * height)),
    ]


def _screenshot(
    x: int | float | None = None,
    y: int | float | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
) -> tuple[bytes, str]:
    given = [value is not None for value in (x, y, width, height)]
    query = ""
    if any(given):
        if not all(given):
            raise ScannerError("для части экрана нужны x, y, width и height в пикселях полного кадра")
        assert x is not None and y is not None and width is not None and height is not None
        query = "?" + urllib.parse.urlencode(
            {"x": _px(x), "y": _px(y), "w": _px(width), "h": _px(height), "px": 1}
        )
    req = urllib.request.Request(SCANNER + "/api/screenshot.jpg" + query, headers={"Accept": "image/jpeg"})
    with _open(req, 20) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        px_w = resp.headers.get("X-Image-Width") or "?"
        px_h = resp.headers.get("X-Image-Height") or "?"
        frame_w = resp.headers.get("X-Frame-Width") or px_w
        frame_h = resp.headers.get("X-Frame-Height") or px_h
    if "image/jpeg" not in content_type and not data.startswith(b"\xff\xd8"):
        raise ScannerError(data.decode("utf-8", "replace") or "нет кадра")
    origin = f"click и обрезка — пиксели полного кадра {frame_w}×{frame_h}, (0,0) слева сверху"
    if not query:
        return data, f"весь экран, {px_w}×{px_h}. {origin}"
    assert x is not None and y is not None
    return data, (
        f"область x={_px(x)} y={_px(y)} {px_w}×{px_h} из {frame_w}×{frame_h}. "
        f"{origin}, не координаты этой вырезки"
    )


_BUTTONS = {"left": 0, "right": 2, "middle": 1}


def _click(x: int | float, y: int | float, button: str = "left") -> dict:
    if button not in _BUTTONS:
        raise ScannerError("button: left, right или middle")
    ix, iy = _px(x), _px(y)
    if ix < 0 or iy < 0:
        raise ScannerError("x и y — пиксели полного кадра HDMI, (0,0) слева сверху")
    result = _json(
        "POST",
        "/api/click",
        {"x": ix, "y": iy, "button": _BUTTONS[button], "px": True},
        timeout=15,
    )
    result["button"] = button
    result["x"] = ix
    result["y"] = iy
    return result


def _press(keys: str, repeat: int = 1) -> dict:
    return _json("POST", "/api/press", {"keys": keys, "repeat": repeat}, timeout=max(15.0, repeat * 2))


def _status() -> dict:
    st = _json("GET", "/api/status", timeout=15)
    kvm = st.get("kvm") if isinstance(st.get("kvm"), dict) else {}
    job = st.get("type") if isinstance(st.get("type"), dict) else {}
    layout = job.get("layout") if job.get("layout") in ("en", "ru") else "en"
    fw = int(kvm.get("frame_width") or 0)
    fh = int(kvm.get("frame_height") or 0)
    raw_box = (st.get("read") or {}).get("box") if isinstance(st.get("read"), dict) else None
    read_box = None
    if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
        read_box = _box_px(raw_box, fw, fh) if fw and fh else list(raw_box)
    return {
        "ok": True,
        "connected": bool(kvm.get("connected")),
        "has_frame": bool(kvm.get("has_frame")),
        "frame_width": fw,
        "frame_height": fh,
        "layout": layout,
        "url": kvm.get("url") or "",
        "error": kvm.get("error") or "",
        "frame_id": kvm.get("frame_id") or 0,
        "type_running": bool(job.get("running")),
        "type_sent": job.get("sent") or 0,
        "type_total": job.get("total") or 0,
        "read_box": read_box,
    }


def _connect(
    url: str = "",
    username: str = "",
    password: str = "",
    wait_s: float = 8,
) -> dict:
    body: dict = {}
    if url:
        body["url"] = url
    if username:
        body["username"] = username
    if password:
        body["password"] = password
    result = _json("POST", "/api/connect", body, timeout=30)
    deadline = time.time() + max(0.0, float(wait_s))
    last = _status()
    while time.time() < deadline:
        last = _status()
        if last["has_frame"]:
            break
        time.sleep(0.25)
    last["url"] = result.get("url") or last.get("url") or ""
    return last


def _read_terminal(
    x: int | float | None = None,
    y: int | float | None = None,
    width: int | float | None = None,
    height: int | float | None = None,
) -> dict:
    given = [value is not None for value in (x, y, width, height)]
    body: dict = {"px": True}
    if any(given):
        if not all(given):
            raise ScannerError("для области нужны x, y, width и height в пикселях полного кадра")
        body.update({"x": _px(x), "y": _px(y), "w": _px(width), "h": _px(height)})
    return _json("POST", "/api/read_terminal", body, timeout=120)


@mcp.tool()
def type_text(text: str, layout: str = "en") -> str:
    """Напечатать длинный текст на удалённом ПК HID-клавишами.

    Килобайты допустимы. Скорость около 40 символов в секунду, кавычки " и ' не теряются.
    Не используйте paste NanoKVM и не кодируйте в base64 из-за кавычек.
    Вызов ждёт окончания набора.

    layout — раскладка, которая сейчас включена на удалённом ПК: en или ru.
    Её даёт инструмент status. Смешанный текст сам посылает Alt+Shift.
    Перед вызовом кликните в нужное поле.
    В ответе layout — раскладка после набора, её и передавайте в следующий вызов.
    """
    return json.dumps(_type_text(text, layout), ensure_ascii=False)


@mcp.tool()
def type_keys(text: str, layout: str = "en") -> str:
    """Напечатать короткий текст отдельными нажатиями клавиш (HID).

    До 200 символов: команда, пароль, несколько слов. Более длинный текст — type_text.
    Кавычки " и ' доходят как есть. layout — из status: en или ru.
    Перед вызовом кликните в нужное поле.
    """
    return json.dumps(_type_keys(text, layout), ensure_ascii=False)


@mcp.tool()
def screenshot(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
):
    """Снимок HDMI с удалённого ПК.

    Без аргументов — весь экран. Иначе прямоугольник в пикселях полного кадра HDMI:
    x, y, width, height, левый верхний угол (0,0). Не доли 0..1.
    Размер кадра — в подписи к снимку и в status (frame_width × frame_height).
    Пример левой четверти кадра 1920×1080: x=0, y=0, width=480, height=1080.
    Клик по вырезке всё равно в пикселях полного кадра, не этой картинки.
    """
    data, note = _screenshot(x, y, width, height)
    return [note, Image(data=data, format="jpeg")]


@mcp.tool()
def click(x: int, y: int, button: str = "left") -> str:
    """Кликнуть мышью на удалённом ПК.

    x и y — целые пиксели полного кадра HDMI, (0,0) слева сверху.
    Тот же кадр, что у screenshot без обрезки. Не доли 0..1 и не координаты вырезки.
    Размер кадра: status.frame_width × frame_height или подпись screenshot.
    button: left, right или middle.
    """
    return json.dumps(_click(x, y, button), ensure_ascii=False)


@mcp.tool()
def read_terminal(
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """Прочитать текст с экрана OCR (терминал, FAIL, вывод команды).

    Без аргументов — последняя область screenshot. Иначе те же пиксели полного кадра,
    что у screenshot: x, y, width, height, (0,0) слева сверху. Не доли 0..1.
    Возвращает text, не картинку. Для трёх строк FAIL не вызывайте screenshot.
    Нужны запущенные сканер и Ollama (glm-ocr).
    """
    return json.dumps(_read_terminal(x, y, width, height), ensure_ascii=False)


@mcp.tool()
def status() -> str:
    """Состояние связи с NanoKVM без снимка экрана.

    connected — HID/видео живы. has_frame — есть кадр HDMI.
    frame_width и frame_height — размер кадра для click/screenshot в пикселях.
    layout — текущая раскладка для type_text / type_keys (en или ru).
    error — последняя ошибка связи. Если connected=false, вызовите connect.
    """
    return json.dumps(_status(), ensure_ascii=False)


@mcp.tool()
def connect(
    url: str = "",
    username: str = "",
    password: str = "",
    wait_s: float = 8,
) -> str:
    """Подключить или переподключить NanoKVM.

    Без аргументов — повтор с последними url/логином/паролем из интерфейса сканера.
    Иначе url (http://host), username (по умолчанию admin), password.
    Ждёт кадр HDMI до wait_s секунд. Ответ как у status: connected, has_frame, layout.
    """
    return json.dumps(_connect(url, username, password, wait_s), ensure_ascii=False)


@mcp.tool()
def press(keys: str, repeat: int = 1) -> str:
    """Нажать клавишу или сочетание, как на виртуальной клавиатуре.

    Модификаторы: Ctrl, Alt, Shift, Win (правые: RCtrl, RAlt, RShift, RWin).
    В первую очередь: Ctrl+C, Ctrl+L, Ctrl+U, Alt+Tab, Backspace, Delete.
    Также: Ctrl+A, Ctrl+V, Ctrl+X, Ctrl+Z, Ctrl+Shift+Esc, Win+E, Win+D,
    Enter, Tab, Esc, Space, F1–F12, Up, Down, Left, Right, Home, End, PageUp, PageDown, Insert.
    Несколько подряд через запятую: Ctrl+A, Delete
    repeat — сколько раз повторить всё (удобно для Backspace).
    Это физические клавиши, не ввод текста: длинный текст — type_text, короткий — type_keys.
    """
    return json.dumps(_press(keys, repeat), ensure_ascii=False)


def _run() -> None:
    """HTTP MCP на http://127.0.0.1:8766/mcp.

    Каждый POST сам возвращает JSON. Долгая SSE-сессия здесь вешала вызовы
    инструментов: ответ уходил в поток, который Cursor не читал.
    """
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    _run()
