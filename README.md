# NanoKVM Scanner

[English](README.md) | [Русский](README.ru.md)

Captures pages from a remote computer through **NanoKVM Pro** (MJPEG + HID keyboard) and saves JPEGs into a folder.

## NanoKVM API used

- `POST /api/auth/login` — JWT (`Authorization: Bearer` or cookie `nano-kvm-token`)
- `GET /api/stream/mjpeg` — desktop frames (`multipart/x-mixed-replace`, JPEG `FF D8`…`FF D9`)
- `GET /api/ws` — HID: type `1` + 8 keyboard bytes, type `2` + 4/6 mouse bytes (same as the NanoKVM web client)

The device must be in **MJPEG** mode. On the remote PC, open the document fullscreen (F11) and click into the article so Page Down scrolls it.

## Run

Python 3.10+ is required.

```bat
cd NanoKVM-Scanner
python -m pip install -r requirements.txt
python app.py
```

or `run.cmd`. The browser opens `http://127.0.0.1:8765/` (localhost only). The UI is in Russian.

1. NanoKVM address, login/password → **Подключить**.
2. Session name → **Создать сессию** (folder `scans/<name>/`).
3. On the remote PC, open the document. **Фокус** — click the center of the HDMI view so the page receives the keyboard.
4. **Снять и далее** — save a frame and send Page Down. Or **Автоскан**.
5. Stops when the next frame nearly matches the previous one (end of the article), or click **Стоп**.

Key: Page Down / Space / ↓ / mouse wheel. Pause after paging is usually 800–1200 ms.

Default crop: **top 10%** (tabs), **left 32%**, **bottom 5%** (taskbar). Right 0. You can draw and move the capture frame on the HDMI preview; the numbers in the side panel stay in sync.

Session folder:

- `0001.jpg`, `0002.jpg`, …
- `manifest.json` — order and sizes
- `OCR_PROMPT.md` — prompt for recognizing text from the frames
