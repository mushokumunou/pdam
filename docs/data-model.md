# Data model

Target: Directus 12.6.1 on PostgreSQL 18. All primary keys are `uuid` with
`default gen_random_uuid()`. All collections carry `date_created` / `date_updated`
(Directus special `date-created` / `date-updated`) unless stated.

## 1. Naming and conventions

| Rule | Value | Reason |
|---|---|---|
| Collection names | lower `snake_case`, **plural**, stable forever (they are template slugs and API paths) | `GET /items/vps`, MCP tool names, sync file names |
| Field names | lower `snake_case`; no type prefixes, no abbreviations | agents and humans read the same names |
| Human identifier of any asset | **`name`** | one column for All-list, one addressing scheme for agents (`PATCH /items/vps/<id>` by name lookup) |
| Dates | `*_on` = `date`, `*_at` = `timestamp` | unambiguous in filters |
| Money | `price numeric(12,2)` + `currency char(3)` ISO-4217 | no floats |
| States | `status` string + `select-dropdown` choices, **not** PG enums | choices are editable without DDL; PG enums are painful to alter |
| Archive | `status = archived`, collection `archive_field = status`, `archive_value = archived`, `unarchive_value = active` | native Archive/Unarchive actions |
| Delete | `RESTRICT` for identity-bearing parents (provider), `SET NULL` for optional links | deleting a provider must not silently orphan assets |
| Value rules (IP, last four, ISO currency, lower-case FQDN) | PostgreSQL `CHECK` constraints in `schema/checks.sql`, **not** Directus field meta `validation` | field meta `validation` 500s every write on 12.6.1 (architecture §2) |

**Deviation from the brief's field list (deliberate):** the Domain template names its identifier
`name` (holding the FQDN) instead of `domain`. Reason: every asset needs one uniform identifier
column for the All list, cross-type search and agent addressing; a domain-specific column name
would special-case Domain in every generic query. The field is labelled "Domain" in the Studio.

## 2. Collection map

```
assets (group / folder)
├── vps                 → providers, accounts, tags
├── domains             → providers (registrar, dns_provider), accounts, vps (primary_server), tags
├── accounts            → providers, secret_references, tags
├── subscriptions       → providers, accounts, payment_methods, tags
└── payment_methods     → providers, secret_references, tags

reference (group)
├── providers
├── tags
└── secret_references

derived (group)
└── asset_index          read-only, trigger-maintained (ADR-006)
```

## 3. `providers` — vendors (reference entity, not an asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | required, unique |
| slug | varchar(60) | required, unique, `[a-z0-9-]+` — stable machine handle |
| category | varchar(32) | `cloud\|domain\|ai\|finance\|software\|other`, default `other` |
| website | varchar(255) | |
| notes | text | |
| status | varchar(32) | `active\|inactive\|archived`, default `active` |

Why an entity and not a string: "all assets of Hetzner" must be one indexed join, and providers
carry their own lifecycle (a provider can be retired while its historical assets stay).

## 4. `tags`

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(60) | required, unique (`production`, `finance`, …) |
| slug | varchar(60) | required, unique |
| color | varchar(9) | `#rrggbb`, optional |

Attached to every asset collection as **M2M**: junction `<collection>_tags`
(`id, <collection>_id, tags_id`) created by Directus. Cross-type tag queries use
`asset_index.tags` (ADR-006), not a polymorphic junction.

## 5. `secret_references` — pointers into an external vault (ADR-005)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | required, label only |
| secret_type | varchar(32) | `password\|totp\|api_token\|ssh_key\|recovery_codes\|card\|other` |
| provider | varchar(32) | `vaultwarden\|bitwarden\|1password\|generic` |
| reference | varchar(255) | item UUID / path / item name — **not a secret** |
| vault_url | varchar(255) | optional deep link |
| last_rotated_at | timestamp | optional |
| status | varchar(32) | `active\|rotated\|revoked` |
| notes | text | |

Hard rule: no field in any collection may store a password, TOTP seed, private key, recovery code
or full card number. Review checklist item for every future template.

## 6. `accounts` (asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | required, e.g. `Cloudflare Personal` |
| provider | uuid → providers.id | required, `RESTRICT` |
| username | varchar(120) | login handle |
| email | varchar(190) | Directus `email` interface + validation |
| url | varchar(255) | login page |
| secret_reference | uuid → secret_references.id | `SET NULL` |
| status | varchar(32) | `active\|inactive\|archived` |
| tags | M2M → tags | junction `accounts_tags` |
| notes | text | |

