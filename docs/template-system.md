# Template system

## 1. Definition

A **Template** is the definition of one kind of asset. It answers, in one place:

1. what the thing is (identity, icon, description, nav order),
2. which fields exist and of what type,
3. how each field is edited (interface, options, validation, conditions),
4. which columns the list shows, in what order and width,
5. which filters and default sort the list offers,
6. how the asset form is grouped and ordered,
7. which relations point where,
8. which lifecycle fields apply,
9. where it appears in navigation,
10. which table/API endpoint/MCP tools back it.

An **Asset** is one row in the template's collection (`Tokyo-01` in `vps`). Templates are types;
assets are instances. There is no per-asset schema and no per-asset "template copy".

## 2. Anatomy — a template is six native Directus records

| # | What the product calls it | Where it actually lives | Versioned by |
|---|---|---|---|
| 1 | Template identity + nav | `directus_collections` row (`icon`, `note`, `sort`, `group`, `hidden`, `translations`) | `directus/default/schema/<slug>.json` |
| 2 | Field schema | `directus_fields` rows (`type`, `interface`, `options`, `sort`, `group`, `validation`, `conditions`, `readonly`, `hidden`, `special`) | same file |
| 3 | Relations | `directus_relations` (FKs, M2M junctions) | same file |
| 4 | List view | `directus_presets` row, global scope (`user=null, role=null`), `layout_query.tabular.fields/sort`, `layout_options.tabular.widths`, `filters` | `directus/default/data/directus-presets_*.json` |
| 5 | Form layout | field `sort` + field-groups (`group` pointing to a group field) + `conditions` | schema file |
| 6 | Registry membership | `meta.group = assets` (the group is itself a collection with no table) | schema file |

Because all six are native metadata, `GET /collections?filter[group][_eq]=assets` and
`GET /fields/<slug>` already answer "what templates exist and what fields does VPS have". No
duplicated registry table exists to drift (ADR-002).

## 3. Authoring model

```
templates/<slug>.json          compact spec, human-authored (single source of truth)
        │  scripts/generate.py
        ▼
directus/default/schema/<slug>_<hash>.json    Directus collection definition
directus/default/data/directus-presets_*.json list view merged in
schema/asset_index.sql                        trigger + mapping for the read model
        │  d6s sync diff / push
        ▼
live instance (collections, fields, relations, presets, nav)
```

Two supported ways to change a template:

- **Spec-first (default).** Edit `templates/<slug>.json`, regenerate, review the JSON diff, push.
  Deterministic, reviewable, no UI clicks; the whole system can be rebuilt from the repo.
- **Studio-first (escape hatch).** Change it in Data Studio, then `d6s sync pull`, review and
  commit. Useful for exploratory field work; the diff lands in the same files.

Never edit both sides at once: pull before you push.

## 4. Spec file shape (example: VPS)

```json
{
  "slug": "vps",
  "name": "VPS",
  "icon": "dns",
  "note": "Virtual private servers",
  "sort": 10,
  "group": "assets",
  "display_template": "{{name}}",
  "archive": { "field": "status", "value": "archived", "unarchive_value": "active" },
  "list": {
    "sort": ["name"],
    "columns": ["name", "provider", "ipv4", "region", "os", "status"],
    "widths": { "name": 200, "provider": 160, "ipv4": 140, "region": 120, "os": 120, "status": 110 },
    "filters": [
      { "field": "provider", "interface": "select-dropdown" },
      { "field": "status", "interface": "select-dropdown" }
    ]
  },
  "index": {
    "name": "name", "provider": "provider.name", "account": "account.name",
    "expires": "expires_on", "renewal": null,
    "extra_search": ["hostname", "ipv4", "ipv6", "region", "os"]
  },
  "fields": [
    { "field": "name", "type": "string", "interface": "input", "required": true, "length": 120 },
    { "field": "provider", "type": "uuid", "interface": "select-dropdown-m2o",
      "relation": { "collection": "providers", "on_delete": "RESTRICT" }, "required": true },
    { "field": "account", "type": "uuid", "interface": "select-dropdown-m2o",
      "relation": { "collection": "accounts", "on_delete": "SET NULL" } },
    { "field": "hostname", "type": "string", "interface": "input" },
    { "field": "ipv4", "type": "string", "interface": "input", "length": 15,
      "validation": { "_regex": "^((25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(25[0-5]|2[0-4]\\d|1?\\d?\\d)$" },
      "validation_message": "IPv4 dotted quad" },
    { "field": "ipv6", "type": "string", "interface": "input", "length": 45 },
    { "field": "region", "type": "string", "interface": "input" },
    { "field": "os", "type": "string", "interface": "input" },
    { "field": "status", "type": "string", "interface": "select-dropdown",
      "choices": ["active", "inactive", "expired", "archived"], "default": "active" },
    { "field": "expires_on", "type": "date", "interface": "datetime" },
    { "field": "tags", "type": "tags", "interface": "tags", "special": ["m2m"], "relation": { "collection": "tags" } },
    { "field": "notes", "type": "text", "interface": "input-multiline" }
  ]
}
```

