#!/usr/bin/env python3
import base64
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

URL = "http://127.0.0.1:8766/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
SHOT = Path(r"O:\My\Work2\wiki-scanner\scans\_mcp_check.jpg")


def call(name: str, arguments: dict | None = None, timeout: float = 60) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    result = body.get("result") or {}
    texts = [item.get("text") for item in (result.get("content") or []) if item.get("type") == "text"]
    for item in result.get("content") or []:
        if item.get("type") == "image":
            SHOT.write_bytes(base64.b64decode(item.get("data") or ""))
            texts.append(f"image {SHOT.stat().st_size} bytes")
    print(name, texts or result)
    if result.get("isError") or body.get("error"):
        raise SystemExit(body)
    return result


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "shot"
    if action == "shot":
        call("screenshot", {"x": 0, "y": 0.11, "width": 1, "height": 0.82})
        return
    if action == "shot-full":
        call("screenshot")
        return
    if action == "click":
        call("click", {"x": 0.5, "y": 0.55, "button": "left"})
        return
    if action == "press":
        call("press", {"keys": sys.argv[2], "repeat": int(sys.argv[3]) if len(sys.argv) > 3 else 1})
        return
    if action == "keys":
        call("type_keys", {"text": " ".join(sys.argv[2:]), "layout": "en"}, timeout=40)
        return
    if action == "text":
        call("type_text", {"text": Path(sys.argv[2]).read_text(encoding="utf-8"), "layout": "en"}, timeout=60)
        return
    raise SystemExit(f"unknown {action}")


if __name__ == "__main__":
    main()
