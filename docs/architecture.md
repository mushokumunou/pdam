# Architecture

Status: **Phase 0 — decision draft.** Every claim about Directus behaviour below was verified
against the live instance `pdam.8w8.xyz` (Directus **12.6.1**, PostgreSQL 18), not assumed from
documentation alone. Verification notes are marked **[verified]**.

## 1. Problem statement

A personal digital asset manager must hold wildly different asset kinds (VPS, domains, accounts,
subscriptions, payment methods, later certificates/licenses/API keys) where each kind needs

1. its own field schema and field types,
2. its own list columns, filters, default sort,
3. its own create/edit form,
4. its own entry in navigation,
5. real relations to shared entities (providers, accounts, tags, payment methods),
6. one cross-type list ("All assets"), one search entry point and one dashboard,
7. a machine-readable surface for agents (REST + MCP), independent of the UI.

The naive answers all fail: a single table with `data JSONB` loses types, validation, sorting and
per-type forms; a pure EAV table destroys query planning and relations; hard-coding each type in a
custom SPA duplicates Directus (auth, CRUD, validation, permissions, dashboards) and multiplies
maintenance.

## 2. Verified platform facts

These determine the design more than any preference.

| Fact | Consequence |
|---|---|
| Directus 12.6.1, Node 26 in the official image **[verified]** | Pin the image tag; the CLI requires an exact version match. |
| `@directus/cli@12` (`d6s`) *Environment Sync* moves **schema + configuration** (roles, policies, permissions, flows, dashboards, panels, settings, presets/bookmarks, translations) through git **[verified]** | Templates and their list views are versionable without custom tooling. |
| Sync files: `directus/default/schema/<collection>_<hash>.json`, `directus/default/data/<resource>_<hash>.json`, plus `metadata.json` pinning `directus` version + DB vendor **[verified]** | Human-reviewable diffs; collection files are self-contained definitions. |
| A hand-written collection JSON pushed with `d6s sync push` created the collection, its 7 fields and interfaces, and the server answered *"schema hash verified"* **[verified]** | The repo can be the source of truth; no click-through setup. |
| Presets (`directus_presets`) hold `layout`, `layout_query.fields/sort`, `layout_options.tabular.widths`, `filters`, scoped to `user` or `role` (null = everyone) **[verified]** | Per-template list columns/filters/sort are configuration, synced to git. |
| Collections carry `icon`, `note`, `sort`, `group`, `hidden`, `display_template`, `archive_field`, `archive_value`, `unarchive_value`, `translations` **[verified]** | Navigation metadata and lifecycle "archive" behaviour are native, no custom table. |
| A group collection (`POST /collections` with `schema: null`) plus `meta.group` on children produces a collapsible nav group; members are read from `GET /collections` and filtered on `meta.group` **[verified]** — note `/collections` ignores query filters (it always returns every collection), so the filter is client-side | The Template registry already exists: it is the `assets` group. |
| Database **views cannot be registered as Directus collections** — registering metadata and `directus_fields` rows still yields `403 … collection "v_probe" or it does not exist`, because introspection only tracks base tables **[verified]** | "All assets" cannot be a SQL view. A read model must be a real table (or a custom endpoint). |
| Built-in **MCP server** (`/mcp`, JSON-RPC, `mcp_enabled` setting, tool mode default/registry) initialises and inherits the calling user's permissions **[verified]** | Agents get a first-class interface with zero custom code. |
| `/mcp` requires the project setting `mcp_enabled = true`; `MCP_ENABLED=false` disables it instance-wide **[verified]** | Keep it enabled, gate by policy. |
| Field meta `validation` (e.g. `{"_regex": "..."}`) makes **every** item create/update fail with `500 Maximum call stack size exceeded` on 12.6.1 **[verified]** — an internal lodash merge overflow, not a payload problem | Do not put regex rules in field metadata. Enforce with PostgreSQL `CHECK` constraints (`schema/checks.sql`) and keep the interface hint only. |
| Licence is MSCL-1.0-GPL (internal use permitted; no competing hosted offering; converts to GPL-3.0 after 4 years); licence-key functionality must not be bypassed **[verified]** | Self-hosting here is compliant; do not strip licence checks. |

