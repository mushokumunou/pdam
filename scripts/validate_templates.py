#!/usr/bin/env python3
"""Validate templates/*.json against templates/schema.json plus the semantic rules that
JSON Schema cannot express.

Two layers on purpose:
  * structural — shapes, enums, patterns, per-type requirements (same rules as schema.json)
  * semantic   — duplicates, dangling relation targets, preset/index fields that do not exist,
                 missing `name` identifier, archive wired to a non-select field

Dependency-free (the schema file is the documented contract; this script implements it). If the
`jsonschema` package is installed, the JSON Schema is additionally enforced against it.

    python3 scripts/validate_templates.py [--self-test] [files...]
"""
from __future__ import annotations

import glob
import json
import re
import sys

TOP_KEYS = {"slug", "name", "icon", "note", "sort", "group", "display_template", "archive",
            "list", "index", "fields"}
FIELD_KEYS = {"field", "type", "required", "unique", "length", "precision", "scale", "default",
              "choices", "to", "on_delete", "display", "format", "note", "placeholder"}
TYPES = {"string", "text", "integer", "decimal", "boolean", "date", "timestamp",
         "select", "m2o", "m2m", "tags"}
GROUPS = {"assets", "reference", "derived"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


def load_specs(paths):
    specs = {}
    for p in paths:
        with open(p) as fh:
            specs[p] = json.load(fh)
    return specs


def structural(path, spec, errors):
    def err(msg):
        errors.append(f"{path}: {msg}")

    if not isinstance(spec, dict):
        return err("spec is not an object")
    unknown = set(spec) - TOP_KEYS
    if unknown:
        err(f"unknown top-level keys {sorted(unknown)}")
    for key in ("slug", "name", "group", "fields"):
        if key not in spec:
            err(f"missing required key '{key}'")
    slug = spec.get("slug")
    if slug is not None and not (isinstance(slug, str) and NAME_RE.match(slug)):
        err(f"invalid slug {slug!r}")
    if "group" in spec and spec["group"] not in GROUPS:
        err(f"invalid group {spec['group']!r}")
    if "sort" in spec and not isinstance(spec["sort"], int):
        err("sort must be an integer")
    if "archive" in spec:
        arc = spec["archive"]
        if not isinstance(arc, dict) or "field" not in arc or "value" not in arc:
            err("archive needs {field, value}")
        elif set(arc) - {"field", "value", "unarchive"}:
            err(f"archive has unknown keys {sorted(set(arc) - {'field', 'value', 'unarchive'})}")
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields:
        return err("fields must be a non-empty array")
    for i, f in enumerate(fields):
        where = f"fields[{i}]"
        if not isinstance(f, dict):
            err(f"{where} is not an object")
            continue
        unknown = set(f) - FIELD_KEYS
        if unknown:
            err(f"{where} unknown keys {sorted(unknown)}")
        name = f.get("field")
        if not isinstance(name, str) or not NAME_RE.match(name):
            err(f"{where} invalid field name {name!r}")
        ftype = f.get("type")
        if ftype not in TYPES:
            err(f"{where} invalid type {ftype!r}")
            continue
        if ftype == "select" and not f.get("choices"):
            err(f"{where} select requires choices")
        if ftype in ("m2o", "m2m", "tags") and not f.get("to"):
            err(f"{where} {ftype} requires 'to'")
        if ftype == "decimal" and f.get("scale", 0) > f.get("precision", 0):
            err(f"{where} scale exceeds precision")
        if "on_delete" in f and ftype != "m2o":
            err(f"{where} on_delete only applies to m2o")


def semantic(path, spec, all_slugs, errors):
    def err(msg):
        errors.append(f"{path}: {msg}")

    slug = spec.get("slug")
    fields = spec.get("fields") or []
    by_name = {}
    for f in fields:
        n = f.get("field")
        if n in by_name:
            err(f"duplicate field '{n}'")
        by_name[n] = f

    if "name" not in by_name:
        err("missing the universal 'name' identifier field")

    for f in fields:
        if f.get("type") in ("m2o", "m2m", "tags"):
            target = f.get("to")
            if target and target not in all_slugs:
                err(f"relation '{f['field']}' targets unknown collection '{target}'")
            if target == slug:
                err(f"relation '{f['field']}' targets its own collection")

    arc = spec.get("archive")
    if arc:
        fld = by_name.get(arc.get("field"))
        if not fld:
            err(f"archive.field '{arc.get('field')}' does not exist")
        elif fld.get("type") != "select":
            err(f"archive.field '{arc['field']}' must be a select")
        elif arc.get("value") not in (fld.get("choices") or []):
            err(f"archive.value '{arc.get('value')}' is not one of the choices")

    listing = spec.get("list") or {}
    for col in listing.get("columns") or []:
        if col not in by_name:
            err(f"list.column '{col}' does not exist")
    for col in (listing.get("widths") or {}):
        if col not in by_name:
            err(f"list.widths references unknown field '{col}'")
    for col in listing.get("sort") or []:
        if col.lstrip("-") not in by_name:
            err(f"list.sort references unknown field '{col}'")

    idx = spec.get("index")
    if idx:
        for key in ("name", "expires", "renewal"):
            v = idx.get(key)
            if v and v not in by_name:
                err(f"index.{key} '{v}' does not exist")
        for key in ("provider", "account"):
            v = idx.get(key)
            if not v:
                continue
            head, _, tail = v.partition(".")
            if head not in by_name:
                err(f"index.{key} '{v}' references unknown field '{head}'")
            elif by_name[head].get("type") != "m2o":
                err(f"index.{key} '{v}' must point at an m2o field")
            elif tail != "name":
                err(f"index.{key} '{v}' must end in '.name'")
        for extra in idx.get("extra") or []:
            if extra not in by_name:
                err(f"index.extra '{extra}' does not exist")


def validate(specs):
    all_slugs = {s.get("slug") for s in specs.values() if isinstance(s, dict)}
    errors: list[str] = []
    for path, spec in specs.items():
        structural(path, spec, errors)
        semantic(path, spec, all_slugs - {spec.get("slug")}, errors)
    return errors


def also_jsonschema(path, spec, schema, errors):
    try:
        import jsonschema
    except ImportError:
        return False
    v = jsonschema.Draft202012Validator(schema)
    for e in v.iter_errors(spec):
        errors.append(f"{path}: schema: {'/'.join(map(str, e.path))}: {e.message}")
    return True


# --------------------------------------------------------------------------- self test

def self_test() -> int:
    base = {
        "slug": "probe", "name": "Probe", "group": "assets", "icon": "bug_report", "sort": 5,
        "archive": {"field": "status", "value": "archived", "unarchive": "active"},
        "list": {"columns": ["name", "status"], "sort": ["name"]},
        "index": {"name": "name"},
        "fields": [
            {"field": "name", "type": "string", "length": 120, "required": True},
            {"field": "status", "type": "select", "choices": ["active", "archived"],
             "default": "active"},
        ],
    }
    cases = {
        "valid minimal spec": (base, True),
        "missing slug": ({k: v for k, v in base.items() if k != "slug"}, False),
        "duplicate field": (dict(base, fields=base["fields"] + [base["fields"][0]]), False),
        "invalid field type": (dict(base, fields=[{"field": "name", "type": "wat"}]), False),
        "relation target missing": (dict(base, fields=base["fields"] + [
            {"field": "owner", "type": "m2o", "to": "nope"}]), False),
        "m2m without target": (dict(base, fields=base["fields"] + [
            {"field": "domains", "type": "m2m"}]), False),
        "preset column missing": (dict(base, list={"columns": ["name", "ghost"]}), False),
        "archive field missing": (dict(base, archive={"field": "ghost", "value": "archived"}), False),
        "no name identifier": (dict(base, fields=[{"field": "title", "type": "string"}]), False),
        "unknown top-level key": (dict(base, nonsense=True), False),
        "select without choices": (dict(base, fields=[
            {"field": "name", "type": "string"}, {"field": "status", "type": "select"}]), False),
        "index provider not m2o": (dict(base, index={"name": "name", "provider": "name.name"}), False),
    }
    ok = True
    for label, (spec, should_pass) in cases.items():
        errs = validate({"<synthetic>": spec})
        passed = not errs
        good = passed == should_pass
        ok = ok and good
        print(f"  {'OK ' if good else 'BAD'}  {label}: "
              f"{'accepted' if passed else 'rejected'}"
              + (f" ({errs[0]})" if errs else ""))
    print("self-test PASS" if ok else "self-test FAILED")
    return 0 if ok else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--self-test" in sys.argv:
        return self_test()

    paths = args or sorted(p for p in glob.glob("templates/*.json")
                           if not p.endswith("schema.json"))
    specs = load_specs(paths)
    errors = validate(specs)

    schema_path = "templates/schema.json"
    used_lib = False
    try:
        schema = json.load(open(schema_path))
    except FileNotFoundError:
        schema = None
    if schema:
        for path, spec in specs.items():
            used_lib = also_jsonschema(path, spec, schema, errors) or used_lib

    for e in errors:
        print(f"INVALID  {e}")
    if errors:
        print(f"\n{len(errors)} problem(s) across {len(specs)} templates")
        return 1
    print(f"PASS  {len(specs)} template specs valid"
          + (" (schema.json enforced via jsonschema)" if used_lib else " (built-in rules; jsonschema not installed)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
