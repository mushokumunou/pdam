#!/usr/bin/env python3
"""Phase 7 / 8A verification: prove the boundary is enforced by Directus, not by prompts.

Runs as three identities:
  agent  (PDAM Agent policy, static token from .agent-token)  -> ALLOW and DENY tests
  anon   (no credentials)                                     -> PUBLIC tests
  admin  (only for setup/cleanup of probe rows)

Any "expected deny -> actually allowed" is a hard failure (exit 1).

    DIRECTUS_URL=... DIRECTUS_TOKEN_FILE=.admin-token python3 scripts/verify_security.py
"""
from __future__ import annotations

import json
import os
import time
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ADMIN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
AGENT = open(os.environ.get("PDAM_AGENT_TOKEN_FILE", ".agent-token")).read().strip()

RESULTS: list[tuple[str, bool, str, str, str]] = []   # section, ok, name, expected, actual


def call(token, method, path, body=None):
    path = path.replace(" ", "%20")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "pdam-verify-sec/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
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


def record(section, name, ok, expected, actual):
    RESULTS.append((section, ok, name, expected, actual))
    print(f"{'PASS' if ok else 'FAIL'}  [{section}] {name}   expected={expected} actual={actual}")


def expect_allow(section, token, name, method, path, body=None, ok_codes=(200, 201, 204)):
    code, payload = call(token, method, path, body)
    record(section, name, code in ok_codes, "/".join(map(str, ok_codes)), str(code))
    return code, payload


def expect_deny(section, token, name, method, path, body=None, deny_codes=(401, 403)):
    code, payload = call(token, method, path, body)
    record(section, name, code in deny_codes, "/".join(map(str, deny_codes)), str(code))
    return code, payload


def id_of(payload):
    """Accepts a single-item response or a list response (`?limit=1`)."""
    data = (payload or {}).get("data")
    if isinstance(data, list):
        return data[0]["id"] if data else None
    return (data or {}).get("id")


