# Agent API

Two equivalent surfaces, one data model. Neither requires the UI.

- **REST** — `https://pdam.8w8.xyz/items/<collection>`, `Authorization: Bearer <token>`
- **MCP** — `https://pdam.8w8.xyz/mcp` (JSON-RPC / streamable HTTP), same bearer token, tools scoped
  by that user's permissions (verified: `initialize` + `tools/list` return 12 tools on 12.6.1)

Agents must discover the model, not assume it. Everything below is discoverable at runtime.

## 1. Discovery

```bash
# which asset templates exist (registry = the "assets" collection group)
# NOTE: /collections ignores ?filter=… (it returns every collection), so filter client-side
# on meta.group. 36 rows on a fresh instance — cheap.
curl -s "$BASE/collections?fields=collection,meta.group,meta.icon,meta.note,meta.sort" \
  -H "Authorization: Bearer $TOKEN" \
  | jq '[.data[] | select(.meta.group == "assets") | .collection]'

# field schema of one template (types, required, choices, relations)
curl -s "$BASE/fields/vps" -H "Authorization: Bearer $TOKEN"

# relations of one template
curl -s "$BASE/relations?filter[collection][_eq]=vps" -H "Authorization: Bearer $TOKEN"

# list views (columns/filters per template) — global presets
curl -s "$BASE/presets?filter[user][_null]=true&filter[role][_null]=true" -H "Authorization: Bearer $TOKEN"
```

MCP equivalents: `schema` (collections + fields summary), `collections` (read),
`fields` (read), `relations` (read).

Stable contract: **collection name = template slug**, `name` = asset identifier, `status` =
lifecycle state, `*_on` dates, `*_at` timestamps. These do not change without a migration note.

## 2. Common reads

```bash
# all VPS
GET /items/vps?fields=id,name,status,region,ipv4,provider.name&sort=name

# domains expiring within 30 days
GET /items/domains?filter[status][_eq]=active&filter[expires_on][_lte]=$DATE_30&sort=expires_on

# everything belonging to Hetzner (deep relational filter)
GET /items/vps?filter[provider][slug][_eq]=hetzner
GET /items/vps?filter[account][provider][slug][_eq]=hetzner

# which subscriptions use a given card
GET /items/subscriptions?filter[payment_method][_eq]=<uuid>&fields=name,price,currency,renewal_on

# assets carrying a tag
GET /items/domains?filter[tags][tags_id][name][_eq]=production

# relations for an account → its assets
GET /items/vps?filter[account][_eq]=<uuid>&fields=name,status
GET /items/domains?filter[account][_eq]=<uuid>&fields=name,status

# cross-type, relation-aware search (asset_index denormalises provider/account/tags)
GET /items/asset_index?filter[search_text][_contains]=hetzner&sort=-updated_at

# everything expiring soon across types
GET /items/asset_index?filter[expires_on][_lte]=$DATE_30&sort=expires_on
```

`asset_index` exists precisely so an agent does not have to fan out over N collections for
"find X anywhere" and "what expires soon" (ADR-006).

## 3. Writes

```bash
# create a VPS (relations by uuid; the template decides required fields)
POST /items/vps
{ "name":"Tokyo-01", "provider":"<providers.uuid>", "account":"<accounts.uuid>",
  "hostname":"tokyo-01.example.com", "ipv4":"1.2.3.4", "region":"Tokyo",
  "os":"Debian 13", "status":"active", "expires_on":"2027-03-01" }

# update
PATCH /items/vps/<uuid>  { "status":"expired" }

# archive (never DELETE for lifecycle changes)
PATCH /items/vps/<uuid>  { "status":"archived" }

# tag an asset (M2M junction)
POST /items/vps_tags  { "vps_id":"<uuid>", "tags_id":"<tag uuid>" }

# create the reference entities first when missing
POST /items/providers  { "name":"Hetzner", "slug":"hetzner", "category":"cloud" }
POST /items/accounts   { "name":"Hetzner Personal", "provider":"<uuid>", "status":"active" }
```

Rules an agent must follow:

1. **Resolve before create.** Look up `providers`/`accounts`/`tags` by `slug` or `name`; never
   inline a new provider string (relations are the point of the model).
2. **Archive, don't delete.** `status = archived` keeps history, indexes and relations intact.
   Deletion is an admin action with `mcp_allow_deletes = false` by default.
3. **Use the template.** A field that is not in `GET /fields/<collection>` does not exist; do not
   invent properties, the API will reject them.
4. **Secrets never enter this API.** If a workflow needs a password, it writes a
   `secret_references` row (`provider`, `reference`, `secret_type`) and nothing else.

## 4. MCP usage

```json
{"mcpServers":{"pdam":{"type":"http","url":"https://pdam.8w8.xyz/mcp",
  "headers":{"Authorization":"Bearer <admin-or-agent-static-token>"}}}}
```

Available tools (12.6.1): `items`, `schema`, `collections`, `fields`, `relations`, `files`,
`folders`, `assets`, `flows`, `operations`, `trigger-flow`, `system-prompt`.

Typical session:

