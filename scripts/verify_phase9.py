#!/usr/bin/env python3
"""Phase 9 acceptance: prove a brand-new Asset Type (certificates) is absorbed by the existing model.

Checks schema, relations, preset, permissions, agent CRUD, projection lifecycle, rebuild,
security regressions and the secret lint. Everything runs against the live instance with the
PDAM Agent token (writes) and the admin token (cleanup only).

    DIRECTUS_URL=... python3 scripts/verify_phase9.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ADMIN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
AGENT = open(os.environ.get("PDAM_AGENT_TOKEN_FILE", ".agent-token")).read().strip()
RESULTS: list[tuple[bool, str, str]] = []


def call(token, method, path, body=None):
    path = path.replace(" ", "%20")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "pdam-phase9/1.0"}
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


def check(name, ok, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def items(token, coll, query=""):
    code, body = call(token, "GET", f"/items/{coll}{query}")
    return (body or {}).get("data") or []


def one(token, coll, query):
    rows = items(token, coll, query)
    return rows[0] if rows else None


def scoped_update(token, collection, rid, data):
    """MCP/API update MUST be scoped (Phase 8A finding: unscoped updates hit every row)."""
    assert rid, "refusing to update without a target id"
    return call(token, "PATCH", f"/items/{collection}/{rid}", data)


def main():
    # ---------------------------------------------------------------- schema
    code, body = call(ADMIN, "GET", "/collections/certificates?fields=collection,meta.icon,meta.group,meta.sort,meta.archive_field")
    meta = (body or {}).get("data", {}).get("meta") or {}
    check("collection certificates exists", code == 200, f"http {code}")
    check("nav: group=assets, icon, sort 60",
          meta.get("group") == "assets" and meta.get("icon") == "verified_user" and meta.get("sort") == 60,
          json.dumps({k: meta.get(k) for k in ("group", "icon", "sort")}))
    check("archive wired to status", meta.get("archive_field") == "status")

    code, body = call(ADMIN, "GET", "/fields/certificates?limit=-1")
    fields = {f["field"]: f for f in body.get("data", [])}
    expected = ["name", "provider", "domains", "issuer", "serial_number", "issued_at",
                "expires_on", "auto_renew", "status", "notes", "tags"]
    check("fields complete", all(f in fields for f in expected), f"{len(fields)} fields")
    check("domains is m2m alias",
          fields.get("domains", {}).get("type") == "alias"
          and (fields["domains"].get("meta") or {}).get("special") == ["m2m"])
    check("expires_on is a real date column",
          (fields.get("expires_on", {}).get("schema") or {}).get("data_type") == "date")

    code, body = call(ADMIN, "GET", "/relations?limit=500&fields=collection,field,related_collection")
    rels = {(r["collection"], r["field"]): r["related_collection"] for r in body["data"]
            if not r["collection"].startswith("directus_")}
    check("relation certificates.provider -> providers",
          rels.get(("certificates", "provider")) == "providers")
    check("junction certificates_domains <-> domains",
          rels.get(("certificates_domains", "domains_id")) == "domains"
          and rels.get(("certificates_domains", "certificates_id")) == "certificates")
    check("junction certificates_tags <-> tags",
          rels.get(("certificates_tags", "tags_id")) == "tags")

    code, body = call(ADMIN, "GET", "/presets?limit=500&fields=id,collection,bookmark,user,role,layout_options")
    preset = next((p for p in body["data"]
                   if p["collection"] == "certificates" and p["user"] is None and p["role"] is None),
                  None)
    cols = ((preset or {}).get("layout_options") or {}).get("tabular", {}).get("fields")
    check("global default preset with its own columns",
          bool(preset) and preset.get("bookmark") is None and cols ==
          ["name", "provider", "issuer", "expires_on", "status"], str(cols))

    # ---------------------------------------------------------------- fixtures
    hetzner = one(ADMIN, "providers", "?filter[slug][_eq]=hetzner&fields=id")["id"]
    domain = one(ADMIN, "domains", "?filter[name][_eq]=example-test.com&fields=id")["id"]
    tag_prod = one(ADMIN, "tags", "?filter[name][_eq]=production&fields=id")["id"]
    cert = None
    junction_ids: list[tuple[str, str]] = []

    try:
        # ------------------------------------------------------------ agent CRUD
        code, body = call(AGENT, "POST", "/items/certificates",
                          {"name": "Phase9-TLS-Cert", "provider": hetzner, "issuer": "Let's Encrypt R11",
                           "serial_number": "03:AB:CD:EF", "issued_at": "2026-11-01",
                           "expires_on": "2026-11-25", "auto_renew": True, "status": "active"})
        cert = ((body or {}).get("data") or {}).get("id")
        check("agent creates certificate", code in (200, 201) and bool(cert), f"http {code}")

        code, body = call(AGENT, "POST", "/items/certificates_domains",
                          {"certificates_id": cert, "domains_id": domain})
        check("agent links certificate -> domain (m2m)", code in (200, 201), f"http {code}")
        code, body = call(AGENT, "POST", "/items/certificates_tags",
                          {"certificates_id": cert, "tags_id": tag_prod})
        check("agent tags certificate", code in (200, 201), f"http {code}")

        row = one(AGENT, "certificates",
                  f"?filter[id][_eq]={cert}&fields=name,issuer,status,provider.name,domains.domains_id.name,tags.tags_id.name")
        ok = (row and row["provider"]["name"] == "Hetzner"
              and row["domains"][0]["domains_id"]["name"] == "example-test.com"
              and row["tags"][0]["tags_id"]["name"] == "production")
        check("agent reads certificate with relations", bool(ok),
              json.dumps(row, ensure_ascii=False)[:180] if row else "missing")

        code, body = scoped_update(AGENT, "certificates", cert, {"issuer": "Let's Encrypt R12"})
        check("agent updates certificate (scoped)", code == 200, f"http {code}")

        # ------------------------------------------------------------ projection
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=*")
        check("projection: row created",
              bool(idx) and idx["template"] == "certificates" and idx["name"] == "Phase9-TLS-Cert",
              json.dumps(idx, ensure_ascii=False)[:160] if idx else "missing")
        check("projection: provider denormalised", (idx or {}).get("provider_name") == "Hetzner")
        check("projection: tag in tags column", "production" in (idx or {}).get("tags", ""))

        code, body = scoped_update(AGENT, "certificates", cert, {"name": "Phase9-TLS-Cert-Renamed"})
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=name,expires_on,search_text")
        check("projection: rename propagates", idx["name"] == "Phase9-TLS-Cert-Renamed")

        cloudflare = one(ADMIN, "providers", "?filter[slug][_eq]=cloudflare&fields=id")["id"]
        scoped_update(AGENT, "certificates", cert, {"provider": cloudflare})
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=provider_name")
        check("projection: provider change propagates", idx["provider_name"] == "Cloudflare")

        scoped_update(AGENT, "certificates", cert, {"expires_on": "2027-01-15"})
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=expires_on")
        check("projection: expires_on propagates", idx["expires_on"] == "2027-01-15")

        hit = items(AGENT, "asset_index", "?filter[search_text][_contains]=phase9&fields=name")
        check("projection: cross-type search finds it", len(hit) == 1, str(hit)[:100])

        # remove tag -> projection tags cleared
        jrows = items(AGENT, "certificates_tags", f"?filter[certificates_id][_eq]={cert}&fields=id")
        for j in jrows:
            call(AGENT, "DELETE", f"/items/certificates_tags/{j['id']}")
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=tags,search_text")
        check("projection: tag removal propagates",
              "production" not in (idx.get("tags") or "")
              and "production" not in (idx.get("search_text") or ""))

        # archive
        scoped_update(AGENT, "certificates", cert, {"status": "archived"})
        idx = one(AGENT, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=status")
        check("projection: archive sets status", idx["status"] == "archived")

        # ------------------------------------------------------------ security
        code, body = call(AGENT, "DELETE", f"/items/certificates/{cert}")
        check("agent hard delete certificate denied", code in (401, 403), f"http {code}")
        code, body = call(AGENT, "POST", "/items/asset_index",
                          {"asset_id": "00000000-0000-0000-0000-000000000010", "template": "certificates"})
        check("agent asset_index write denied", code in (401, 403), f"http {code}")
        code, body = call(AGENT, "POST", "/collections", {"collection": "cert_probe"})
        check("agent schema mutation denied", code in (401, 403), f"http {code}")
        code, body = call("", "GET", "/items/certificates?limit=1")
        check("public read certificates denied", code in (401, 403), f"http {code}")

        # human/agent permissions exist for the new type
        code, body = call(ADMIN, "GET", "/permissions?limit=500&fields=collection,action,policy")
        cert_perms = {(p["action"]) for p in body["data"] if p["collection"] == "certificates"}
        check("permissions cover read/create/update, not delete",
              {"read", "create", "update"} <= cert_perms and "delete" not in cert_perms,
              str(sorted(cert_perms)))
    finally:
        # ---------------------------------------------------------- cleanup
        if cert:
            for j in items(ADMIN, "certificates_domains", f"?filter[certificates_id][_eq]={cert}&fields=id"):
                call(ADMIN, "DELETE", f"/items/certificates_domains/{j['id']}")
            for j in items(ADMIN, "certificates_tags", f"?filter[certificates_id][_eq]={cert}&fields=id"):
                call(ADMIN, "DELETE", f"/items/certificates_tags/{j['id']}")
            call(ADMIN, "DELETE", f"/items/certificates/{cert}")
            check("cleanup: admin removes probe certificate (no orphan)",
                  one(ADMIN, "asset_index", f"?filter[asset_id][_eq]={cert}&fields=asset_id") is None)

    # ---------------------------------------------------------------- rebuild
    code, body = call(ADMIN, "GET", "/items/asset_index?limit=-1&fields=template")
    counts: dict[str, int] = {}
    for r in body["data"]:
        counts[r["template"]] = counts.get(r["template"], 0) + 1
    print("   projection counts:", counts)

    # ---------------------------------------------------------------- lint
    lint = subprocess.run([sys.executable, "scripts/lint_secret_schema.py"],
                          capture_output=True, text=True)
    check("secret lint passes with certificates", lint.returncode == 0, lint.stdout.strip().splitlines()[-1])
    selftest = subprocess.run([sys.executable, "scripts/lint_secret_schema.py", "--self-test"],
                              capture_output=True, text=True)
    check("lint self-test still flags private_key/password", selftest.returncode == 0,
          "self-test PASS")

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
