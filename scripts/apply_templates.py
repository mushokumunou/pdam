#!/usr/bin/env python3
"""Apply templates/*.json to a Directus instance (idempotent).

Spec (templates/<slug>.json) -> Directus collections/fields/relations/presets.
After a successful run, `d6s sync pull --from <profile>` captures the canonical
JSON into directus/default/ for review and commit.

Usage:
    DIRECTUS_URL=https://pdam.example.com DIRECTUS_TOKEN_FILE=.admin-token \
        python3 scripts/apply_templates.py [--only vps,domains]
"""
from __future__ import annotations

import glob
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
TOKEN_FILE = os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")
TOKEN = open(TOKEN_FILE).read().strip()
ONLY = None
for i, a in enumerate(sys.argv):
    if a == "--only":
        ONLY = set(sys.argv[i + 1].split(","))


def api(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "pdam-templates/1.0", "Accept": "application/json"})
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


def exists(path: str) -> bool:
    code, _ = api("GET", path)
    return code == 200


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- fields

def field_payload(slug: str, f: dict, sort: int) -> dict:
    name, kind = f["field"], f["type"]
    required = bool(f.get("required"))
    meta = {"interface": "input", "width": f.get("width", "full"), "sort": sort,
            "required": required, "hidden": False, "readonly": False}
    schema: dict | None = {"is_nullable": not required}

    if kind == "string":
        schema["data_type"] = "varchar"
        schema["max_length"] = f.get("length", 255)
    elif kind == "text":
        schema["data_type"] = "text"
    elif kind == "integer":
        schema["data_type"] = "integer"
    elif kind == "decimal":
        schema["data_type"] = "numeric"
        schema["numeric_precision"] = f.get("precision", 12)
        schema["numeric_scale"] = f.get("scale", 2)
        meta["interface"] = "input"
    elif kind == "boolean":
        schema["data_type"] = "boolean"
        meta["interface"] = "boolean"
        meta["options"] = {"label": f.get("label", name.replace("_", " "))}
    elif kind == "date":
        schema["data_type"] = "date"
        meta["interface"] = "datetime"
    elif kind == "timestamp":
        schema["data_type"] = "timestamp with time zone"
        schema["data_type"] = "timestamp"
        meta["interface"] = "datetime"
    elif kind == "select":
        schema["data_type"] = "varchar"
        schema["max_length"] = f.get("length", 32)
        meta["interface"] = "select-dropdown"
        meta["options"] = {"choices": [{"text": c.replace("_", " ").title(), "value": c}
                                       for c in f["choices"]]}
    elif kind == "m2o":
        schema["data_type"] = "uuid"
        meta["interface"] = "select-dropdown-m2o"
        meta["special"] = ["m2o"]
        meta["display"] = "related-values"
        meta["display_options"] = {"template": "{{" + f.get("display", "name").split(".")[-1] + "}}"}
    else:
        raise SystemExit(f"unknown field type {kind} in {slug}.{name}")

    if f.get("default") is not None:
        schema["default_value"] = f["default"]
    if f.get("unique"):
        schema["is_unique"] = True
    if f.get("validation"):
        meta["validation"] = f["validation"]
        meta["validation_message"] = f.get("validation_message", "invalid value")
    if f.get("note"):
        meta["note"] = f["note"]
    if f.get("placeholder"):
        meta["options"] = {"placeholder": f["placeholder"]}

    return {"field": name, "type": "uuid" if kind == "m2o" else
            ("decimal" if kind == "decimal" else ("integer" if kind == "integer" else
             ("boolean" if kind == "boolean" else ("text" if kind == "text" else
              ("date" if kind == "date" else ("timestamp" if kind == "timestamp" else "string")))))),
            "meta": meta, "schema": schema}


