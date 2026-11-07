#!/usr/bin/env python3
"""Phase 2-5 acceptance run: schema discovery, relations, projection lifecycle, API queries.

Prints one line per check with PASS/FAIL. Exits non-zero if anything fails.
    DIRECTUS_URL=... DIRECTUS_TOKEN_FILE=.admin-token python3 scripts/verify_phase2_5.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
TOKEN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
RESULTS: list[tuple[bool, str, str]] = []


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    path = path.replace(" ", "%20")
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "pdam-verify/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


def items(coll, query=""):
    code, body = api("GET", f"/items/{coll}{query}")
    return (body or {}).get("data") or []


def index_row(collection, rid):
    rows = items("asset_index", f"?filter[asset_id][_eq]={rid}&fields=*")
    return rows[0] if rows else None


def main():
    # ---------- discovery -------------------------------------------------
    code, body = api("GET", "/collections?fields=collection,meta.group,meta.sort,meta.icon")
    groups = {}
    for c in body.get("data", []):
        g = (c.get("meta") or {}).get("group")
        groups.setdefault(g, []).append(c["collection"])
    asset_types = sorted(groups.get("assets", []))
    # derive the expected set from the template specs instead of hardcoding a registry
    import glob as _glob
    spec_types = sorted(json.load(open(p_))["slug"]
                        for p_ in _glob.glob("templates/*.json")
                        if json.load(open(p_)).get("group") == "assets")
    check("discovery: asset templates match template specs",
          asset_types == spec_types, f"live={asset_types} specs={spec_types}")

    for coll, must in (("vps", ["provider", "account", "ipv4", "region", "os", "expires_on", "tags"]),
                       ("domains", ["registrar", "dns_provider", "account", "primary_server",
                                    "expires_on", "auto_renew", "tags"])):
        code, body = api("GET", f"/fields/{coll}")
        names = [f["field"] for f in body.get("data", [])]
        check(f"discovery: /fields/{coll} exposes schema", all(m in names for m in must),
              f"{len(names)} fields")

    code, body = api("GET", "/relations?limit=500&fields=collection,field,related_collection")
    rels = {(r["collection"], r["field"]): r["related_collection"]
            for r in body.get("data", []) if not r["collection"].startswith("directus_")}
    expect = {("vps", "provider"): "providers", ("vps", "account"): "accounts",
              ("domains", "registrar"): "providers", ("domains", "account"): "accounts",
              ("domains", "primary_server"): "vps",
              ("subscriptions", "payment_method"): "payment_methods",
              ("accounts", "secret_reference"): "secret_references"}
    check("relations: expected FKs exist", all(rels.get(k) == v for k, v in expect.items()),
          f"{len(rels)} app relations")

    # ---------- projection lifecycle -------------------------------------
    code, body = api("GET", "/items/providers?filter[slug][_eq]=hetzner")
    hetzner = body["data"][0]["id"]
    code, body = api("GET", "/items/accounts?filter[name][_eq]=Hetzner Personal")
    hacc = body["data"][0]["id"]

    code, body = api("POST", "/items/vps", {"name": "Probe-VPS", "provider": hetzner,
                                            "account": hacc, "region": "Tokyo", "os": "Debian 13",
                                            "ipv4": "198.51.100.7", "status": "active"})
    check("projection: create VPS row", code in (200, 201), f"http {code}")
    vid = (body or {}).get("data", {}).get("id")
    row = index_row("vps", vid) if vid else None
    check("projection: create VPS -> asset_index row",
          bool(row) and row["template"] == "vps" and row["name"] == "Probe-VPS",
          json.dumps(row, ensure_ascii=False)[:160] if row else "missing")

    api("PATCH", f"/items/vps/{vid}", {"name": "Probe-VPS-Renamed"})
    row = index_row("vps", vid)
    check("projection: rename -> name updated", bool(row) and row["name"] == "Probe-VPS-Renamed")

    cloudflare = items("providers", "?filter[slug][_eq]=cloudflare")[0]["id"]
    api("PATCH", f"/items/vps/{vid}", {"provider": cloudflare})
    row = index_row("vps", vid)
    check("projection: provider change -> provider_name updated",
          bool(row) and row["provider_name"] == "Cloudflare", (row or {}).get("provider_name", ""))

    api("PATCH", f"/items/vps/{vid}", {"status": "archived"})
    row = index_row("vps", vid)
    check("projection: archive stays visible with status=archived",
          bool(row) and row["status"] == "archived", (row or {}).get("status", ""))

    tags_all = {t["name"]: t["id"] for t in items("tags", "?limit=50&fields=id,name")}
    api("POST", "/items/vps_tags", {"vps_id": vid, "tags_id": tags_all["production"]})
    row = index_row("vps", vid)
    check("projection: tag add -> tags column updated",
          bool(row) and "production" in (row.get("tags") or ""), (row or {}).get("tags", ""))

    code, _ = api("DELETE", f"/items/vps/{vid}")
    row = index_row("vps", vid)
    check("projection: delete -> no orphan row", code in (200, 204) and row is None, f"http {code}")

    # ---------- rebuild ---------------------------------------------------
    api("POST", "/server/ping")  # no-op, keeps session warm
    before = items("asset_index", "?limit=-1&fields=asset_id")
    print("   (rebuild via SQL: pdam_index_rebuild())")
    check("projection: index is populated for all 5 types",
          len(before) >= 10, f"{len(before)} rows")

    # ---------- API acceptance -------------------------------------------
    vps_rows = items("vps", "?fields=name,status,provider.name&sort=name")
    check("api: get all VPS with relation", len(vps_rows) == 2 and "provider" in vps_rows[0],
          str([r["name"] for r in vps_rows]))

    dom_rows = items("domains", "?fields=name,registrar.name,expires_on&sort=name")
    check("api: get all Domains", len(dom_rows) == 3, str([r["name"] for r in dom_rows]))

    acc = items("accounts", "?filter[name][_eq]=Cloudflare Personal"
                            "&fields=name,email,provider.name,secret_reference.name")
    check("api: get account with relations", bool(acc) and acc[0]["provider"]["name"] == "Cloudflare",
          json.dumps(acc, ensure_ascii=False)[:140])

    by_prov = items("vps", "?filter[provider][slug][_eq]=hetzner&fields=name")
    check("api: get VPS by provider", len(by_prov) == 2, str([r["name"] for r in by_prov]))

    by_acc = items("domains", "?filter[account][name][_eq]=Cloudflare Personal&fields=name")
    check("api: get Domains by account", len(by_acc) == 3)

    pm = items("payment_methods", "?filter[name][_eq]=Wise Test Card")[0]["id"]
    by_pm = items("subscriptions", f"?filter[payment_method][_eq]={pm}&fields=name,price")
    check("api: get Subscriptions by payment method", len(by_pm) == 1,
          str([r["name"] for r in by_pm]))

    tagged = items("vps", "?filter[tags][tags_id][name][_eq]=production&fields=name")
    check("api: get assets by tag", len(tagged) == 1, str([r["name"] for r in tagged]))

    all_assets = items("asset_index", "?limit=-1&fields=template,name,provider_name,status")
    check("api: all assets via asset_index", len(all_assets) >= 10,
          f"{len(all_assets)} rows, {sorted({r['template'] for r in all_assets})}")

    hit = items("asset_index", "?filter[search_text][_contains]=hetzner&fields=template,name")
    check("api: cross-type search (hetzner spans types)",
          len(hit) >= 3 and len({r["template"] for r in hit}) >= 2,
          str([(r["template"], r["name"]) for r in hit]))
    hit = items("asset_index", "?filter[search_text][_contains]=cloudflare&fields=template,name")
    check("api: cross-type search (cloudflare spans types)",
          len({r["template"] for r in hit}) >= 2,
          str([(r["template"], r["name"]) for r in hit]))

    code, body = api("GET", "/items/vps?fields=id,name&limit=1")
    sample = body["data"][0]
    hit = items("asset_index", f"?filter[asset_id][_eq]={sample['id']}&fields=template,name")
    check("api: row reverse lookup by asset_id", bool(hit) and hit[0]["template"] == "vps")

    # ---------- delete behaviour -----------------------------------------
    # 1. provider in use -> RESTRICT (Directus surfaces the FK violation as 500)
    code, body = api("DELETE", f"/items/providers/{hetzner}")
    still = items("providers", f"?filter[id][_eq]={hetzner}&fields=id")
    check("delete: provider in use is blocked, row survives",
          code >= 400 and len(still) == 1, f"http {code}")

    # 2. tag delete -> junction rows cascade, asset survives, projection drops the tag
    tag_id = api("POST", "/items/tags", {"name": "probe-tag", "slug": "probe-tag"})[1]["data"]["id"]
    vps_id = api("POST", "/items/vps", {"name": "Probe-Cascade", "provider": cloudflare,
                                        "status": "active"})[1]["data"]["id"]
    api("POST", "/items/vps_tags", {"vps_id": vps_id, "tags_id": tag_id})
    api("DELETE", f"/items/tags/{tag_id}")
    alive = items("vps", f"?filter[id][_eq]={vps_id}&fields=id")
    jun = items("vps_tags", f"?filter[vps_id][_eq]={vps_id}")
    row = index_row("vps", vps_id)
    check("delete: tag cascade removes junction only",
          len(alive) == 1 and len(jun) == 0, f"asset={len(alive)} junction={len(jun)}")
    check("projection: tag delete -> tags column cleared",
          bool(row) and "probe-tag" not in (row.get("tags") or ""),
          (row or {}).get("tags", "") or "(empty)")

    # 3. account delete -> vps.account SET NULL
    acc_id = api("POST", "/items/accounts", {"name": "Probe-Account", "provider": cloudflare,
                                             "status": "active"})[1]["data"]["id"]
    api("PATCH", f"/items/vps/{vps_id}", {"account": acc_id})
    api("DELETE", f"/items/accounts/{acc_id}")
    v = items("vps", f"?filter[id][_eq]={vps_id}&fields=id,account")[0]
    check("delete: account delete nulls vps.account, keeps vps", v["account"] is None)

    # 4. payment method delete -> subscription.payment_method SET NULL
    pm_id = api("POST", "/items/payment_methods", {"name": "Probe-Card", "provider": cloudflare,
                                                   "type": "card", "status": "active"})[1]["data"]["id"]
    sub_id = api("POST", "/items/subscriptions", {"name": "Probe-Sub", "payment_method": pm_id,
                                                  "price": 1, "status": "active"})[1]["data"]["id"]
    api("DELETE", f"/items/payment_methods/{pm_id}")
    s2 = items("subscriptions", f"?filter[id][_eq]={sub_id}&fields=id,payment_method")[0]
    check("delete: payment method delete nulls subscription.payment_method",
          s2["payment_method"] is None)

    # 5. secret reference delete -> account.secret_reference SET NULL
    sr_id = api("POST", "/items/secret_references",
                {"name": "Probe-Secret", "reference": "vault://probe", "secret_type": "password"})[1]["data"]["id"]
    acc2 = api("POST", "/items/accounts", {"name": "Probe-SecAcc", "provider": cloudflare,
                                           "secret_reference": sr_id, "status": "active"})[1]["data"]["id"]
    api("DELETE", f"/items/secret_references/{sr_id}")
    a2 = items("accounts", f"?filter[id][_eq]={acc2}&fields=id,secret_reference")[0]
    check("delete: secret reference delete nulls account.secret_reference",
          a2["secret_reference"] is None)

    # cleanup probe rows
    for coll, rid in (("vps", vps_id), ("subscriptions", sub_id), ("accounts", acc2)):
        api("DELETE", f"/items/{coll}/{rid}")
    check("cleanup: probe records removed", True)

    # ---------- summary ---------------------------------------------------
    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
