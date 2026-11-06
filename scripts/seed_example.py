#!/usr/bin/env python3
"""Seed the acceptance dataset from the brief (§34) — idempotent by natural key.

    Cloudflare -> personal@example.com -> example.com / .net / .org
    Hetzner    -> Account A            -> Tokyo-01 / Falkenstein-01
    OpenAI     -> personal@example.com -> ChatGPT Plus -> Wise Card

Run after scripts/apply_templates.py. Safe to re-run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
TOKEN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
TODAY = dt.date.today()


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "pdam-seed/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def one(collection, key, value):
    code, body = api("GET", f"/items/{collection}?limit=5&fields=id,{key}")
    for row in (body or {}).get("data", []):
        if row.get(key) == value:
            return row["id"]
    return None


def ensure(collection, key, value, payload):
    got = one(collection, key, value)
    if got:
        print(f"  = {collection}:{value}")
        return got
    code, body = api("POST", f"/items/{collection}", payload)
    if code not in (200, 201):
        raise SystemExit(f"{collection} {value}: {code} {json.dumps(body)[:400]}")
    print(f"  + {collection}:{value}")
    return body["data"]["id"]


def main():
    code, me = api("GET", "/users/me")
    if code != 200:
        raise SystemExit(f"auth failed {code} {me}")

    tags = {}
    for name in ("production", "infra", "finance"):
        tags[name] = ensure("tags", "slug", name, {"name": name, "slug": name})

    prov = {}
    for name, slug, cat in (("Cloudflare", "cloudflare", "domain"),
                            ("Hetzner", "hetzner", "cloud"),
                            ("OpenAI", "openai", "ai"),
                            ("Wise", "wise", "finance")):
        prov[slug] = ensure("providers", "slug", slug,
                            {"name": name, "slug": slug, "category": cat, "status": "active"})

    acct = {}
    for name, provider, email in (
            ("Cloudflare Personal", "cloudflare", "personal@example.com"),
            ("Hetzner Personal", "hetzner", "personal@example.com"),
            ("OpenAI Personal", "openai", "personal@example.com")):
        acct[name] = ensure("accounts", "name", name, {
            "name": name, "provider": prov[provider], "email": email,
            "username": email.split("@")[0], "url": "https://example.com", "status": "active"})

    pm = ensure("payment_methods", "name", "Wise Card", {
        "name": "Wise Card", "provider": prov["wise"], "type": "card", "last_four": "4242",
        "currency": "USD", "expires_on": str(TODAY + dt.timedelta(days=400)),
        "status": "active"})

    vps = {}
    for name, region, os_, ip in (("Tokyo-01", "Tokyo", "Debian 13", "203.0.113.10"),
                                  ("Falkenstein-01", "Falkenstein", "Ubuntu 24.04", "203.0.113.20")):
        vps[name] = ensure("vps", "name", name, {
            "name": name, "provider": prov["hetzner"], "account": acct["Hetzner Personal"],
            "hostname": f"{name.lower()}.example.com", "ipv4": ip, "region": region, "os": os_,
            "status": "active", "expires_on": str(TODAY + dt.timedelta(days=180))})

    dom = {}
    for i, d in enumerate(("example.com", "example.net", "example.org")):
        dom[d] = ensure("domains", "name", d, {
            "name": d, "registrar": prov["cloudflare"], "account": acct["Cloudflare Personal"],
            "dns_provider": prov["cloudflare"], "primary_server": vps["Tokyo-01"],
            "expires_on": str(TODAY + dt.timedelta(days=30 + i * 200)), "auto_renew": True,
            "status": "active"})

    sub = ensure("subscriptions", "name", "ChatGPT Plus", {
        "name": "ChatGPT Plus", "provider": prov["openai"], "account": acct["OpenAI Personal"],
        "payment_method": pm, "price": 20.00, "currency": "USD", "billing_cycle": "monthly",
        "renewal_on": str(TODAY + dt.timedelta(days=14)),
        "started_on": str(TODAY - dt.timedelta(days=200)), "auto_renew": True, "status": "active"})

    # tags on assets (M2M junction: <collection>_tags)
    tag_targets = [("vps", vps["Tokyo-01"], "production"), ("vps", vps["Tokyo-01"], "infra"),
                   ("domains", dom["example.com"], "production"),
                   ("subscriptions", sub, "finance")]
    for coll, aid, tname in tag_targets:
        code, body = api("GET", f"/items/{coll}_tags?limit=50&fields=id,{coll}_id,tags_id")
        exists = any(r.get(f"{coll}_id") == aid and r.get("tags_id") == tags[tname]
                     for r in (body or {}).get("data", []))
        if exists:
            continue
        code, body = api("POST", f"/items/{coll}_tags", {f"{coll}_id": aid, "tags_id": tags[tname]})
        print(f"  + tag {tname} -> {coll} {aid[:8]}: {code}")

    print("seeded")


if __name__ == "__main__":
    main()