`scripts/apply_templates.py` owns the boring parts: uuid PK, `date_created`/`date_updated`,
`sort` numbering, `width: full`, interface and display defaults, group alias fields, junction
collection names, m2o/m2m relations, preset record shape, and (for `asset_index`) projection
registration. It is idempotent: re-running patches meta drift and adds only what is missing.

Spec keys implemented: `slug, name, icon, note, sort, group, display_template, archive, groups,
list{sort,columns,widths}, index{name,provider,account,expires,renewal,extra}, fields[]`
with field types `string, text, integer, decimal, boolean, date, timestamp, select, m2o, tags`.

## 5. Nav tab, then list, then form

- **Nav**: sidebar entry under the `assets` group, ordered by `sort`, icon from the spec, label from
  `translations` when present. Adding a template adds a tab; no frontend change (ADR-003).
- **List**: global preset named after the template. Because it is global (`role=null,user=null`)
  every user lands on the same columns; users may still personalise, which writes a *user-scoped*
  preset and leaves the template default untouched.
- **Form**: field `sort` order + field groups (`Basic`, `Network`, `Lifecycle`, `Billing`, `Notes`).
  Conditional visibility uses native `conditions`, e.g. `auto_renew` is irrelevant when
  `status = canceled`.
- **Filters**: preset `filters` gives one-click views ("Expiring in 30 days"); `select-dropdown`
  interfaces give the type filters (Provider / Status / Region).

## 6. Lifecycle of a new template (worked example: Certificate)

Brief Scenario 3 requires this to be cheap. It is:

1. `templates/certificate.json`:

```json
{ "slug": "certificate", "name": "Certificate", "icon": "verified_user",
  "group": "assets", "sort": 60, "list": { "columns": ["name","issuer","expires_on","status"] },
  "index": { "name": "name", "expires": "expires_on" },
  "fields": [
    { "field": "name", "type": "string", "required": true },
    { "field": "domains", "type": "uuid", "interface": "select-dropdown-m2o",
      "relation": { "collection": "domains", "on_delete": "SET NULL" } },
    { "field": "issuer", "type": "string" },
    { "field": "issued_on", "type": "date" },
    { "field": "expires_on", "type": "date" },
    { "field": "status", "type": "string", "interface": "select-dropdown",
      "choices": ["valid","expiring","expired","archived"], "default": "valid" }
  ] }
```

2. `python3 scripts/generate.py` → new schema file, preset merged, index mapping appended.
3. `d6s sync diff --to pdam` → `d6s sync push --to pdam` (single collection, additive).
4. `psql -f schema/asset_index.sql` (idempotent) → trigger + backfill.

Done, with **zero** changes to frontend code, form code, API code or agent code:

- sidebar shows `Certificate`
- its list shows Name / Issuer / Expires / Status
- Add Certificate renders its own five fields
- `GET /items/certificate`, `filter[expires_on][_lt]=…`, MCP tools, and the All list all work
- dashboard counts include it

If any of those required code changes, the abstraction failed — that is the acceptance test for
Scenario 4 and it is measurable, not rhetorical.

## 7. Relationship between "product template" and "Directus schema"

There is exactly one schema definition layer: Directus' own metadata, materialised as PostgreSQL
DDL. The product concept "Template" is a *view* over that metadata:

- **Product-visible**: slug (= collection), label, icon, order, fields, list columns, filters,
  relations, lifecycle fields.
- **Directus-native**: everything above; no parallel storage.
- **Generated**: `templates/*.json` → `directus/**` (spec is upstream, Directus records are the
  artifact, both committed).

"Directus schema templates" (the upstream project-level snapshot feature) are **not** used as the
product's Template concept; they are a migration mechanism. Environment Sync (`d6s`) supersedes
them here because it also carries presets, dashboards and permissions.

## 8. Guardrails

- A template's slug is immutable after first push (it is a table name, an API path and an MCP tool
  namespace). Renames are a migration, not a rename.
- Deleting a template is a destructive schema operation; export the rows first.
- Template fields may be added freely; type changes and drops need a data migration note in the PR.
- Do not add template-specific code paths — if a field needs behaviour, express it with native
  interfaces, `conditions`, `validation` or a Flow, not with a frontend branch.

## 9. Future: Template Builder UI

When the spec table becomes annoying to edit by hand, the builder is a form over the same six
records (create collection, add field, set preset) — an admin-only extension. It must write the
native records, then `d6s sync pull`. It must never introduce a second schema store.

## 10. Known limitation: field groups (12.6.1)

`type: alias` + `special: ["group"]` fields are invisible to the bulk `/fields` endpoint, so both the
API consumer and the Studio form lose them (and their children). Until upstream fixes
directus/directus#28379, templates express form structure with field order, hidden technical
fields, display modes and conditions only. Re-adding groups later is a metadata-only change
(the applier already knows how); no data migration is involved.
