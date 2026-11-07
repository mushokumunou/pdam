#!/usr/bin/env python3
"""Generic, discovery-driven verifier for every asset/reference template.

No hardcoded asset registry: templates/*.json is the list, and each check is capability-driven
(a template without `index` is not asked to project; one without `tags` is not asked to tag).

For each spec it verifies: collection metadata, field types, relations/junctions, global preset,
permissions (PDAM User, PDAM Agent, Public) and — when the spec declares an index mapping — the
full projection lifecycle (create/rename/status/tags/archive/cleanup).

    python3 scripts/verify_templates.py [--only slug]
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ADMIN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
AGENT = open(os.environ.get("PDAM_AGENT_TOKEN_FILE", ".agent-token")).read().strip()
ONLY = None
if "--only" in sys.argv:
    ONLY = sys.argv[sys.argv.index("--only") + 1]

RESULTS: list[tuple[bool, str, str]] = []


def call(token, method, path, body=None):
    path = path.replace(" ", "%20")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "pdam-verify-templates/1.0"}
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


def check(scope, name, ok, detail=""):
    RESULTS.append((ok, f"{scope}: {name}", detail))
    print(f"{'PASS' if ok else 'FAIL'}  [{scope}] {name}" + (f"  — {detail}" if detail else ""))


def rows(token, coll, query=""):
    code, body = call(token, "GET", f"/items/{coll}{query}")
    return (body or {}).get("data") or []


def first_id(token, coll):
    r = rows(token, coll, "?limit=1&fields=id")
    return r[0]["id"] if r else None


# PostgreSQL reports canonical names ("character varying"), Directus stores short ones.
TYPE_MAP = {
    "string": ({"varchar", "character varying"}, None),
    "text": ({"text"}, None),
    "integer": ({"integer", "int4"}, None),
    "decimal": ({"numeric", "decimal"}, None),
    "boolean": ({"boolean", "bool"}, None),
    "date": ({"date"}, None),
    "timestamp": ({"timestamp", "timestamp with time zone",
                   "timestamp without time zone"}, None),
    "select": ({"varchar", "character varying"}, None),
    "m2o": ({"uuid"}, ["m2o"]),
    "m2m": ({"alias"}, ["m2m"]),
    "tags": ({"alias"}, ["m2m"]),
}


def check_collection(spec):
    slug = spec["slug"]
    code, body = call(ADMIN, "GET", f"/collections/{slug}?fields=collection,meta,"
                                  "schema.name")
    if code != 200:
        check(slug, "collection exists", False, f"http {code}")
        return False
    meta = body["data"].get("meta") or {}
    check(slug, "nav metadata (group/icon/sort/archive)",
          meta.get("group") == spec.get("group")
          and meta.get("icon") == spec.get("icon")
          and (spec.get("sort") is None or meta.get("sort") == spec["sort"])
          and ((spec.get("archive") or {}).get("field") or None) == (meta.get("archive_field") or None),
          json.dumps({k: meta.get(k) for k in ("group", "icon", "sort", "archive_field")}))
    return True


def check_fields(spec):
    slug = spec["slug"]
    code, body = call(ADMIN, "GET", f"/fields/{slug}?limit=-1")
    live = {f["field"]: f for f in body.get("data", [])}
    problems = []
    for f in spec["fields"]:
        got = live.get(f["field"])
        if not got:
            problems.append(f"missing {f['field']}")
            continue
        want_type, want_special = TYPE_MAP[f["type"]]
        stype = (got.get("schema") or {}).get("data_type") or got.get("type")
        special = (got.get("meta") or {}).get("special")
        if f["type"] in ("m2m", "tags"):
            if got.get("type") != "alias" or special != ["m2m"]:
                problems.append(f"{f['field']} not an m2m alias")
        elif want_special:
            if special != want_special:
                problems.append(f"{f['field']} special={special}")
        elif want_type and stype not in want_type:
            problems.append(f"{f['field']} type {stype} not in {sorted(want_type)}")
    check(slug, f"field schema matches spec ({len(spec['fields'])} fields)",
          not problems, "; ".join(problems[:4]))


def check_relations(spec):
    slug = spec["slug"]
    code, body = call(ADMIN, "GET", "/relations?limit=500&fields=collection,field,"
                              "related_collection,meta,schema")
    rels = {(r["collection"], r["field"]): r for r in body["data"]
            if not r["collection"].startswith("directus_")}
    problems = []
    for f in spec["fields"]:
        if f["type"] == "m2o":
            r = rels.get((slug, f["field"]))
            if not r or r["related_collection"] != f["to"]:
                problems.append(f"{f['field']} -> {f['to']} missing")
            elif (r.get("schema") or {}).get("on_delete") != f.get("on_delete", "SET NULL"):
                problems.append(f"{f['field']} on_delete="
                                f"{(r.get('schema') or {}).get('on_delete')}")
        elif f["type"] in ("m2m", "tags"):
            j = f"{slug}_{f['to']}"
            if (j, f"{slug}_id") not in rels or (j, f"{f['to']}_id") not in rels:
                problems.append(f"junction {j} incomplete")
            elif rels[(j, f"{f['to']}_id")]["related_collection"] != f["to"]:
                problems.append(f"{j}.{f['to']}_id wrong target")
    check(slug, "relations/junctions match spec", not problems, "; ".join(problems[:4]))


def check_preset(spec):
    slug = spec["slug"]
    listing = spec.get("list") or {}
    code, body = call(ADMIN, "GET", "/presets?limit=500&fields=id,collection,bookmark,user,role,"
                              "layout_options")
    p = next((x for x in body["data"] if x["collection"] == slug
              and x["user"] is None and x["role"] is None), None)
    cols = ((p or {}).get("layout_options") or {}).get("tabular", {}).get("fields")
    want = listing.get("columns")
    ok = bool(p) and p.get("bookmark") is None and (want is None or cols == want)
    check(slug, "global default preset matches list.columns", ok, str(cols))


def check_permissions(spec):
    slug = spec["slug"]
    code, body = call(ADMIN, "GET", "/permissions?limit=500&fields=collection,action,policy")
    perms = [(p["policy"], p["action"]) for p in body["data"] if p["collection"] == slug]
    code, pb = call(ADMIN, "GET", "/policies?limit=50&fields=id,name")
    pid = {p["name"]: p["id"] for p in pb["data"]}
    for role_name, policy_name in (("PDAM User", "PDAM User"), ("PDAM Agent", "PDAM Agent")):
        actions = {a for pol, a in perms if pol == pid[policy_name]}
        ok = {"read", "create", "update"} <= actions and "delete" not in actions
        check(slug, f"{role_name} perms read/create/update, no delete", ok, str(sorted(actions)))
    public_actions = {a for pol, a in perms if pol == pid.get("$t:public_label")}
    check(slug, "no public permission rows", not public_actions, str(sorted(public_actions)))


def fixture_payload(spec, existing):
    """Minimal valid create payload from the spec (required fields only, plus a select default)."""
    payload = {}
    for f in spec["fields"]:
        if f["type"] == "string" and f.get("required"):
            payload[f["field"]] = f"verify-{spec['slug']}-{int(time.time())}"
        elif f["type"] == "m2o" and f.get("required"):
            target = existing.get(f["to"])
            if not target:
                return None
            payload[f["field"]] = target
        elif f["type"] == "select" and f.get("default"):
            payload[f["field"]] = f["default"]
        elif f["type"] == "boolean" and f.get("default") is not None:
            payload[f["field"]] = f["default"]
    if "name" not in payload and any(f["field"] == "name" for f in spec["fields"]):
        payload["name"] = f"verify-{spec['slug']}-{int(time.time())}"
    return payload


def check_projection(spec, existing):
    if not spec.get("index"):
        return
    slug = spec["slug"]
    payload = fixture_payload(spec, existing)
    if payload is None:
        check(slug, "projection: fixture prerequisites", False, "no target row for required m2o")
        return
    code, body = call(AGENT, "POST", f"/items/{slug}", payload)
    rid = ((body or {}).get("data") or {}).get("id")
    check(slug, "projection: agent creates fixture", code in (200, 201) and bool(rid),
          f"http {code}")
    if not rid:
        return
    try:
        idx = rows(AGENT, "asset_index", f"?filter[asset_id][_eq]={rid}&fields=*")
        row = idx[0] if idx else None
        check(slug, "projection: row appears with template/name",
              bool(row) and row["template"] == slug and row["name"] == payload.get("name"),
              (row or {}).get("name", "missing"))
        if row and spec.get("index", {}).get("provider") and payload.get("provider"):
            check(slug, "projection: provider denormalised", bool(row.get("provider_name")),
                  row.get("provider_name") or "")
        new_name = payload["name"] + "-renamed"
        call(AGENT, "PATCH", f"/items/{slug}/{rid}", {"name": new_name})
        row = rows(AGENT, "asset_index", f"?filter[asset_id][_eq]={rid}&fields=name")[0]
        check(slug, "projection: rename propagates", row["name"] == new_name)
        if any(f["field"] == "status" for f in spec["fields"]):
            call(AGENT, "PATCH", f"/items/{slug}/{rid}", {"status": "archived"})
            row = rows(AGENT, "asset_index", f"?filter[asset_id][_eq]={rid}&fields=status")[0]
            check(slug, "projection: archive propagates", row["status"] == "archived")
        tag_field = next((f for f in spec["fields"] if f["type"] == "tags"), None)
        if tag_field:
            tag = first_id(ADMIN, "tags")
            if tag:
                j = f"{slug}_tags"
                call(AGENT, "POST", f"/items/{j}", {f"{slug}_id": rid, "tags_id": tag})
                row = rows(AGENT, "asset_index", f"?filter[asset_id][_eq]={rid}&fields=tags")[0]
                check(slug, "projection: tag propagates", bool(row.get("tags")), row.get("tags") or "")
                for jr in rows(AGENT, j, f"?filter[{slug}_id][_eq]={rid}&fields=id"):
                    call(AGENT, "DELETE", f"/items/{j}/{jr['id']}")
        code, _ = call(AGENT, "DELETE", f"/items/{slug}/{rid}")
        check(slug, "agent hard delete denied", code in (401, 403), f"http {code}")
    finally:
        for f in spec["fields"]:
            if f["type"] in ("m2m", "tags"):
                j = f"{slug}_{f['to']}"
                for jr in rows(ADMIN, j, f"?filter[{slug}_id][_eq]={rid}&fields=id"):
                    call(ADMIN, "DELETE", f"/items/{j}/{jr['id']}")
        call(ADMIN, "DELETE", f"/items/{slug}/{rid}")
        orphan = rows(ADMIN, "asset_index", f"?filter[asset_id][_eq]={rid}&fields=asset_id")
        check(slug, "cleanup: projection has no orphan", not orphan, str(orphan))


def main():
    specs = [json.load(open(p)) for p in sorted(glob.glob("templates/*.json"))
             if not p.endswith("schema.json")]
    specs = [s for s in specs if not ONLY or s["slug"] == ONLY]
    specs.sort(key=lambda s: (s.get("group") != "reference", s.get("sort", 99)))
    print(f"discovered {len(specs)} templates: {[s['slug'] for s in specs]}")

    existing = {}
    for target in {f["to"] for s in specs for f in s["fields"]
                   if f.get("type") in ("m2o", "m2m", "tags")}:
        existing[target] = first_id(ADMIN, target)

    for spec in specs:
        print(f"\n--- {spec['slug']}")
        if not check_collection(spec):
            continue
        check_fields(spec)
        check_relations(spec)
        check_preset(spec)
        check_permissions(spec)
        check_projection(spec, existing)

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        for _, name, detail in failed:
            print(f"  FAILED: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
