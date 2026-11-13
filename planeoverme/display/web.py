"""Browser LED simulator. Serves the latest frame and the app's debug state.

    /             the simulator page (web/index.html)
    /frame.png    the current 64x32 frame
    /state.json   every nearby plane, rule results, and the current selection
"""

from __future__ import annotations

import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from PIL import Image

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("planeoverme.web")


class WebDisplay:
    def __init__(self, port: int):
        self.port = port
        self._png = b""
        self._lock = threading.Lock()
        self.state_provider: Callable[[], dict] = dict

    def show(self, img: Image.Image) -> None:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        with self._lock:
            self._png = buf.getvalue()

    def frame_png(self) -> bytes:
        with self._lock:
            return self._png

    def start(self) -> None:
        display = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console quiet
                pass

            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                try:
                    if path == "/":
                        self._send((WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                    elif path == "/frame.png":
                        self._send(display.frame_png(), "image/png")
                    elif path == "/state.json":
                        body = json.dumps(display.state_provider(), default=str).encode()
                        self._send(body, "application/json")
                    else:
                        self.send_error(404)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    log.exception("web request failed: %s", path)
                    self.send_error(500)

        # Bound to this machine only, so the page is not visible on the network.
        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
