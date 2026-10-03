"""Small localhost-only HTTP dashboard for usage statistics."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from .usage import UsageTracker


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    server: _DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed_host():
            self.send_error(403, "Forbidden host")
            return
        path = urlsplit(self.path).path
        if path == "/api/stats":
            self._send_json()
        elif path == "/":
            self._send_html()
        else:
            self.send_error(404, "Not found")

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        if not host:
            return False
        parsed = urlsplit(f"//{host}")
        try:
            port = parsed.port
        except ValueError:
            return False
        return parsed.hostname == "127.0.0.1" and port in (None, self.server.server_port)

    def _send_json(self) -> None:
        import json

        body = json.dumps(self.server.tracker.snapshot(), separators=(",", ":")).encode()
        self._send(200, "application/json; charset=utf-8", body, no_cache=True)

    def _send_html(self) -> None:
        try:
            body = files("notadory_memory").joinpath("dashboard.html").read_bytes()
        except Exception:
            self.send_error(500, "Dashboard unavailable")
            return
        self._send(200, "text/html; charset=utf-8", body)

    def _send(self, status: int, content_type: str, body: bytes, no_cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_cache:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)


class DashboardServer:
    """Lifecycle wrapper that never owns or exposes memory data."""

    def __init__(self, tracker: UsageTracker) -> None:
        self.tracker = tracker
        self._http: _DashboardHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None

    def start(self) -> bool:
        requested = self._port()
        try:
            self._http = self._bind(requested)
        except OSError as exc:
            print(f"Notadory dashboard port {requested} unavailable ({exc}); using an ephemeral port", file=sys.stderr)
            try:
                self._http = self._bind(0)
            except OSError as fallback:
                print(f"Notadory dashboard disabled: {fallback}", file=sys.stderr)
                return False
        self._http.tracker = self.tracker
        self._thread = threading.Thread(target=self._http.serve_forever, name="notadory-dashboard", daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._http.server_port}/"
        print(f"Notadory dashboard: {self.url}", file=sys.stderr)
        if os.getenv("NOTADORY_DASHBOARD_OPEN") == "1":
            try:
                webbrowser.open(self.url)
            except Exception as exc:
                print(f"Notadory dashboard browser open failed: {exc}", file=sys.stderr)
        return True

    @staticmethod
    def _port() -> int:
        try:
            port = int(os.getenv("NOTADORY_DASHBOARD_PORT", "8765"))
            if not 0 <= port <= 65535:
                raise ValueError("port must be between 0 and 65535")
            return port
        except ValueError:
            print("Notadory dashboard invalid NOTADORY_DASHBOARD_PORT; using 8765", file=sys.stderr)
            return 8765

    @staticmethod
    def _bind(port: int) -> _DashboardHTTPServer:
        return _DashboardHTTPServer(("127.0.0.1", port), _Handler)

    def stop(self) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._http = None
        self._thread = None