## 3. Component responsibilities

```
              ┌──────────────────────────────────────────┐
  Human ──────▶  Directus Data Studio (native UI)       │
              │   • collection pages = template pages   │
              │   • presets      = list columns/filters │
              │   • Insights     = dashboard            │
  Agent ──────▶  REST/GraphQL  +  /mcp (built-in)        │
              └───────────────┬──────────────────────────┘
                              │ schema + data + permissions
              ┌───────────────▼──────────────────────────┐
              │ Directus (API, auth, validation, flows)  │
              └───────────────┬──────────────────────────┘
                              │ SQL
              ┌───────────────▼──────────────────────────┐
              │ PostgreSQL: typed tables, FKs, indexes,  │
              │ read model (asset_index), generated enums│
              └──────────────────────────────────────────┘

  d6s (git) ── schema + presets + dashboards ──▶ Directus (push) ──▶ PostgreSQL DDL
  Vaultwarden / 1Password ── the secrets themselves; we store references only
```

- **Directus** owns CRUD, auth, permissions, validation, relations, API, MCP, dashboards.
- **PostgreSQL** owns types, constraints, indexes, referential integrity, and the read model.
- **`d6s` + git** own the shape of the system: collections, fields, relations, presets, dashboards.
- **A custom frontend is not part of v1.** The template-driven UX is assembled from native pieces
  (see ADR-003); only if a measurable gap remains do we add one thin extension.
- **The secret manager** owns passwords, TOTP seeds, private keys, recovery codes. This system
  never stores them (ADR-005).

## 4. ADRs

### ADR-001 — Asset storage: typed collections (one collection per asset type)

**Decision.** Every Asset Type is its own PostgreSQL table and Directus collection
(`vps`, `domains`, `accounts`, `subscriptions`, `payment_methods`, …). Cross-type concerns are
carried by shared tables (`providers`, `tags`, `secret_references`) through real foreign keys.
A derived, trigger-maintained read model `asset_index` provides the cross-type list and search.

**Why.** Requirements 38 and 33 of the brief ask for both dynamism and strong typing. Typed
collections deliver the second natively and the first through configuration: a table per type is
*how* a template gets its own field types, validation, indexes, form, list columns and API
endpoint. Nothing else in Directus gives a per-type form and a per-type list with zero code.

**Alternatives rejected**

| Option | Why rejected |
|---|---|
| Single `assets` table + `data JSONB` | Filters/sorts push into JSON path expressions, no per-field validation or types, Studio cannot render a per-type form, agents receive opaque payloads, GIN-only indexing. |
| EAV (`asset_field_values(asset_id, field_id, value)`) | Every read becomes a self-join + pivot, types collapse to text, indexes and constraints are impossible, and it is the classic unbounded-cost trap named in the brief. |
| Base `assets` + typed extension tables | Unified list comes free, but per-type pages must display `asset.name`/`asset.status` through a relation (display works, **sort/filter by them does not**), and creating an asset becomes a two-record write (asset + detail) — a two-step form or a Flow. Pays a permanent UX cost to avoid a read model. |
| DB views as collections | Not supported by Directus 12 **[verified]**; a view cannot even be queried through the API. |

**Cost accepted.** Two things must be kept in sync when a template is added: the collection
definition and the read-model mapping (one row + one trigger). Both are generated from the same
template spec, so the template count does not increase hand-written code.

### ADR-002 — Template → Directus mapping

**Decision.** A *Template* is not a table. It is the tuple:

```
template = collection            (slug = table name, stable forever)
         + collection.meta       (name, icon, note, sort, group, display_template, archive_*)
         + fields[]              (type, interface, options, validation, conditions, sort, group)
         + relations[]           (FKs + M2M junctions)
         + preset                (list columns, widths, default sort, default filters)
```

