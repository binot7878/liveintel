"""Development-only server for the byte-frozen Stitch application.

The server never edits the exported HTML.  It injects a screen identifier and
the shared binding layer while serving.  Writes are disabled unless both the
server environment and request header explicitly opt into development writes.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import stitch_data

ROOT = Path("/opt/liveintel")
SCREENS = ROOT / "stitch_frontend" / "screens"
FRONTEND = ROOT / "stitch_frontend"
DB_PATH = os.environ.get("STITCH_DB_PATH", stitch_data.PRODUCTION_DB)
WRITES_ENABLED = os.environ.get("STITCH_WRITES_ENABLED") == "1"
HOST = os.environ.get("STITCH_HOST", "127.0.0.1")
PORT = int(os.environ.get("STITCH_PORT", "8122"))

PRIMARY = {
    "live": "live_today_scoreboard",
    "players": "players_research_rotation_matrix",
    "heat": "heat_s_on_repeat_performers",
    "math": "math_lab_quantitative_edge_engine",
    "watch": "watch_5_rapid_target_radar_2",
    "picks": "picks_positions_specialist_challenge",
    "tickets": "tickets_settlement_portfolio_ledger",
    "intelligence": "intelligence_ticket_builder",
}
ALIASES = {"heats-on": "heat", "heat-s-on": "heat", "math-lab": "math", "watch-5": "watch"}
VARIANTS = {
    "math-heatmap-1": "math_lab_quantitative_edge_engine_prop_correlation_heatmap_1",
    "math-heatmap-2": "math_lab_quantitative_edge_engine_prop_correlation_heatmap_2",
    "watch-1": "watch_5_rapid_target_radar_1",
    "watch-2": "watch_5_rapid_target_radar_2",
    "watch-telemetry": "watch_5_rapid_target_radar_live_telemetry_odds_movement",
    "watch-slip": "watch_5_rapid_target_radar_with_interactive_betslip_drawer",
    "watch-1q": "watch_5_rapid_target_radar_with_1q_live_odds_tracking",
    "tickets-sgp": "tickets_settlement_ledger_with_sgp_correlation_suite",
}
ALL_SCREENS = set(PRIMARY.values()) | set(VARIANTS.values())

_cache_lock = threading.Lock()
_bootstrap_cache: dict[str, tuple[float, dict]] = {}


def resolve_screen(path: str, query: dict[str, list[str]]) -> str | None:
    explicit = (query.get("screen") or [""])[0]
    if explicit in ALL_SCREENS:
        return explicit
    route = path.strip("/") or "live"
    route = ALIASES.get(route, route)
    variant = (query.get("variant") or [""])[0]
    if variant in VARIANTS:
        return VARIANTS[variant]
    return PRIMARY.get(route) or VARIANTS.get(route)


def cached_bootstrap(screen: str) -> dict:
    now = time.monotonic()
    with _cache_lock:
        item = _bootstrap_cache.get(screen)
        if item and now - item[0] < 10:
            return item[1]
    connection = stitch_data.connect(DB_PATH)
    try:
        data = stitch_data.bootstrap(connection, screen)
    finally:
        connection.close()
    with _cache_lock:
        _bootstrap_cache[screen] = (now, data)
    return data


def clear_cache() -> None:
    with _cache_lock:
        _bootstrap_cache.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "LiveIntelStitchDev/1.0"

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("STITCH_QUIET") != "1":
            super().log_message(fmt, *args)

    def json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def file_response(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/stitch-app.js":
            return self.file_response(FRONTEND / "stitch-app.js", "application/javascript; charset=utf-8")
        if url.path == "/stitch-app.css":
            return self.file_response(FRONTEND / "stitch-app.css", "text/css; charset=utf-8")
        if url.path == "/api/stitch/bootstrap":
            screen = (query.get("screen") or [""])[0]
            if screen not in ALL_SCREENS:
                return self.json_response(400, {"error": "Unknown Stitch screen"})
            try:
                return self.json_response(200, cached_bootstrap(screen))
            except Exception as exc:
                return self.json_response(500, {"error": "Stitch data could not be loaded", "detail": str(exc) if os.environ.get("STITCH_DEBUG") == "1" else None})
        if url.path == "/api/stitch/updates":
            connection = stitch_data.connect(DB_PATH)
            try:
                return self.json_response(200, stitch_data.updates(connection))
            finally:
                connection.close()
        if url.path == "/api/stitch/game-log":
            game_id = (query.get("game_id") or [""])[0]
            connection = stitch_data.connect(DB_PATH)
            try:
                return self.json_response(200, {"game_id": game_id, "plays": stitch_data.game_logs(connection, game_id)})
            finally:
                connection.close()
        if url.path == "/api/stitch/providers":
            connection = stitch_data.connect(DB_PATH)
            try:
                return self.json_response(200, {"providers": stitch_data.provider_registry(connection)})
            finally:
                connection.close()
        if url.path == "/api/stitch/health":
            connection = stitch_data.connect(DB_PATH)
            try:
                return self.json_response(200, stitch_data.system_health(connection))
            finally:
                connection.close()
        if url.path == "/api/stitch/config":
            return self.json_response(200, {"environment": "DEVELOPMENT", "writes_enabled": WRITES_ENABLED,
                                            "wager_execution": False, "database": "isolated" if DB_PATH != stitch_data.PRODUCTION_DB else "production-read-only"})
        screen = resolve_screen(url.path, query)
        if screen:
            source = SCREENS / f"{screen}.html"
            if not source.is_file():
                self.send_error(404)
                return
            html = source.read_text()
            # One exported screen contains Playwright-only selector syntax in
            # browser JavaScript. Normalize only that invalid selector at serve
            # time; the authoritative HTML file and its DOM remain unchanged.
            html = html.replace("button:has-text, button", "button")

            # These routes use the shared runtime exclusively. Exported inline
            # simulators, export handlers and demo timers must never execute.
            # Preserve the head configuration and every frozen file on disk.
            if screen.startswith(("math_lab_", "watch_5_")) or screen in {"live_today_scoreboard", "heat_s_on_repeat_performers"}:
                head, boundary, body_html = html.partition("</head>")
                body_html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", body_html,
                                   flags=re.IGNORECASE | re.DOTALL)
                html = head + boundary + body_html

            injection = (f'<link rel="stylesheet" href="/stitch-app.css">'
                         f'<script>window.__STITCH_SCREEN__={json.dumps(screen)};window.__STITCH_ENV__="DEVELOPMENT";</script>'
                         f'<script src="/stitch-app.js" defer></script>')
            html = html.replace("</head>", injection + "</head>", 1)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be between 1 and 1000000 bytes")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        return payload

    def require_development_write(self) -> None:
        if not WRITES_ENABLED or self.headers.get("X-LiveIntel-Development") != "1":
            raise PermissionError("Development writes are disabled")
        if DB_PATH == stitch_data.PRODUCTION_DB:
            raise PermissionError("Development writes cannot target the production database")

    def do_POST(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        try:
            payload = self.body_json()
            if url.path == "/api/stitch/calculate":
                return self.json_response(200, stitch_data.calculation(payload))
            self.require_development_write()
            connection = stitch_data.connect(DB_PATH, readonly=False)
            try:
                if url.path == "/api/stitch/watch/add":
                    result = stitch_data.add_watch(connection, payload)
                elif url.path == "/api/stitch/watch/remove":
                    result = stitch_data.remove_watch(connection, payload)
                elif url.path == "/api/stitch/watch/reorder":
                    result = stitch_data.reorder_watch(connection, payload)
                elif url.path == "/api/stitch/tickets/record":
                    result = stitch_data.record_ticket(connection, payload)
                else:
                    return self.json_response(404, {"error": "Unknown action"})
            finally:
                connection.close()
            clear_cache()
            return self.json_response(200, result)
        except PermissionError as exc:
            return self.json_response(403, {"error": str(exc), "wager_placed": False})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.json_response(422, {"error": str(exc), "wager_placed": False})
        except Exception:
            return self.json_response(500, {"error": "Action failed safely", "wager_placed": False})


def run() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Exact Stitch development server: http://{HOST}:{PORT} db={DB_PATH} writes={WRITES_ENABLED}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run()
