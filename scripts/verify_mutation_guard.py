#!/usr/bin/env python3
"""Verify the PDAM mutation guard: unit rules + real traffic through the local MCP proxy.

Proves the invariant "no unscoped update" holds in the layer PDAM controls, and that the guard does
not damage legitimate create/read/delete-deny behaviour.

    python3 scripts/verify_mutation_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_guard import guard_error, serve  # noqa: E402

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ADMIN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
AGENT = open(os.environ.get("PDAM_AGENT_TOKEN_FILE", ".agent-token")).read().strip()
RESULTS: list[tuple[bool, str, str]] = []
PORT = int(os.environ.get("PDAM_GUARD_PORT", "8789"))


def check(name, ok, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def raw_call(token, method, path, body=None, base=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "pdam-guard-test/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request((base or BASE) + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf8", "replace")}


def rpc(rid, name, args):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def unit_tests():
    cases = [
        ("update with keys", rpc(1, "items", {"action": "update", "collection": "vps",
                                             "keys": ["x"], "data": {"a": 1}}), False),
        ("update with explicit filter", rpc(2, "items", {"action": "update", "collection": "vps",
                                                        "data": {"a": 1},
                                                        "query": {"filter": {"status": {"_eq": "expired"}}}}), False),
        ("update with neither", rpc(3, "items", {"action": "update", "collection": "vps",
                                                 "data": {"a": 1}}), True),
        ("update with empty keys", rpc(4, "items", {"action": "update", "collection": "vps",
                                                    "keys": [], "data": {"a": 1}}), True),
        ("update with empty filter", rpc(5, "items", {"action": "update", "collection": "vps",
                                                      "data": {"a": 1}, "query": {"filter": {}}}), True),
        ("create needs no target", rpc(6, "items", {"action": "create", "collection": "vps",
                                                    "data": {"name": "x"}}), False),
        ("delete passes (denied downstream)", rpc(7, "items", {"action": "delete",
                                                               "collection": "vps",
                                                               "keys": ["x"]}), False),
        ("read passes", rpc(8, "items", {"action": "read", "collection": "vps",
                                         "query": {"limit": 1}}), False),
        ("other tool passes", rpc(9, "collections", {"action": "read"}), False),
    ]
    for label, payload, should_deny in cases:
        denied = guard_error(payload) is not None
        check(f"unit: {label}", denied == should_deny,
              "denied" if denied else "allowed")


def integration_tests():
    # idempotent: clear any fixture left by a previous failed run
    for r in raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                               "&fields=id")[1].get("data", []):
        raw_call(ADMIN, "DELETE", f"/items/vps/{r['id']}")
    httpd = serve(PORT, BASE)
    proxy = f"http://127.0.0.1:{PORT}"
    try:
        code, body = raw_call(AGENT, "POST", "/mcp", rpc(10, "items", {
            "action": "read", "collection": "asset_index", "query": {"limit": 1}}), base=proxy)
        check("proxy: read forwards", code == 200 and "result" in (body or {}), f"http {code}")

        # fixtures: two VPS so an unscoped update would visibly damage both
        code, prov = raw_call(ADMIN, "GET", "/items/providers?limit=1&fields=id")
        prov_id = prov["data"][0]["id"]
        created = []
        for i in (1, 2):
            c, b = raw_call(AGENT, "POST", "/mcp", rpc(20 + i, "items", {
                "action": "create", "collection": "vps",
                "data": {"name": f"Guard-Probe-{i}", "provider": prov_id, "status": "active"}}),
                base=proxy)
            created.append((c, json.dumps(b)[:200]))
        names = [n for n in raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                                               "&fields=id,name")[1]["data"]]
        check("proxy: create unaffected (2 fixtures)", len(names) == 2, str([n["name"] for n in names]))

        before = {r["id"]: r["name"] for r in names}
        code, body = raw_call(AGENT, "POST", "/mcp", rpc(30, "items", {
            "action": "update", "collection": "vps", "data": {"name": "GUARD-BREACH"}}), base=proxy)
        error = ((body or {}).get("error") or {})
        check("proxy: unscoped update rejected before send",
              code == 200 and error.get("data", {}).get("reason") == "unscoped_update",
              json.dumps(error)[:120])
        after = {r["id"]: r["name"] for r in raw_call(
            ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe&fields=id,name")[1]["data"]}
        check("proxy: no rows were modified by the rejected call", before == after, str(after))

        target = list(before)[0]
        code, body = raw_call(AGENT, "POST", "/mcp", rpc(31, "items", {
            "action": "update", "collection": "vps", "keys": [target],
            "data": {"region": "GuardOK"}}), base=proxy)
        ok_rows = raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                                           "&fields=name,region")[1]["data"]
        regions = {r["name"]: r["region"] for r in ok_rows}
        check("proxy: scoped update (keys) reaches one row only",
              code == 200 and sorted((v or "") for v in regions.values()) == ["", "GuardOK"],
              str(regions))

        code, body = raw_call(AGENT, "POST", "/mcp", rpc(32, "items", {
            "action": "update", "collection": "vps", "data": {"region": "Filtered"},
            "query": {"filter": {"name": {"_eq": "Guard-Probe-2"}}}}), base=proxy)
        regions = {r["name"]: r["region"] for r in raw_call(
            ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                          "&fields=name,region")[1]["data"]}
        check("proxy: scoped update (filter) reaches one row only",
              regions.get("Guard-Probe-2") == "Filtered" and regions.get("Guard-Probe-1") == "GuardOK",
              str(regions))

        code, body = raw_call(AGENT, "POST", "/mcp", rpc(33, "items", {
            "action": "delete", "collection": "vps", "keys": [target]}), base=proxy)
        text = json.dumps(body or {})
        check("proxy: delete still denied downstream",
              "disabled" in text.lower() or "error" in text.lower() or "forbidden" in text.lower(),
              text[:120])

        for r in raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                                   "&fields=id")[1]["data"]:
            raw_call(ADMIN, "DELETE", f"/items/vps/{r['id']}")
        check("cleanup: fixtures removed",
              not raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe&fields=id")[1]["data"])
    finally:
        # fixtures must not survive a failed run
        for r in raw_call(ADMIN, "GET", "/items/vps?filter[name][_starts_with]=Guard-Probe"
                                   "&fields=id")[1].get("data", []):
            raw_call(ADMIN, "DELETE", f"/items/vps/{r['id']}")
        httpd.shutdown()


def main():
    unit_tests()
    integration_tests()
    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
