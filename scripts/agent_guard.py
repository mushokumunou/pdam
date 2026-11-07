#!/usr/bin/env python3
"""Thin MCP guard: refuse unscoped `items update` before it reaches Directus.

Why: Directus' MCP `items` tool applies an update to **every row** when neither `keys` nor
`query.filter` is supplied (verified in Phase 8A, it rewrote two rows during testing). Directus
cannot reject that server-side on this licence (row-level permission rules are gated), so PDAM
enforces it in the layer it controls.

Layer honesty: this is a **client-side/tooling guard**, not a security boundary. An agent that
bypasses it and calls Directus' `/mcp` directly still has collection-level UPDATE permission — that
is the accepted limitation recorded in docs/security.md. The real security boundary remains the
Directus policy (no delete, no schema/policy writes, asset_index read-only).

Usage:
    python3 scripts/agent_guard.py --port 8787        # run a local proxy: point MCP clients here
    python3 scripts/agent_guard.py --check < payload  # validate one JSON-RPC request, exit 1 if denied
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://pdam.8w8.xyz"
GUARD_MESSAGE = ("PDAM guard: unscoped update rejected — supply non-empty `keys` or an explicit "
                 "`query.filter`. Unscoped `items update` would rewrite the whole collection.")


def guard_error(payload: dict) -> dict | None:
    """Return a JSON-RPC error object when the request must not be forwarded."""
    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return None
    params = payload.get("params") or {}
    if params.get("name") != "items":
        return None
    args = params.get("arguments") or {}
    action = args.get("action")
    if action != "update":
        return None                      # create/read need no target; delete is denied downstream
    keys = args.get("keys")
    filt = (args.get("query") or {}).get("filter")
    has_keys = isinstance(keys, list) and len(keys) > 0
    has_filter = isinstance(filt, dict) and len(filt) > 0
    if has_keys or has_filter:
        return None
    return {"jsonrpc": "2.0", "id": payload.get("id"),
            "error": {"code": -32602, "message": GUARD_MESSAGE,
                      "data": {"collection": args.get("collection"),
                               "reason": "unscoped_update"}}}


class Handler(BaseHTTPRequestHandler):
    upstream = DEFAULT_UPSTREAM

    def log_message(self, fmt, *args):          # keep stdout for results
        sys.stderr.write("guard: " + fmt % args + "\n")

    def do_POST(self):                          # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = None

        denied = guard_error(payload) if payload is not None else None
        if denied is not None:
            body = json.dumps(denied).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        headers = {"Content-Type": "application/json",
                   "Accept": self.headers.get("Accept", "application/json, text/event-stream"),
                   "User-Agent": "pdam-agent-guard/1.0"}
        if self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]
        req = urllib.request.Request(self.upstream.rstrip("/") + self.path, data=raw,
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                status = r.status
                ctype = r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            body = e.read()
            status = e.code
            ctype = e.headers.get("Content-Type", "application/json")
        except Exception as e:                  # pragma: no cover
            body = json.dumps({"jsonrpc": "2.0", "id": (payload or {}).get("id"),
                               "error": {"code": -32000, "message": f"upstream error: {e}"}}).encode()
            status, ctype = 502, "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                           # noqa: N802
        self.send_response(405)
        self.end_headers()


def serve(port: int, upstream: str) -> ThreadingHTTPServer:
    Handler.upstream = upstream
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser(description="PDAM MCP mutation guard")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    ap.add_argument("--check", action="store_true", help="read one JSON-RPC request on stdin")
    args = ap.parse_args()

    if args.check:
        payload = json.load(sys.stdin)
        err = guard_error(payload)
        if err:
            print(json.dumps(err, indent=2))
            return 1
        print("allowed")
        return 0

    httpd = serve(args.port, args.upstream)
    print(f"PDAM guard listening on http://127.0.0.1:{args.port}/mcp -> {args.upstream}/mcp")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
