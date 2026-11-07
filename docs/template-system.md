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

## 6. Adding a new Asset Type — measured on `certificates` (Phase 9)

This section is the **actual procedure executed** to add the sixth asset type, not a plan.
Certificate was chosen because it is a type that did not exist when the system was designed.

### 6.1 Steps (in order, with what each one produced)

| # | step | command | result |
|---|---|---|---|
| 1 | declare the type | new `templates/certificate.json` (11 fields, 2 M2M, 1 M2O, preset, index mapping) | spec only, no code |
| 2 | generate Directus metadata | `python3 scripts/apply_templates.py` | collection `certificates` (14 fields incl. system), junction `certificates_domains`, junction `certificates_tags`, 3 relations, alias fields `domains`/`tags`, global preset |
| 3 | regenerate projection DDL | `python3 scripts/build_index_sql.py` | `schema/asset_index.sql` now holds 6 templates: `pdam_idx_certificates()` + 2 triggers (table + tags junction) |
| 4 | apply projection DDL | `psql -f schema/asset_index.sql` on the host | functions/triggers created idempotently, `pdam_index_rebuild()` covers the new table |
| 5 | grant permissions | `python3 scripts/apply_security.py` | 9 new permission rows (certificates r/c/u, both junctions r/c/d) for both policies — collections discovered from the specs |
| 6 | verify | `python3 scripts/verify_phase9.py` | 32/32 checks |
| 7 | capture configuration | `d6s sync pull` | 3 new schema files + updated presets/permissions in git |

Wall clock for steps 1-7 in one pass: minutes, not hours; nothing restarted, no downtime
(collection creation is online in Directus).

### 6.2 Cardinality decisions made in the spec

- `certificates ↔ domains` is **M2M** (`certificates_domains`): a certificate can carry several
  names (SAN) and a domain can hold several certificates at once (issuer overlap during rotation).
  An M2O would have been simpler to build and wrong to live with.
- `certificates.provider` is **M2O → providers** (who we manage it through), while `issuer` stays a
  plain string. Issuing CAs are not account-holding vendors in this domain: they have no lifecycle,
  no credentials, no assets pointing back at them. Promoting `issuer` to `providers` would have
  added a relation without adding meaning.
- `status` reuses the generic lifecycle (`active/inactive/expired/archived`); no
  `certificate_status` was introduced.
- No `private_key` field: certificate private material belongs to the secret manager. The lint
  enforces this (and would fail the build if someone added it).

### 6.3 What was mechanical (and what that implies)

| repeated work | count for this type | now generated? |
|---|---|---|
| permission rows | 9 (3 collections × 3 actions, per policy) | yes — `apply_security.py` discovers collections from `templates/*.json` |
| projection SQL | 1 upsert function + 2 triggers + rebuild entry | yes — `build_index_sql.py` |
| preset record | 1 | yes — `apply_templates.py` |
| collection/fields/relations | 1 collection, 11 fields, 3 relations, 2 junctions | yes — `apply_templates.py` |
| verification assertions | ~32 checks | hand-written once per type (`verify_phase9.py`); could be parameterised later |
| dashboard panels | 0 | n/a — panels read `asset_index` |

Two bits of *generic* tooling had to grow, both discovered by this exercise, neither
certificate-specific:

1. `apply_templates.py` only understood `type: "tags"` for M2M. Certificate needed a real M2M to
   `domains`, so the handler became generic (`tags`/`m2m`, junction `<slug>_<related>`).
2. `apply_security.py` and `verify_phase2_5.py` carried hardcoded collection lists. They now derive
   the expected set from the template specs, so adding a type no longer means editing test/config
   files.

That is the intended outcome: the first new type exposes generic gaps, not per-type code.

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

## 11. The spec contract (Phase 10)

`templates/schema.json` (JSON Schema 2020-12) is the published contract; `scripts/validate_templates.py`
implements the same rules without third-party dependencies and adds the semantic checks JSON Schema
cannot express:

- duplicate field names, missing `name` identifier
- relation targets that do not exist (and self-relations)
- `archive.field` that is not a `select`, or whose `value` is not one of its choices
- `list.columns` / `list.widths` / `list.sort` / `index.*` referencing fields that do not exist
- `index.provider` / `index.account` pointing at something that is not an `m2o` ending in `.name`
- per-type requirements (`select` needs choices; `m2o`/`m2m`/`tags` need `to`)

`--self-test` runs 12 synthetic specs (valid one plus 11 deliberately broken ones) and fails if any
wrong verdict appears. `jsonschema` is used automatically when installed; otherwise the built-in
rules run and say so.

## 12. Generic verifier (Phase 10)

`scripts/verify_templates.py` replaces the per-phase verifier for schema work. It discovers
`templates/*.json` (no registry) and, per template, checks collection metadata, field types against
the spec, relations/junctions, the global preset, permissions for both non-admin policies, public
denial, and — only when the spec declares an `index` mapping — the projection lifecycle with a real
fixture (create → row appears → rename → status → tags → cleanup). Capabilities a spec does not
declare are skipped, not faked: a template without `expires_on` is not asked to expire.

Phase-specific scripts remain for behaviour that is not per-template (`verify_phase2_5.py` for the
core model, `verify_security.py` for policy boundaries, `verify_phase9.py` for the certificate
walkthrough). `scripts/verify_all.py` runs everything and prints one verdict per section.