## 7. `vps` (asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | required, `Tokyo-01` |
| provider | uuid → providers.id | required, `RESTRICT` |
| account | uuid → accounts.id | `SET NULL` |
| hostname | varchar(255) | FQDN |
| ipv4 | varchar(15) | regex-validated dotted quad (Directus has no PG `inet` type) |
| ipv6 | varchar(45) | regex-validated |
| region | varchar(60) | `Tokyo`, `Falkenstein`… |
| os | varchar(60) | `Debian 13`, `Ubuntu 24.04`… |
| status | varchar(32) | `active\|inactive\|expired\|archived` |
| expires_on | date | optional — a VPS may have no renewal |
| tags | M2M → tags | junction `vps_tags` |
| notes | text | |

## 8. `domains` (asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(190) | required, unique (case-insensitive), the FQDN |
| registrar | uuid → providers.id | `SET NULL` |
| account | uuid → accounts.id | `SET NULL` |
| dns_provider | uuid → providers.id | `SET NULL` |
| primary_server | uuid → vps.id | optional, `SET NULL` |
| expires_on | date | |
| auto_renew | boolean | default `true` |
| status | varchar(32) | `active\|inactive\|expired\|archived` |
| tags | M2M → tags | junction `domains_tags` |
| notes | text | |

## 9. `subscriptions` (asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | `ChatGPT Plus` |
| provider | uuid → providers.id | `SET NULL` |
| account | uuid → accounts.id | `SET NULL` |
| payment_method | uuid → payment_methods.id | `SET NULL` (answers "which subscriptions use this card") |
| price | numeric(12,2) | |
| currency | varchar(3) | ISO-4217, default `USD` |
| billing_cycle | varchar(32) | `monthly\|quarterly\|yearly\|custom` |
| renewal_on | date | next charge |
| started_on | date | |
| auto_renew | boolean | default `true` |
| status | varchar(32) | `active\|inactive\|canceled\|archived` |
| tags | M2M → tags | junction `subscriptions_tags` |
| notes | text | |

## 10. `payment_methods` (asset)

| field | type | notes |
|---|---|---|
| id | uuid | PK |
| name | varchar(120) | `Wise Card` |
| provider | uuid → providers.id | `SET NULL` |
| type | varchar(32) | `card\|bank_account\|paypal\|crypto\|other` |
| last_four | varchar(4) | only card data stored |
| currency | varchar(3) | |
| expires_on | date | card expiry |
| secret_reference | uuid → secret_references.id | `SET NULL` |
| status | varchar(32) | `active\|inactive\|expired\|archived` |
| tags | M2M → tags | junction `payment_methods_tags` |
| notes | text | |

## 11. `asset_index` (derived read model — ADR-006)

| field | type | source |
|---|---|---|
| asset_id | uuid PK | source row id |
| template | varchar(60) | collection name |
| name | text | `name` |
| status | varchar(32) | `status` |
| provider_name | text | provider relation's `name` |
| account_name | text | account relation's `name` |
| expires_on | date | `expires_on` where the template has one |
| renewal_on | date | `renewal_on` for subscriptions |
| tags | text | comma-joined tag names |
| search_text | text | lower(name ⊕ provider ⊕ account ⊕ tags ⊕ hostname/ipv4) |
| updated_at | timestamptz | `now()` on write |

Maintenance: `pdam_index_sync()` trigger function per asset table (generated), plus
`pdam_index_rebuild()` for backfill. The collection is exposed read-only: fields `readonly`,
and no create/update permission for non-admin policies.

Mapping is declared once per template:

```json
{ "template": "vps", "collection": "vps", "name": "name",
  "provider": "provider.name", "account": "account.name",
  "expires": "expires_on", "renewal": null,
  "extra_search": ["hostname", "ipv4", "ipv6"] }
```

## 12. Relations summary

| from | to | cardinality | on delete |
|---|---|---|---|
| accounts.provider | providers | M2O | RESTRICT |
| accounts.secret_reference | secret_references | M2O | SET NULL |
| vps.provider | providers | M2O | RESTRICT |
| vps.account | accounts | M2O | SET NULL |
| domains.registrar | providers | M2O | SET NULL |
| domains.dns_provider | providers | M2O | SET NULL |
| domains.account | accounts | M2O | SET NULL |
| domains.primary_server | vps | M2O | SET NULL |
| subscriptions.provider | providers | M2O | SET NULL |
| subscriptions.account | accounts | M2O | SET NULL |
| subscriptions.payment_method | payment_methods | M2O | SET NULL |
| payment_methods.provider | providers | M2O | SET NULL |
| payment_methods.secret_reference | secret_references | M2O | SET NULL |
| `<asset>.tags` | tags | M2M via `<asset>_tags` | junction cascade |

Acceptance chain from the brief maps onto these FKs directly:
`providers ← accounts ← vps`, `providers ← domains.account`, and
`subscriptions.payment_method → payment_methods` ("which subscriptions use this card").

## 13. Indexes