def system_fields() -> list[dict]:
    return [
        {"field": "id", "type": "uuid",
         "meta": {"hidden": True, "readonly": True, "interface": "input", "sort": 1,
                  "width": "full"},
         "schema": {"data_type": "uuid", "is_primary_key": True, "has_auto_increment": False,
                    "default_value": "gen_random_uuid()", "is_nullable": False}},
        {"field": "date_created", "type": "timestamp",
         "meta": {"special": ["date-created"], "interface": "datetime", "readonly": True,
                  "hidden": False, "width": "half", "sort": 900},
         "schema": {"data_type": "timestamp", "is_nullable": True}},
        {"field": "date_updated", "type": "timestamp",
         "meta": {"special": ["date-updated"], "interface": "datetime", "readonly": True,
                  "hidden": False, "width": "half", "sort": 901},
         "schema": {"data_type": "timestamp", "is_nullable": True}},
    ]


def collection_meta(spec: dict) -> dict:
    meta = {
        "icon": spec.get("icon", "table_chart"),
        "note": spec.get("note"),
        "sort": spec.get("sort", 100),
        "group": spec.get("group"),
        "display_template": spec.get("display_template"),
        "hidden": False,
        "singleton": False,
        "archive_app_filter": True,
        "accountability": "all",
    }
    if spec.get("archive"):
        meta["archive_field"] = spec["archive"]["field"]
        meta["archive_value"] = spec["archive"]["value"]
        meta["unarchive_value"] = spec["archive"].get("unarchive", "active")
    return meta


# --------------------------------------------------------------------------- appliers

def ensure_group(name: str, icon: str, note: str, sort: int):
    if exists(f"/collections/{name}"):
        return
    code, body = api("POST", "/collections", {
        "collection": name, "schema": None,
        "meta": {"icon": icon, "note": note, "sort": sort, "collapse": "open"}})
    log(f"  + group {name}: {code}")


def ensure_collection(spec: dict):
    slug = spec["slug"]
    if exists(f"/collections/{slug}"):
        log(f"  = collection {slug} exists")
        meta_patch = collection_meta(spec)
        api("PATCH", f"/collections/{slug}", {"meta": meta_patch})
    else:
        fields = system_fields()
        user_fields = [f for f in spec["fields"] if f["type"] not in ("tags",)]
        for i, f in enumerate(user_fields, start=10):
            fields.append(field_payload(slug, f, i))
        code, body = api("POST", "/collections", {
            "collection": slug, "meta": collection_meta(spec), "schema": {},
            "fields": fields})
        if code not in (200, 204):
            raise SystemExit(f"create {slug} failed {code}: {json.dumps(body)[:600]}")
        log(f"  + collection {slug} ({len(fields)} fields)")

    # fields added later (idempotent)
    for i, f in enumerate(spec["fields"], start=10):
        if f["type"] == "tags":
            continue
        if not exists(f"/fields/{slug}/{f['field']}"):
            code, body = api("POST", f"/fields/{slug}", field_payload(slug, f, i))
            if code not in (200, 204):
                raise SystemExit(f"add {slug}.{f['field']} failed {code}: {json.dumps(body)[:400]}")
            log(f"    + field {slug}.{f['field']}")
        if not exists(f"/fields/{slug}/{f['field']}"):
            pass
        else:
            sync_field_meta(slug, f, i)
        if f["type"] == "m2o":
            ensure_relation(slug, f)
    for f in spec["fields"]:
        if f["type"] == "tags":
            ensure_tags(slug, f)


def sync_field_meta(slug: str, f: dict, sort: int):
    """Keep existing field meta aligned with the spec (interface, options, validation…)."""
    payload = field_payload(slug, f, sort)
    meta = payload["meta"]
    meta["validation"] = f.get("validation")
    meta["validation_message"] = f.get("validation_message")
    code, body = api("PATCH", f"/fields/{slug}/{f['field']}", {"meta": meta})
    if code not in (200, 204):
        raise SystemExit(f"patch meta {slug}.{f['field']} failed {code}: {json.dumps(body)[:300]}")


def relations_all() -> list[dict]:
    code, body = api("GET", "/relations?limit=1000&fields=collection,field,related_collection")
    if code != 200 or not body:
        raise SystemExit(f"cannot read relations: {code}")
    return body["data"]


def relation_exists(slug: str, field: str, rels=None) -> bool:
    rels = rels if rels is not None else relations_all()
    return any(r["collection"] == slug and r["field"] == field for r in rels)


