# NanoKVM Scanner

[English](README.md) | [Русский](README.ru.md)

Captures pages from a remote computer through **NanoKVM Pro** (MJPEG + HID keyboard and mouse) and saves JPEGs into a folder.

## NanoKVM API used

- `POST /api/auth/login` — JWT (`Authorization: Bearer` or cookie `nano-kvm-token`)
- `GET /api/stream/mjpeg` — desktop frames (`multipart/x-mixed-replace`, JPEG `FF D8`…`FF D9`)
- `GET /api/ws` — HID: type `1` + 8 keyboard bytes, type `2` + 4/6 mouse bytes (same as the NanoKVM web client)

The device must be in **MJPEG** mode. On the remote PC, open the document fullscreen (F11) and click into the article so Page Down scrolls it.

## MCP (agent keyboard / screenshot)

Cursor talks to **NanoKVM-Pro** on the device: `https://<kvm>/mcp` (see `.cursor/mcp.json`). Tools: `status`, `screenshot`, `click`, `type_keys`, `type_text`, `press`, `stop_type`. The scanner UI “Type” box uses the same MCP after Connect. Do not run a local MCP on port 8766.

## Run

Python 3.10+ is required.

```bat
cd NanoKVM-Scanner
python -m pip install -r requirements.txt
python app.py
```

or `run.cmd`. It opens `http://127.0.0.1:8765/` (localhost only) in its own Edge or Chrome window. Switch **EN / RU** in the header; the choice is remembered. **Exit** closes that window and stops the app.

1. NanoKVM address, username/password → **Connect**.
2. Session name → **Create session** (folder `scans/<name>/`).
3. On the remote PC, open the document. A click on the HDMI preview is a click on the remote PC (left or right button); the wheel scrolls there. **Focus** clicks into the article and moves the cursor out of the capture area.
4. **Capture + next** — save a frame and send Page Down. Or **Autoscan**.
5. Stops when the next frame nearly matches the previous one (end of the article), or click **Stop**.

Key: Page Down / Space / ↓ / mouse wheel. Pause after paging is usually 800–1200 ms.

Default crop: **top 10%** (tabs), **left 32%**, **bottom 5%** (taskbar). Right 0. Drag the frame edges to resize it; Shift+drag draws a new region. The numbers in the side panel stay in sync.

Session folder:

- `0001.jpg`, `0002.jpg`, …
- `0001.md`, `0002.md`, … — recognized text of each frame
- `_index.md` — table of contents: page → JPEG ↔ markdown
- `manifest.json` — order and sizes
- `OCR_PROMPT.md` — how to use the frames and recognized text