```
providers(slug) unique, providers(name) unique, tags(slug) unique, tags(name) unique
domains(lower(name)) unique
vps(provider_id), accounts(provider_id), subscriptions(provider_id)
subscriptions(payment_method_id), domains(account_id), vps(account_id)
domains(expires_on), vps(expires_on), subscriptions(renewal_on), payment_methods(expires_on)
<each asset>(status)
asset_index(template), asset_index(status), asset_index(expires_on), asset_index(search_text)
-- when search_text grows: CREATE INDEX ... USING gin (search_text gin_trgm_ops)
```

## 14. Lifecycle fields per template

| template | status | expires_on | renewal_on | auto_renew | archived_at |
|---|---|---|---|---|---|
| vps | ✔ | ✔ (optional) | – | – | via status |
| domains | ✔ | ✔ | – | ✔ | via status |
| subscriptions | ✔ | – | ✔ | ✔ | via status |
| accounts | ✔ | – | – | – | via status |
| payment_methods | ✔ | ✔ (card expiry) | – | – | via status |

No shared "one schema for everything": a template only declares the lifecycle it actually has.
`status = archived` is the archive signal; there is no soft-delete column, so `asset_index` rows
follow the trigger and remain queryable (archived assets are still assets).

## 15. What is deliberately *not* a table

- `asset_templates` / `template_fields` — Directus collections and fields **are** the templates
  (ADR-002). A second table would diverge.
- `views` / `layouts` — presets are the list views (ADR-003).
- `asset_relations` (polymorphic graph) — every relation in the brief is typed; a generic graph
  would trade FK integrity for flexibility nobody asked for. Add the junction only if a real
  many-to-many asset-to-asset relation appears.
- `secrets` — never (ADR-005).


## 16. Form layout (implemented)

Fields are ordered by `meta.sort` in the spec, technical fields (`id`) hidden, timestamps shown at
the end, and displays set per type: `select` -> labels, `date`/`timestamp` -> datetime,
`m2o` -> related-values with `{{name}}`, `m2m tags` -> list-m2m.

**Field groups are NOT used.** On Directus 12.6.1, alias fields with `special: ["group"]` are
omitted from the bulk `GET /fields` response (single-field `GET /fields/<c>/<f>` returns them,
upstream issue directus/directus#28379), so Studio cannot render the group *or its children* —
a grouped form collapses to only the ungrouped fields. The applier therefore creates no group
fields and explicitly clears `meta.group`. Forms stay flat but remain template-specific.

Field order per template (create/edit drawer):

| template | order |
|---|---|
| vps | name, provider, account, hostname, ipv4, ipv6, region, os, status, expires_on, notes, tags |
| domains | name, registrar, account, dns_provider, primary_server, expires_on, auto_renew, status, notes, tags |
| accounts | name, provider, username, email, url, secret_reference, status, notes, tags |
| subscriptions | name, provider, account, payment_method, price, currency, billing_cycle, renewal_on, started_on, auto_renew, status, notes, tags |
| payment_methods | name, provider, type, last_four, currency, expires_on, status, secret_reference, notes, tags |
| secret_references | name, secret_type, provider, reference, vault_url, last_rotated_at, status, notes |
| providers | name, slug, category, website, status, notes |

## 17. Delete behaviour (verified)

| deleted row | effect | observed |
|---|---|---|
| provider referenced by accounts/VPS | **blocked** — `RESTRICT` FK violation; Directus returns HTTP **500** (not 400) | row and assets survive |
| tag in use | junction rows cascade, **assets untouched**, `asset_index.tags` recomputed | asset still exists |
| account referenced by vps | `vps.account = NULL`, VPS survives | verified |
| payment_method referenced by subscription | `subscriptions.payment_method = NULL` | verified |
| secret_reference referenced by account | `accounts.secret_reference = NULL` | verified |
| asset (vps/domains/...) | its `asset_index` row is deleted by trigger (no orphan) | verified |

Nothing cascades into asset deletion: there is no `ON DELETE CASCADE` on any asset->parent FK.

## 18. asset_index identity and rebuilding

- Identity: `UNIQUE (template, asset_id)` plus PK on `asset_id`; reverse lookup is
  `GET /items/asset_index?filter[asset_id][_eq]=<uuid>`.
- Rebuild: `SELECT pdam_index_rebuild();` — re-derives every row from the typed tables.
  Safe to run repeatedly (verified: 10 -> 10 rows, 0 duplicates), and after
  `TRUNCATE asset_index` it restores the full projection (verified).
- Disposable: dropping every row loses nothing but the All list until the next rebuild.

## 19. Dashboard adds no tables

Phase 6 introduced no collection: panels query `asset_index` (cross-type) and `subscriptions`
(billing detail) directly. Any future statistic must stay derived; do not add
`asset_statistics`-style tables.