def ensure_relation(slug: str, f: dict, rels: list | None = None):
    if relation_exists(slug, f["field"], rels):
        return
    payload = {
        "collection": slug, "field": f["field"], "related_collection": f["to"],
        "meta": {"many_collection": slug, "many_field": f["field"],
                 "one_collection": f["to"], "one_field": None,
                 "one_deselect_action": "nullify"},
        "schema": {"on_delete": f.get("on_delete", "SET NULL")},
    }
    code, body = api("POST", "/relations", payload)
    if code not in (200, 204):
        raise SystemExit(f"relation {slug}.{f['field']} failed {code}: {json.dumps(body)[:400]}")
    log(f"    + relation {slug}.{f['field']} -> {f['to']} ({f.get('on_delete','SET NULL')})")


def ensure_tags(slug: str, f: dict):
    related = f["to"]
    junction = f"{slug}_{related}"
    fk_many, fk_one = f"{slug}_id", f"{related}_id"
    if not exists(f"/collections/{junction}"):
        code, body = api("POST", "/collections", {
            "collection": junction, "schema": {},
            "meta": {"hidden": True, "icon": "import_export", "note": f"junction {slug} <-> {related}"},
            "fields": [
                {"field": "id", "type": "integer",
                 "meta": {"hidden": True, "interface": "input", "readonly": True},
                 "schema": {"data_type": "integer", "is_primary_key": True,
                            "has_auto_increment": True, "is_nullable": False}},
                {"field": fk_many, "type": "uuid",
                 "meta": {"hidden": True, "interface": "select-dropdown-m2o", "special": ["m2o"]},
                 "schema": {"data_type": "uuid", "is_nullable": True}},
                {"field": fk_one, "type": "uuid",
                 "meta": {"hidden": True, "interface": "select-dropdown-m2o", "special": ["m2o"]},
                 "schema": {"data_type": "uuid", "is_nullable": True}},
            ]})
        if code not in (200, 204):
            raise SystemExit(f"junction {junction} failed {code}: {json.dumps(body)[:400]}")
        log(f"    + junction {junction}")

    rels = relations_all()
    for coll, fld, rel, jf, one in (
            (junction, fk_many, slug, fk_one, f["field"]),
            (junction, fk_one, related, fk_many, None)):
        if relation_exists(coll, fld, rels):
            continue
        code, body = api("POST", "/relations", {
            "collection": coll, "field": fld, "related_collection": rel,
            "meta": {"many_collection": coll, "many_field": fld, "one_collection": rel,
                     "one_field": one, "one_deselect_action": "nullify",
                     "junction_field": jf},
            "schema": {"on_delete": "CASCADE" if rel == related else "SET NULL"}})
        if code not in (200, 204):
            raise SystemExit(f"m2m relation {coll}.{fld} failed {code}: {json.dumps(body)[:400]}")
        log(f"    + m2m relation {coll}.{fld} -> {rel}")

    if not exists(f"/fields/{slug}/{f['field']}"):
        code, body = api("POST", f"/fields/{slug}", {
            "field": f["field"], "type": "alias",
            "meta": {"special": ["m2m"], "interface": "list-m2m", "width": "full",
                     "sort": 800, "options": {"template": "{{" + fk_one + ".name}}"}},
            "schema": None})
        if code not in (200, 204):
            raise SystemExit(f"aliased m2m field {slug}.{f['field']} failed {code}: {json.dumps(body)[:400]}")
        log(f"    + m2m field {slug}.{f['field']}")


def ensure_preset(spec: dict):
    slug = spec["slug"]
    listing = spec.get("list") or {}
    cols = listing.get("columns") or ["name"]
    sort = listing.get("sort") or ["name"]
    code, body = api("GET", "/presets?limit=500&fields=id,collection,bookmark,user,role")
    mine = [p for p in (body or {}).get("data", [])
            if p["collection"] == slug and p["user"] is None and p["role"] is None]
    payload = {
        "collection": slug, "user": None, "role": None,
        "bookmark": spec["name"], "layout": "tabular",
        "layout_query": {"tabular": {"fields": cols, "sort": sort}},
        "layout_options": {"tabular": {"fields": cols,
                                       "widths": listing.get("widths") or {},
                                       "limit": 50}},
    }
    if code == 200 and mine:
        pid = mine[0]["id"]
        payload.pop("collection")
        c2, b2 = api("PATCH", f"/presets/{pid}", payload)
        log(f"  ~ preset {spec['name']}: {c2}")
    else:
        c2, b2 = api("POST", "/presets", payload)
        log(f"  + preset {spec['name']}: {c2}")