The **registry of templates is the `assets` collection group**; membership is `meta.group`.
Nothing else is stored, so nothing can drift: the metadata an agent reads from
`GET /collections` and `GET /fields/<collection>` *is* the template.

**Why.** Any bespoke `asset_templates`/`template_fields` table would duplicate Directus' own
metadata and immediately become a second source of truth. Directus already exposes exactly the
five things a template needs, machine-readably, and `d6s` versions all of them.

**Consequence.** "Template Builder UI" later = a form writing these same native records, not a new
storage model.

### ADR-003 — Template-specific pages, lists and navigation

**Decision.** Native collection pages, no custom frontend in v1:

| Requirement | Native mechanism |
|---|---|
| `[All] [VPS] [Domain] …` navigation | `assets` group in the sidebar (icon, sort, collapse state) |
| "VPS page shows only VPS" | collection page `vps` — different collections by construction |
| "VPS list shows Name/Provider/IPv4/Region/OS/Status" | global preset `bookmark: "VPS"` with `layout_query.tabular.fields` + widths |
| "Domain list shows Domain/Registrar/Expiration/Status" | its own preset |
| Filters offered per page | preset `filters` + field interfaces (`select-dropdown` choices) |
| `+ Add VPS` shows the VPS form | native create drawer, fields ordered by `meta.sort`, grouped by field groups |
| Archived instead of deleted | `archive_field = status`, `archive_value = archived` → native Archive action |

**Why.** Every one of these is a native feature that already survives a `d6s` round-trip
**[verified for presets]**. Building a Vue module would reimplement forms, validation, permissions
and relation pickers for no product gain.

**Escape hatch.** If the tab strip itself is later judged necessary, it is a *navigation* module
(reads `GET /collections?filter[group][_eq]=assets` and links to native pages) — it does not
re-implement CRUD.

### ADR-004 — Extension path: adding an Asset Type

**Decision.** Adding a type touches only data/config, never frontend code:

1. add `templates/<slug>.json` (fields, list columns, filters, nav metadata),
2. run `scripts/generate.py` → writes `directus/default/schema/<slug>.json`, merges the preset
   into `directus/default/data/directus-presets*.json`, appends the read-model mapping,
3. `d6s sync diff --to <env>` → review → `d6s sync push --to <env>`,
4. run `scripts/asset_index.sql` (idempotent) to add the trigger + backfill.

Result: new sidebar entry, new list with its own columns, new form, new REST/GraphQL endpoints,
new MCP tools, new rows in All/search/dashboard. **Verification for Scenario 3/4 = a Certificate
template added by steps 1–4 only.**

### ADR-005 — Secret references, not secrets

**Decision.** `secret_references` holds `name`, `provider` (`vaultwarden|bitwarden|1password|other`),
`reference` (item UUID / path), `secret_type` (`password|totp|api_token|ssh_key|recovery_codes|card`),
`last_rotated_at`, `status`. `accounts` and `payment_methods` point at it via FK.
No field anywhere stores a password, TOTP seed, private key or full card number.

**Why.** The brief is explicit that this is not a password manager. Keeping the boundary at
"metadata + pointer" means backup/export of this system is not a credential leak, and the secret
manager can be swapped without data migration. `payment_methods.last_four` is the only card data
kept, by design.

### ADR-006 — Unified "All assets" and cross-type search: `asset_index` read model

**Decision (implemented in Phase 5).** One real table `asset_index`, maintained by PostgreSQL
triggers on every asset table and on every tags junction, exposed as a read-only Directus
collection:

```
asset_index(
  asset_id uuid PK,                 -- one row per typed asset
  template varchar(60) NOT NULL,    -- source collection slug
  name text, status varchar(32),
  provider_name text, account_name text,   -- denormalised for cross-type search
  expires_on date, renewal_on date, tags text,
  search_text text,                 -- lower(name ⊕ provider ⊕ account ⊕ tags ⊕ type-specific)
  updated_at timestamptz
)
UNIQUE (template, asset_id)         -- explicit identity contract
```

