# Implementation plan

Each phase ends with: it runs, it is verified, schema/relations/API checked, docs updated, commit.
"No phase is done because the code looks right."

Status legend: **[done]** verified on `pdam.8w8.xyz` · **[next]** · **[ ]** planned

---

## Phase 0 — Architecture (this document set) **[done]**

- Verified Directus 12.6.1 capabilities against a live instance (see `architecture.md` §2).
- Compared single table / EAV / base+extension / typed collections / DB views.
- Chose typed collections + `asset_index` read model; wrote ADR-001…006.
- Wrote `architecture.md`, `data-model.md`, `template-system.md`, `agent-api.md`, this plan.

**Verified during Phase 0** (not assumed): sync round-trip (`d6s pull → edit → push` recreated a
7-field collection with "schema hash verified"), presets captured in sync files, nav group
enumeration, MCP `initialize`/`tools/list`, DB views rejected by Directus.

---

## Phase 1 — Infrastructure **[done]**

| Item | State |
|---|---|
| Container `pdam` (`directus/directus:12.6.1`) on `proxy_net` | running, `restart: unless-stopped` |
| Shared PostgreSQL 18 (`postgres` container, db `pdam`, user `admin`) | reused, not a new instance |
| TLS | Caddy `pdam.8w8.xyz` → `pdam:8055`, Let's Encrypt DNS-01 (issuer verified in logs) |
| DNS | AAAA `pdam.8w8.xyz` → host IPv6, Cloudflare proxied |
| Admin | `admin@8w8.xyz` / password from `.env` |
| Static admin token for CLI/MCP | issued, stored outside git |
| MCP | enabled (`settings.mcp_enabled = true`), tools listed |
| Volumes | `/opt/data/pdam/uploads`, `/opt/data/pdam/extensions` |

Verification used: `POST /auth/login` → 200 + token; `GET /server/ping` → `pong` from inside the
container and 200 through Cloudflare.

---

## Phase 2 — Base data model **[done — except the index DDL step]**

Shipped: `templates/{providers,tags,secret_references,accounts}.json`, `scripts/apply_templates.py`
(idempotent spec → API: collections, fields, m2o + m2m relations, global presets),
`scripts/build_index_sql.py`, `directus/` sync baseline.

Still pending: applying `schema/asset_index.sql` and `schema/checks.sql` on the database
(functions, triggers, CHECK constraints). The files are written and reviewed; execution needs a
host-level write that was blocked pending approval.

1. `templates/providers.json`, `tags.json`, `secret_references.json`, `accounts.json` (spec files).
2. `scripts/generate.py` — spec → Directus collection JSON (`directus/default/schema/*.json`),
   merge into `directus/default/data/directus-presets*.json`, update `metadata.json`.
3. `d6s sync diff --to pdam` → review → `d6s sync push --to pdam`.
4. `scripts/asset_index.sql` — table + `pdam_index_sync()` + triggers + `pdam_index_rebuild()`.
5. Register `asset_index` as a read-only collection (readonly fields, no write permission).

**Verify:** `GET /fields/accounts`, `GET /relations?filter[collection][_eq]=accounts]`,
`POST /items/providers` then `POST /items/accounts` with that provider, `GET /items/asset_index`
returns the account row with `provider_name`, `d6s sync pull` produces no diff (idempotent).

---

## Phase 3 — First templates **[done]**

`vps`, `domains`, `subscriptions`, `payment_methods` generated from spec files; tags M2M junctions
(`<collection>_tags`) with two relations each; one global preset per template with its own
columns/widths/sort; `asset_index` registered as a read-only collection.

**Verified:** `/items/vps` and `/items/domains` return disjoint sets; each preset carries its own
column list; deep relational filters work
(`/items/vps?filter[provider][slug][_eq]=hetzner` → 2 rows,
`/items/domains?filter[account][name][_eq]=Cloudflare Personal` → 3 rows);
M2M tags resolve (`tags.tags_id.name` → `["production","infra"]`);
`?filter[expires_on][_lte]=…` returns the right domain.

---

## Phase 4 — Template-driven views **[done]**

1. Nav group `assets` holds all five types with icons and `sort` **[done]**.
2. `[All]` = `asset_index` page with the global "All Assets" preset (name, template, provider,
   account, status, expires, renewal, tags; sort `-updated_at`) **[done]**.