# --------------------------------------------------------------------------- derived collection

ASSET_INDEX = {
    "collection": "asset_index",
    "meta": {"icon": "table_view", "note": "Derived read model. Never write here.",
             "sort": 90, "group": "derived", "hidden": False, "singleton": False},
}
INDEX_FIELDS = [
    ("asset_id", "uuid", {"is_primary_key": True, "is_nullable": False}),
    ("template", "string", {"max_length": 60}),
    ("name", "text", {}),
    ("status", "string", {"max_length": 32}),
    ("provider_name", "text", {}),
    ("account_name", "text", {}),
    ("expires_on", "date", {}),
    ("renewal_on", "date", {}),
    ("tags", "text", {}),
    ("search_text", "text", {}),
    ("updated_at", "timestamp", {}),
]


def ensure_asset_index():
    if exists("/collections/asset_index"):
        log("  = collection asset_index exists")
        return
    fields = []
    for i, (name, kind, extra) in enumerate(INDEX_FIELDS, start=1):
        if kind == "uuid":
            meta = {"hidden": False, "readonly": True, "interface": "input", "sort": i, "width": "full"}
            schema = {"data_type": "uuid", "is_primary_key": True, "is_nullable": False}
        elif kind == "text":
            meta = {"readonly": True, "interface": "input-multiline", "sort": i, "width": "full"}
            schema = {"data_type": "text", "is_nullable": True}
        elif kind == "date":
            meta = {"readonly": True, "interface": "datetime", "sort": i, "width": "half"}
            schema = {"data_type": "date", "is_nullable": True}
        elif kind == "timestamp":
            meta = {"readonly": True, "interface": "datetime", "sort": i, "width": "half"}
            schema = {"data_type": "timestamp", "is_nullable": True}
        else:
            meta = {"readonly": True, "interface": "input", "sort": i, "width": "half"}
            schema = {"data_type": "varchar", "max_length": extra.get("max_length", 255),
                      "is_nullable": True}
        schema.update({k: v for k, v in extra.items() if k not in ("max_length",)})
        fields.append({"field": name, "type": kind, "meta": meta, "schema": schema})
    code, body = api("POST", "/collections", {
        "collection": "asset_index", "meta": ASSET_INDEX["meta"], "schema": {}, "fields": fields})
    if code not in (200, 204):
        raise SystemExit(f"asset_index failed {code}: {json.dumps(body)[:500]}")
    log(f"  + collection asset_index ({len(fields)} fields, read-only projection)")


# --------------------------------------------------------------------------- main

def load_specs() -> list[dict]:
    specs = [json.load(open(p)) for p in glob.glob(os.path.join(os.path.dirname(__file__), "..", "templates", "*.json"))]
    specs = [s for s in specs if not ONLY or s["slug"] in ONLY]
    refs = sorted([s for s in specs if s.get("group") == "reference"], key=lambda s: s.get("sort", 99))
    assets = sorted([s for s in specs if s.get("group") != "reference"], key=lambda s: s.get("sort", 99))
    return refs + assets


def main():
    log(f"target: {BASE}")
    code, me = api("GET", "/users/me")
    if code != 200:
        raise SystemExit(f"auth failed: {code} {me}")
    log(f"auth: {me['data']['email']}")

    ensure_group("assets", "inventory_2", "Asset types (templates)", 10)
    ensure_group("reference", "folder", "Shared reference data", 20)
    ensure_group("derived", "analytics", "Derived read models", 30)

    for spec in load_specs():
        log(f"[{spec['slug']}] {spec['name']}")
        ensure_collection(spec)
        ensure_preset(spec)

    if not ONLY:
        ensure_asset_index()
    log("done")


if __name__ == "__main__":
    main()