Maintenance: `pdam_idx_<template>(uuid)` recomputes one row (joined tags, provider, account),
called by `AFTER INSERT/UPDATE/DELETE` triggers on the typed table and by triggers on
`<collection>_tags`. `pdam_index_rebuild()` truncates nothing — it deletes and re-derives every row
from the typed collections, so the projection is disposable.

**Never a source of truth.** Nothing writes to it except the trigger functions: Directus fields are
`readonly`, the collection has no create/edit workflow in the product, and there is no code path
that posts to `/items/asset_index`. If the table is empty or dropped, `pdam_index_rebuild()`
restores it exactly.

**Why.** Directus cannot union collections and cannot expose views (ADR-001), but a single query
over `asset_index` gives: the All list with native sort/filter/search, dashboard panels without
N queries, and a one-call agent endpoint (`GET /items/asset_index?search=hetzner`) that is
*relation-aware* because provider/account names are denormalised into it.

**Verified in Phase 5** (all 30 checks green, see `scripts/verify_phase2_5.py`): create → row
appears; rename → name updates; provider change → provider_name updates; tag add/remove → tags
updates; archive → row stays with `status=archived`; delete → row disappears; rebuild twice keeps
the same row count with zero duplicates; `TRUNCATE asset_index` followed by rebuild restores all
rows; cross-type search by provider name returns rows from several templates.

**Cost.** One denormalised table and generated triggers. It is derived data: writes go to the typed
collections, never here.

**Rejected alternative.** Read-only SQL view — impossible. Custom endpoint merging N collections —
works for agents, but leaves the Studio without an All list and pushes aggregation into code.

## 5. Scale validation (200 domains / 300 accounts / 100 subscriptions / 100 servers / 50 providers)

| Operation | Mechanism | Verdict |
|---|---|---|
| List per type, sort, filter | single-table query + indexes on `status`, `expires_on`, FKs | fine |
| All assets | `asset_index` single-table scan, `search_text` btree/trigram if needed | fine at ~1k rows |
| Relation queries ("assets of Hetzner") | FK index + Directus deep filter `filter[provider][slug][_eq]=hetzner` | fine |
| Tag queries across types | 5 junction tables; `asset_index.tags` for the cross-type case | fine |
| Dashboard | 4–6 aggregate queries over ≤1k rows | fine |
| Schema migration | `d6s sync diff/push`, additive by default | fine |
| Backup/restore | `pg_dump` + `directus/` in git | fine |
| Agent operations | REST per type + `asset_index` for cross-type, MCP | fine |

Nothing in the design needs revisiting before ~10⁵ rows. The first thing to feel pressure would be
`search_text` (switch to `pg_trgm` + GIN, a one-line index change).

## 6. Risks and over-engineering flags in the brief

1. **`asset_index` is the only derived structure.** If the metric that matters is "no triggers in
   my database", drop it and let the All view be a custom module querying N collections. Everything
   else survives; this is a deliberate, reversible trade.
2. **"Template Builder UI" is deferred on purpose.** A CRUD form over native metadata is cheap to
   add later; a template engine built now would be the "second Directus" the brief warns against.
3. **Per-template list columns are presets, not a `view_definition` column.** Storing them in the
   template spec would duplicate Directus and require a custom renderer.
4. **`tags` M2M produces one junction per asset type.** A shared polymorphic `asset_tags` table
   would allow a single cross-type join but forfeits FK integrity; `asset_index.tags` covers the
   cross-type read path instead.
5. **Do not generalise `status` choices across types** — a URL bookmark has no lifecycle; forcing
   `expires_on`/`renewal_on` into every table would produce the null-column sprawl the brief forbids.
6. **MCP write access is a real risk.** Keep `mcp_allow_deletes = false`, give agents a dedicated
   policy/token rather than the admin token once a second user exists.
7. **`d6s` pins exact versions.** Upgrading Directus is a two-step operation (bump image, re-pull
   sync files); automate it before adding many environments.

## 7. Non-goals (v1)

Password storage, TOTP, file vaults, workflow engine, form builder, permission DSL, custom query
language, multi-tenant, drag-and-drop template designer, bespoke SPA.