3. Each type page = native collection page + its preset **[done]**.
4. Form drawers verified template-specific (VPS: name/provider/account/hostname/ipv4/ipv6/region/
   os/status/expires/notes/tags; Domain: registrar/dns_provider/auto_renew/…; no cross-type fields).
   Technical fields hidden, `status` as labels, dates as datetime, relations by name **[done]**.
   Field *group sections* were removed: group alias fields are invisible to `/fields` on 12.6.1
   (directus/directus#28379) and collapse the form — documented deviation, flat forms instead.
5. Dashboard panels — moved to Phase 6 (out of scope this round).

**Verify:** switch types → columns change, `/items/<type>` unchanged, Add form differs per type,
All list shows all five, counts match `meta.filter_count` per collection.

*If the tab strip itself is requested later*, it is one small module reading
`GET /collections?filter[group][_eq]=assets` that links to native pages — no CRUD reimplementation.

---

## Phase 5 — asset_index projection **[done]**

`schema/asset_index.sql` (generated by `scripts/build_index_sql.py`): table, `UNIQUE (template,
asset_id)`, indexes, one `pdam_idx_<template>()` upsert per asset type, `AFTER` triggers on each
typed table and each tags junction, and `pdam_index_rebuild()`.

**Verified:** create/rename/re-provider/re-tag/archive/delete all reflected in the projection;
rebuild twice = 10 rows, 0 duplicates; `TRUNCATE` + rebuild = full restore; cross-type search by
provider name spans templates; deleting an asset leaves no orphan row.

**Also verified:** the delete matrix (RESTRICT on provider, SET NULL for account / payment method /
secret reference, junction cascade for tags) — see `data-model.md` §17.

---

## Phase 6 — Dashboard **[done]**

Native Insights dashboard `Personal Digital Assets` (`scripts/build_dashboard.py`, idempotent):
Total Assets (metric), Assets by Type (metric-list grouped by `template`), Expiring Soon 30 days
(list over `asset_index.expires_on` with `$NOW` relative filters), Upcoming Renewals (list over
`subscriptions.renewal_on`, so price/currency/billing_cycle stay in their own table), Status
Distribution (pie-chart over `asset_index.status`), Not Active (list), Recently Updated (list).

Verified in-browser with live data; dashboard and all 10 panels are captured by `d6s sync` as
configuration. Navigation result: native list rows open an inline item drawer for the panel's own
collection (`subscriptions` verified); cross-type projection rows are intentionally not clickable,
so dashboard → source asset needs a future navigation extension.

---

## Phase 7 — Secret boundary **[done]**

Schema audit: `secret_references` holds pointer metadata only (name, secret_type, provider,
reference, vault_url, last_rotated_at, status, notes) — no material. `scripts/lint_secret_schema.py`
scans specs and the live schema for secret-material field names (with `--self-test`), and is part of
the verification pass. No secret-manager integration in this phase.

## Phase 8A — Agent access policy **[done]**

`scripts/apply_security.py` creates policy `PDAM Agent` (`app_access=false`, 43 permission rows),
role, user and static token (env/`0600` file, never git). Also creates `PDAM User` (Studio-capable,
no hard delete) so humans stop using the admin account for daily work. Verified by
`scripts/verify_security.py` — 72/72, three identities, allow/deny/public/MCP sections.

---

## Phase 8 — Agent surface

1. Agent policy + dedicated static token (not admin), `mcp_allow_deletes = false`.
2. `/mcp` smoke test with the 10 queries from `agent-api.md`.
3. Optional: thin CLI/MCP wrapper + Hermes skill, generated from `GET /collections` — no hard-coded
   type list.

**Verify:** every query in `agent-api.md` returns the expected shape; a create→archive round-trip
through MCP only.

---

## Phase 9 — Extensibility proof (Certificate) **[done]**

Executed end to end: `templates/certificate.json` → `apply_templates.py` → `build_index_sql.py` →
`psql -f schema/asset_index.sql` → `apply_security.py` → `d6s sync pull`.

Measured result:

| category | changed by |
|---|---|
| template declaration | 1 new spec (`templates/certificate.json`) |
| generated Directus metadata | 1 collection + 2 junctions + 3 relations + 1 preset (generated) |
| projection SQL | regenerated file, +1 function / +2 triggers (generated) |
| permission config | 9 rows per policy, generated from specs |
| tests | new `scripts/verify_phase9.py` (32 checks); `verify_phase2_5.py` de-hardcoded |
| docs | 6 files |
| **generic application code** | **0** — no router, handler, endpoint, MCP tool, dashboard or registry edit |

Verification: `verify_phase9.py` 32/32, `verify_security.py` 72/72 (no regression),
`verify_phase2_5.py` 30/30 after making its type list spec-driven, browser checks as PDAM User,
dashboard absorbed the new type with zero changes.

---

## Continuous rules

- `d6s sync pull` after any Studio edit; `d6s sync diff` before any push; never push blind.
- Use `d6s sync diff --mode add` to prove the target already has the repo's configuration. Plain `merge` always reports the 10 dashboard panels as updates because the sync format nulls server-managed `date_created`/`user_created`, which the API returns — a cosmetic no-op, not drift.
- Backups: `pg_dump` (content) + `directus/` (shape). Both, every time.
- Directus upgrades: bump image tag → boot → `d6s sync pull` (new baseline) → commit.
- Every new template: check it does not add a secret-bearing field name (Phase 7 lint).

## Phase 10 — Tooling hardening + v1 release gate **[done]**

| item | implementation | verification |
|---|---|---|
| spec contract | `templates/schema.json` + `scripts/validate_templates.py` (structural + semantic, `--self-test`) | 12/12 synthetic verdicts, 9 real specs valid |
| generic verifier | `scripts/verify_templates.py` (discovery-based, capability-driven) | 107/107 across all 9 templates |
| mutation guard | `scripts/agent_guard.py` (function + thin `/mcp` proxy) | 17/17, including "rejected call changed no rows" |
| unified gate | `scripts/verify_all.py` | prints PASS/FAIL per section |
| backup/restore | `docs/backup-restore.md`, real restore into `pdam_restore_test` | all table counts equal, projection rebuild 0→10 |
| release | README status, `v1.0.0` tag | repo clean, `d6s --mode add` parity |

**v1 definition:** architecture frozen, ready for real data. New capability work only from real
usage friction, not from design speculation.
