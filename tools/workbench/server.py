#!/usr/bin/env python3
"""Local development server and reverse proxy for Snippets Workbench.

Listens on port 4300 (or PORT env var) and provides:
  - GET /               -> serves index.html (Workbench dashboard with all tabs)
  - GET /healthz        -> returns {"ok": true}
  - /task-notif/*       -> reverse-proxies to http://127.0.0.1:4310/* (Task Notif)
  - /keep-alive/*       -> reverse-proxies to http://127.0.0.1:4311/* (Keep Alive)

The Emulation Engine runs locally on 127.0.0.1:4320 and is connected directly via the browser iframe.
"""

import http.server
import json
import os
import signal
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PORT = 4300
DEFAULT_HOST = "127.0.0.1"

PORT = int(os.environ.get("PORT", DEFAULT_PORT))
BIND_HOST = os.environ.get("BIND_HOST", DEFAULT_HOST)
TASK_NOTIF_UPSTREAM = os.environ.get("TASK_NOTIF_URL", "http://127.0.0.1:4310")
KEEP_ALIVE_UPSTREAM = os.environ.get("KEEP_ALIVE_URL", "http://127.0.0.1:4311")
ENGINE_UPSTREAM = os.environ.get("ENGINE_URL", "http://127.0.0.1:4320")

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(HERE, "index.html")


def make_offline_page(title, port, tool_dir, npm_script):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  :root {{ color-scheme: dark; --bg: #0e1116; --panel: #161b22; --border: #2a3140; --text: #e6edf3; --muted: #8b98a9; --accent: #4c8dff; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; }}
  .card {{ max-width: 520px; padding: 28px 32px; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; }}
  h2 {{ margin: 0 0 12px; font-size: 17px; display: flex; align-items: center; gap: 8px; }}
  p {{ margin: 0 0 10px; color: var(--muted); font-size: 13px; }}
  code {{ background: #1c2330; border: 1px solid var(--border); border-radius: 6px; padding: 2px 7px; color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  .tag {{ font-size: 11px; padding: 2px 8px; border-radius: 999px; background: rgba(210, 153, 34, 0.2); color: #d29922; border: 1px solid rgba(210, 153, 34, 0.4); }}
</style>
</head>
<body>
<div class="card">
  <h2><span>⚡</span> {title} <span class="tag">Port {port} Offline</span></h2>
  <p>{title} is not running locally on port <code>{port}</code>.</p>
  <p>To run it locally on your computer:</p>
  <p><code>cd tools/{tool_dir} &amp;&amp; {npm_script}</code></p>
  <p>Or if it is hosted on your remote VM, open an SSH tunnel:</p>
  <p><code>ssh -N -L {port}:127.0.0.1:{port} user@host</code></p>
</div>
</body>
</html>"""


class WorkbenchHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SnippetsWorkbench/1.0"

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path

        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if path in ("/", "/index.html"):
            if not os.path.exists(INDEX_FILE):
                self.send_error(404, "index.html not found")
                return
            with open(INDEX_FILE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path.startswith("/task-notif"):
            self.proxy_request(TASK_NOTIF_UPSTREAM, "/task-notif", "Task Notif", 4310, "task-notif", "npm run dev")
            return

        if path.startswith("/keep-alive"):
            self.proxy_request(KEEP_ALIVE_UPSTREAM, "/keep-alive", "Keep Alive", 4311, "keep-alive", "npm run dev")
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path.startswith("/task-notif"):
            self.proxy_request(TASK_NOTIF_UPSTREAM, "/task-notif", "Task Notif", 4310, "task-notif", "npm run dev")
            return
        if path.startswith("/keep-alive"):
            self.proxy_request(KEEP_ALIVE_UPSTREAM, "/keep-alive", "Keep Alive", 4311, "keep-alive", "npm run dev")
            return
        self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path.startswith("/task-notif"):
            self.proxy_request(TASK_NOTIF_UPSTREAM, "/task-notif", "Task Notif", 4310, "task-notif", "npm run dev")
            return
        if path.startswith("/keep-alive"):
            self.proxy_request(KEEP_ALIVE_UPSTREAM, "/keep-alive", "Keep Alive", 4311, "keep-alive", "npm run dev")
            return
        self.send_response(204)
        self.end_headers()

    def proxy_request(self, upstream_base, prefix, title, port, tool_dir, npm_script):
        url = urllib.parse.urlsplit(self.path)
        sub_path = url.path[len(prefix):]
        if not sub_path.startswith("/"):
            sub_path = "/" + sub_path
        if url.query:
            sub_path += "?" + url.query

        target_url = upstream_base.rstrip("/") + sub_path

        body = None
        length = self.headers.get("Content-Length")
        if length:
            try:
                body = self.rfile.read(int(length))
            except Exception:
                body = None

        req = urllib.request.Request(target_url, data=body, method=self.command)
        for header, value in self.headers.items():
            if header.lower() not in ("host", "connection", "transfer-encoding"):
                req.add_header(header, value)
        req.add_header("X-Forwarded-Prefix", prefix)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.send_response(resp.status)
                for header, value in resp.headers.items():
                    if header.lower() not in ("connection", "transfer-encoding"):
                        self.send_header(header, value)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for header, value in e.headers.items():
                if header.lower() not in ("connection", "transfer-encoding"):
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception:
            offline_html = make_offline_page(title, port, tool_dir, npm_script).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(offline_html)))
            self.end_headers()
            self.wfile.write(offline_html)

    def log_message(self, format, *args):
        sys.stderr.write(f"[workbench] {self.address_string()} - {format % args}\n")


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    server = ReusableTCPServer((BIND_HOST, PORT), WorkbenchHandler)
    url = f"http://{BIND_HOST}:{PORT}/"
    print("\n=======================================================")
    print("  🚀 Snippets Workbench Dashboard")
    print(f"  URL: {url}")
    print("  Tabs:")
    print("    • Task Notif       -> /task-notif/ (Port 4310)")
    print("    • Keep Alive       -> /keep-alive/ (Port 4311)")
    print("    • Emulation Engine -> http://127.0.0.1:4320/")
    print("=======================================================\n")

    def _sig_handler(_signum, _frame):
        print("\n[workbench] Shutting down workbench server...")
        server.shutdown()
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

