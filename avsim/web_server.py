"""Tiny HTTP server for the web touch panel (standard library only).

    GET  /             the touch panel page
    GET  /api/state    room state + Crestron-style joins as JSON
    POST /api/action   {"action": "system_on" | "system_off" | "source" |
                        "vol_up" | "vol_down" | "volume" | "mute", "value": ...}

The HTTP server runs in its own thread and hands work to the controller's
asyncio event loop, so the room logic stays single-threaded.
"""

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PANEL = Path(__file__).parent / "web" / "panel.html"
log = logging.getLogger("web")


def start_web_server(room, loop: asyncio.AbstractEventLoop, host="127.0.0.1", port=8080):
    def on_loop(coro, timeout=5.0):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    async def get_snapshot():
        return room.snapshot()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PANEL.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(200, on_loop(get_snapshot()))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/action":
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                result = on_loop(room.handle_action(body.get("action", ""), body.get("value")))
                self._json(200, {"result": result})
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, fmt, *args):  # keep the console quiet
            log.debug(fmt, *args)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    log.info("touch panel at http://%s:%s", host, server.server_address[1])
    return server