def main():
    # ---------------------------------------------------------------- setup
    code, me = call(ADMIN, "GET", "/users/me?fields=id,email")
    if code != 200:
        raise SystemExit(f"admin auth failed: {code}")
    code, agent_me = call(AGENT, "GET", "/users/me?fields=id")
    record("IDENTITY", "agent token resolves to its own user", code == 200,
           "200", str(code))
    if code == 200:
        # field-level permissions hide email/role even on /users/me -> only id comes back
        print(f"      agent /users/me returns keys: {sorted(agent_me['data'].keys())}")
    agent_uid = agent_me["data"]["id"]

    code, refs = call(ADMIN, "GET", "/items/providers?limit=5&fields=id,name,slug")
    providers = {p["slug"]: p["id"] for p in refs["data"]}
    code, tags = call(ADMIN, "GET", "/items/tags?limit=50&fields=id,name")
    tag_ids = {t["name"]: t["id"] for t in tags["data"]}
    code, doms = call(ADMIN, "GET", "/items/domains?limit=1&fields=id")
    existing_domain = id_of(doms)
    code, accs = call(ADMIN, "GET", "/items/accounts?limit=1&fields=id")
    existing_account = id_of(accs)
    code, subs = call(ADMIN, "GET", "/items/subscriptions?limit=1&fields=id")
    existing_sub = id_of(subs)
    code, pms = call(ADMIN, "GET", "/items/payment_methods?limit=1&fields=id")
    existing_pm = id_of(pms)
    code, dash = call(ADMIN, "GET", "/dashboards?limit=1&fields=id")
    dash_id = id_of(dash)
    code, panels = call(ADMIN, "GET", f"/panels?limit=1&fields=id")
    panel_id = id_of(panels)
    # relation PK is (collection, field) — there is no `id` column
    rel_path = "/relations/vps/provider"
    code, pols = call(ADMIN, "GET", "/policies?limit=50&fields=id,name")
    agent_policy = next(p["id"] for p in pols["data"] if p["name"] == "PDAM Agent")
    code, roles = call(ADMIN, "GET", "/roles?limit=50&fields=id,name")
    admin_role = next(r["id"] for r in roles["data"] if r["name"] == "Administrator")

    probe_vps = probe_domain = probe_junction = None

    try:
        # ------------------------------------------------------------ ALLOW
        for coll in ("asset_index", "vps", "domains", "accounts", "subscriptions",
                     "payment_methods", "providers", "tags", "secret_references"):
            expect_allow("ALLOW", AGENT, f"read {coll}", "GET", f"/items/{coll}?limit=1")
        expect_allow("ALLOW", AGENT, "search asset_index",
                     "GET", "/items/asset_index?filter[search_text][_contains]=hetzner&fields=name")
        expect_allow("ALLOW", AGENT, "schema discovery: collections", "GET",
                     "/collections?fields=collection,meta.group")
        expect_allow("ALLOW", AGENT, "schema discovery: fields", "GET", "/fields/vps")
        expect_allow("ALLOW", AGENT, "schema discovery: relations", "GET",
                     "/relations?limit=10&fields=collection,field,related_collection")

        code, payload = expect_allow("ALLOW", AGENT, "create VPS", "POST", "/items/vps",
                                     {"name": "Agent-Probe-VPS", "provider": providers["hetzner"],
                                      "region": "Tokyo", "os": "Debian 13", "status": "active"})
        probe_vps = id_of(payload)
        expect_allow("ALLOW", AGENT, "update VPS", "PATCH", f"/items/vps/{probe_vps}",
                     {"region": "Osaka"})
        code, tagrow = expect_allow("ALLOW", AGENT, "add tag to VPS", "POST", "/items/vps_tags",
                                    {"vps_id": probe_vps, "tags_id": tag_ids["production"]})
        probe_junction = id_of(tagrow)
        expect_allow("ALLOW", AGENT, "read VPS with tag", "GET",
                     f"/items/vps/{probe_vps}?fields=name,tags.tags_id.name")
        expect_allow("ALLOW", AGENT, "remove tag from VPS", "DELETE",
                     f"/items/vps_tags/{probe_junction}")
        probe_junction = None

        code, payload = expect_allow("ALLOW", AGENT, "create Domain", "POST", "/items/domains",
                                     {"name": "agent-probe.test", "status": "active",
                                      "auto_renew": False})
        probe_domain = id_of(payload)
        expect_allow("ALLOW", AGENT, "update Domain", "PATCH", f"/items/domains/{probe_domain}",
                     {"auto_renew": True})
        expect_allow("ALLOW", AGENT, "create Provider", "POST", "/items/providers",
                     {"name": "Agent Probe Provider", "slug": "agent-probe-provider"})

        expect_allow("ALLOW", AGENT, "archive VPS (status=archived)", "PATCH",
                     f"/items/vps/{probe_vps}", {"status": "archived"})
        code, idx = call(AGENT, "GET",
                         f"/items/asset_index?filter[asset_id][_eq]={probe_vps}&fields=status")
        ok = code == 200 and idx["data"] and idx["data"][0]["status"] == "archived"
        record("ALLOW", "asset_index reflects archive", ok, "status=archived",
               json.dumps(idx.get("data") if code == 200 else code)[:80])
        expect_allow("ALLOW", AGENT, "unarchive VPS", "PATCH", f"/items/vps/{probe_vps}",
                     {"status": "active"})

        # ------------------------------------------------------------ DENY
        expect_deny("DENY", AGENT, "hard delete VPS", "DELETE", f"/items/vps/{probe_vps}")
        expect_deny("DENY", AGENT, "hard delete Domain", "DELETE",
                    f"/items/domains/{existing_domain}")
        expect_deny("DENY", AGENT, "hard delete Account", "DELETE",
                    f"/items/accounts/{existing_account}")
        expect_deny("DENY", AGENT, "hard delete Subscription", "DELETE",
                    f"/items/subscriptions/{existing_sub}")
        expect_deny("DENY", AGENT, "hard delete Payment Method", "DELETE",
                    f"/items/payment_methods/{existing_pm}")
        expect_deny("DENY", AGENT, "asset_index create", "POST", "/items/asset_index",
                    {"asset_id": "00000000-0000-0000-0000-000000000001", "template": "x"})
        expect_deny("DENY", AGENT, "asset_index update", "PATCH",
                    f"/items/asset_index/{probe_vps}", {"status": "active"})
        expect_deny("DENY", AGENT, "asset_index delete", "DELETE",
                    f"/items/asset_index/{probe_vps}")

        expect_deny("DENY", AGENT, "create collection", "POST", "/collections",
                    {"collection": "agent_probe_collection", "schema": {}})
        expect_deny("DENY", AGENT, "add field", "POST", "/fields/vps",
                    {"field": "agent_probe_field", "type": "string",
                     "schema": {"max_length": 10}})
        expect_deny("DENY", AGENT, "modify relation", "PATCH", rel_path,
                    {"meta": {"one_deselect_action": "delete"}})
        expect_deny("DENY", AGENT, "modify policy", "PATCH", f"/policies/{agent_policy}",
                    {"app_access": True, "admin_access": True})
        expect_deny("DENY", AGENT, "create permission", "POST", "/permissions",
                    {"policy": agent_policy, "collection": "asset_index", "action": "update",
                     "fields": ["*"]})
        expect_deny("DENY", AGENT, "modify dashboard", "PATCH", f"/dashboards/{dash_id}",
                    {"name": "hacked"})
        expect_deny("DENY", AGENT, "modify panel", "PATCH", f"/panels/{panel_id}",
                    {"width": 12})
        expect_deny("DENY", AGENT, "read users", "GET", "/users?limit=1")
        expect_deny("DENY", AGENT, "create role", "POST", "/roles", {"name": "agent-role"})
        expect_deny("DENY", AGENT, "self privilege escalation", "PATCH",
                    f"/users/{agent_uid}", {"role": admin_role})
        expect_deny("DENY", AGENT, "read system settings", "GET", "/settings")

        # ------------------------------------------------------------ HUMAN (PDAM User)
        user_pw = open(os.environ.get("PDAM_USER_PASSWORD_FILE", ".pdam-user-password")).read().strip()
        hcode, hbody = call("", "POST", "/auth/login",
                            {"email": os.environ.get("PDAM_USER_EMAIL", "pdam@8w8.xyz"),
                             "password": user_pw})
        human_token = ((hbody or {}).get("data") or {}).get("access_token")
        record("HUMAN", "PDAM User can sign in", hcode == 200 and bool(human_token), "200",
               str(hcode))
        if human_token:
            expect_allow("HUMAN", human_token, "read asset_index", "GET",
                         "/items/asset_index?limit=3")
            expect_allow("HUMAN", human_token, "read VPS", "GET", "/items/vps?limit=1")
            expect_allow("HUMAN", human_token, "create VPS", "POST", "/items/vps",
                         {"name": "Human-Probe-VPS", "provider": providers["hetzner"],
                          "status": "active"})
            code, payload = call(human_token, "GET", "/items/vps?filter[name][_eq]=Human-Probe-VPS&fields=id")
            human_vps = id_of(payload)
            expect_deny("HUMAN", human_token, "hard delete VPS", "DELETE",
                        f"/items/vps/{human_vps}")
            expect_deny("HUMAN", human_token, "asset_index create", "POST", "/items/asset_index",
                        {"asset_id": "00000000-0000-0000-0000-00000000000a", "template": "x"})
            # Directus ships a system permission row on directus_users scoped to $CURRENT_USER,
            # so a human may read their own profile only.
            hcode, hme = call(human_token, "GET", "/users?limit=10&fields=id")
            own = id_of(hme) if hcode == 200 else None
            record("HUMAN", "user list is scoped to self",
                   hcode == 200 and len(hme["data"]) == 1 and own is not None,
                   "1 row", f"{hcode} rows={len((hme or {}).get('data') or [])}")
            expect_deny("HUMAN", human_token, "read another user", "GET", f"/users/{agent_uid}")
            if human_vps:
                call(ADMIN, "DELETE", f"/items/vps/{human_vps}")

        # ------------------------------------------------------------ PUBLIC
        for coll in ("asset_index", "vps", "domains", "accounts", "subscriptions",
                     "payment_methods", "secret_references", "providers"):
            expect_deny("PUBLIC", "", f"anonymous read {coll}", "GET", f"/items/{coll}?limit=1")
        expect_deny("PUBLIC", "", "anonymous MCP", "POST", "/mcp",
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "anon", "version": "1"}}})

        # ------------------------------------------------------------ MCP
        def mcp_raw(body, tries=4):
            """Directus MCP intermittently answers 403 with an empty body (per-user limiter);
            treat that as retryable so the assertions stay about permissions."""
            for attempt in range(tries):
                code, payload = call(AGENT, "POST", "/mcp", body)
                if code == 403 and not payload:
                    time.sleep(3)
                    continue
                return code, payload
            return 403, None

        code, init = mcp_raw({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "pdam-verify", "version": "1"}}})
        record("MCP", "initialize", code == 200, "200", str(code))
        code, tools = mcp_raw({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in (tools or {}).get("result", {}).get("tools", [])]
        record("MCP", "tools/list", code == 200 and "items" in names, "200+tools", f"{code} {len(names)}")

        def mcp_tool(name, arguments):
            code, payload = mcp_raw({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                     "params": {"name": name, "arguments": arguments}})
            result = (payload or {}).get("result") or {}
            text = "".join(c.get("text", "") for c in result.get("content", []))
            return code, result.get("isError", False), text

        code, err, text = mcp_tool("items", {"action": "read", "collection": "asset_index",
                                             "query": {"fields": ["name", "template"], "limit": 3}})
        record("MCP", "items read asset_index", code == 200 and not err and "raw" in text,
               "rows", f"{code} err={err}")

        code, payload = call(ADMIN, "GET", "/items/providers?limit=1&fields=id")
        prov = payload["data"][0]["id"]
        code, err, text = mcp_tool("items", {"action": "create", "collection": "vps",
                                             "data": {"name": "MCP-Probe-VPS", "provider": prov,
                                                      "status": "active"}})
        mcp_vps = None
        if not err and code == 200:
            import re
            m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", text)
            mcp_vps = m.group(1) if m else None
        record("MCP", "items create VPS", code == 200 and not err and bool(mcp_vps), "created",
               f"{code} err={err}")

        code, err, text = mcp_tool("items", {"action": "update", "collection": "vps",
                                             "keys": [mcp_vps], "data": {"region": "MCP-Osaka"}})
        record("MCP", "items update VPS (scoped by keys)", code == 200 and not err, "updated",
               f"{code} err={err}")
        code, idx = call(AGENT, "GET",
                         f"/items/asset_index?filter[asset_id][_eq]={mcp_vps}&fields=status")
        record("MCP", "projection follows MCP write", code == 200 and bool(idx["data"]),
               "row exists", str(code))

        code, err, text = mcp_tool("items", {"action": "create", "collection": "asset_index",
                                             "data": {"asset_id": "00000000-0000-0000-0000-000000000009",
                                                      "template": "x"}})
        record("MCP", "items create asset_index denied", err or code >= 400, "error",
               f"{code} err={err}")

        code, err, text = mcp_tool("items", {"action": "delete", "collection": "vps",
                                             "keys": [mcp_vps]})
        record("MCP", "items delete VPS denied (mcp_allow_deletes=false)", err or code >= 400,
               "error", f"{code} err={err}")

        # defense in depth: with MCP deletes enabled, the Directus permission still refuses
        call(ADMIN, "PATCH", "/settings", {"mcp_allow_deletes": True})
        try:
            code, err, text = mcp_tool("items", {"action": "delete", "collection": "vps",
                                                 "keys": [mcp_vps]})
            denied = err or code >= 400 or "forbidden" in text.lower() or "permission" in text.lower()
            record("MCP", "delete still denied by policy when MCP deletes are enabled", denied,
                   "permission error", f"{code} err={err}")
        finally:
            call(ADMIN, "PATCH", "/settings", {"mcp_allow_deletes": False})

        code, err, text = mcp_tool("collections", {"action": "create",
                                                   "collection": "agent_mcp_probe"})
        record("MCP", "collections create denied", err or code >= 400, "error",
               f"{code} err={err}")

        # refuse-to-escalate: the agent cannot read the users collection even through MCP
        code, err, text = mcp_tool("items", {"action": "read", "collection": "directus_users",
                                             "query": {"limit": 1}})
        record("MCP", "items read directus_users denied", err or code >= 400, "error",
               f"{code} err={err}")

        if mcp_vps:
            call(ADMIN, "DELETE", f"/items/vps/{mcp_vps}")

        # ------------------------------------------------------ cleanup
        if probe_junction:
            call(ADMIN, "DELETE", f"/items/vps_tags/{probe_junction}")
        if probe_vps:
            call(ADMIN, "DELETE", f"/items/vps/{probe_vps}")
        if probe_domain:
            call(ADMIN, "DELETE", f"/items/domains/{probe_domain}")
        for p in call(ADMIN, "GET", "/items/providers?limit=10&fields=id,slug")[1]["data"]:
            if p["slug"] == "agent-probe-provider":
                call(ADMIN, "DELETE", f"/items/providers/{p['id']}")

        # ------------------------------------------------------ summary
        failed = [r for r in RESULTS if not r[1]]
        allow = [r for r in RESULTS if r[0] in ("ALLOW", "MCP", "HUMAN")]
        deny = [r for r in RESULTS if r[0] in ("DENY", "PUBLIC")]
        print(f"\nALLOW/MCP {sum(1 for r in allow if r[1])}/{len(allow)}   "
              f"DENY/PUBLIC {sum(1 for r in deny if r[1])}/{len(deny)}")
        print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        if failed:
            print("\nSECURITY FAILURES (expected deny but allowed, or allow but denied):")
            for section, _, name, exp, act in failed:
                print(f"  [{section}] {name}: expected {exp}, got {act}")
            sys.exit(1)

    finally:
        pass


if __name__ == "__main__":
    main()