1. `schema` → collection list + fields, or `collections` read with the `assets` group filter,
2. `items` with `action: "read"`, `collection: "vps"`, `filter`/`search`,
3. `items` with `action: "create"/"update"` for writes.

The tool mode is switchable in Settings → AI → Model Context Protocol (`default` per-tool vs
`registry` search-then-execute) to protect a small context window.

## 5. Boundaries and error behaviour

| Situation | Expected |
|---|---|
| Unknown collection | `403 FORBIDDEN … collection "x" or it does not exist` |
| Unknown field in payload | `400 INVALID_PAYLOAD` |
| Missing required field | `400 FAILED_VALIDATION` with `field` extension |
| FK to non-existent uuid | `400` foreign-key violation |
| MCP disabled / token not allowed | `403 MCP must be enabled` |
| Too many rows | use `limit`/`page`; `meta.filter_count` gives the total |

Rate limiting is enabled instance-wide (`RATE_LIMITER_*`); batch reads, don't loop per row.

## 6. Not exposed on purpose

No custom query language, no bespoke CLI grammar, no write-only "agent tables". The CLI/MCP/skill
layer, when it arrives, is a thin wrapper over these REST calls so that human and agent capability
cannot diverge.

## 7. Verified query set (Phase 2-5 acceptance run)

Executed against `pdam.8w8.xyz` on Directus 12.6.1; `scripts/verify_phase2_5.py` re-runs all of
these (30/30 green).

| purpose | call | observed |
|---|---|---|
| discover asset types | `GET /collections?fields=collection,meta.group` then keep `meta.group == "assets"` | `accounts, domains, payment_methods, subscriptions, vps` |
| discover a template's fields | `GET /fields/vps` | 15 fields, m2o/m2m specials included |
| discover relations | `GET /relations?limit=500` (filter client-side) | 23 app relations |
| all VPS with provider | `GET /items/vps?fields=name,status,provider.name` | 2 rows |
| VPS by provider | `GET /items/vps?filter[provider][slug][_eq]=hetzner` | 2 rows |
| domains by account | `GET /items/domains?filter[account][name][_eq]=Cloudflare Personal` | 3 rows |
| subscriptions by card | `GET /items/subscriptions?filter[payment_method][_eq]=<id>` | 1 row |
| assets by tag | `GET /items/vps?filter[tags][tags_id][name][_eq]=production` | 1 row |
| all assets (one table) | `GET /items/asset_index?fields=template,name,provider_name,status` | 10 rows over 5 types |
| cross-type search | `GET /items/asset_index?filter[search_text][_contains]=cloudflare` | domains + account |
| reverse lookup | `GET /items/asset_index?filter[asset_id][_eq]=<uuid>` | 1 row |
| create / update / archive | `POST /items/vps`, `PATCH /items/vps/<id>` `{status:"archived"}` | projection follows every step |
| delete asset | `DELETE /items/vps/<id>` | 204, projection row removed |

Error semantics observed:

- Deleting a provider that still has accounts/VPS returns **HTTP 500** with a foreign-key violation
  message (Directus does not map `RESTRICT` to 409). The delete is still correctly refused; agents
  should treat 500-on-delete as "still referenced" and re-check.
- Archiving is `PATCH {status:"archived"}`; the row remains in `asset_index` with that status, which
  is intentional: archived assets are still assets, they are not deleted.
- `DELETE` on an unknown id returns 204 and changes nothing.

## 8. Dashboards (configuration, not data)

The Insights dashboard is stored in `directus_dashboards` / `directus_panels` and synced by `d6s`,
so an agent can read it without scraping the UI:

```bash
GET /dashboards?fields=id,name,panels
GET /panels?fields=name,type,position_x,position_y,width,height,options
```

Panel `options.collection` / `options.filter` describe exactly which query feeds each panel — same
filters an agent can replay against `/items/...`. The dashboard creates no tables and owns no data;
`asset_index` remains the only cross-type read path.

## 9. Agent credentials and limits (Phase 8A)

Agents authenticate with a **dedicated static token** (`PDAM_AGENT_TOKEN` in the runtime
environment, or a `0600` file locally). Never the administrator token.

Allowed: read every business collection plus `asset_index`; create/update assets and references;
add/remove tags (junction rows); archive via `PATCH {"status":"archived"}`; read schema metadata
(`/collections`, `/fields`, `/relations`) and use `/mcp`.

Denied by Directus permissions (403, not by prompt):

- `DELETE` on any asset or reference collection
- any write to `asset_index` (create / update / delete)
- `POST /collections`, `POST /fields/...`, `PATCH /relations/...`
- `PATCH /policies/...`, `POST /permissions`, `POST /roles`, `/users` (including self role change)
- dashboards/panels mutation, project settings, other users' records

MCP notes: the agent identity has `app_access=false`, so it cannot open the Studio. `collections`,
`fields` and `relations` MCP tools refuse non-admins outright. `items delete` is disabled by
`mcp_allow_deletes=false` **and** by permission (defense in depth).

**Always scope writes.** `items update` with neither `keys` nor `query.filter` updates every row of
the collection — verified live during testing. Use:

```json
{"action":"update","collection":"vps","keys":["<uuid>"],"data":{"region":"Osaka"}}
```
