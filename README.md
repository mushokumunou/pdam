# pdam — Personal Digital Asset Manager

A self-hosted, schema-driven personal digital asset database built on **Directus** + **PostgreSQL**.
Assets (VPS, domains, accounts, subscriptions, payment methods, …) are modelled as **typed
collections**; each collection *is* an Asset Template, and its fields, list view, filters and
navigation entry are versioned in git.

- Live instance: `https://pdam.8w8.xyz` (Directus Data Studio)
- Docs: [architecture](docs/architecture.md) · [data model](docs/data-model.md) ·
  [template system](docs/template-system.md) · [agent API](docs/agent-api.md) ·
  [implementation plan](docs/implementation-plan.md)

## Status

Phase 0-6 complete and verified against the live instance:

- 8 typed/reference collections + 5 tag junctions + 1 projection (`asset_index`)
- 23 real relations (m2o, m2m), 8 per-template Studio presets, 1 "All Assets" preset
- `asset_index` maintained by generated PostgreSQL triggers; rebuildable and disposable
- 30/30 API + projection + delete-behaviour checks: `python3 scripts/verify_phase2_5.py`
- Insights dashboard "Personal Digital Assets" (7 panels over `asset_index` + `subscriptions`),
  built by `python3 scripts/build_dashboard.py`

Not built (deliberately): custom frontend, dashboard panels, agent RBAC, template builder.

## Why this shape

| Concern | Where it lives |
|---|---|
| Asset data, types, relations, validation | Directus collections on PostgreSQL |
| Template = fields + list columns + filters + nav entry | Directus collection meta, fields and presets (sync files in `directus/`) |
| Human UX | Directus Data Studio (no custom frontend in v1) |
| Agent UX | Directus REST API + built-in MCP server at `/mcp` |
| Secrets (passwords, TOTP, keys) | External secret manager; this system stores only references |

## Requirements

- Docker + Docker Compose v2
- An existing PostgreSQL 14+ server (this repo expects a reachable `postgres` container or host)
- Node.js 22+ locally for the Directus CLI (`d6s`), only needed to push/pull schema
- A hostname with TLS termination (this deployment uses Caddy + Cloudflare DNS-01)

## Quickstart (fresh environment)

```bash
git clone https://github.com/mushokumunou/pdam.git
cd pdam
cp .env.example .env          # set DB password, SECRET, admin email/password
docker compose up -d
```

The container creates the Directus schema tables and the first admin user on first boot.

### Restore the data model (schema + views + navigation)

Two equivalent paths — pick one:

**A. From the spec files (no Studio clicks)**

```bash
export DIRECTUS_URL=https://pdam.example.com
echo "<admin-static-token>" > .admin-token        # git-ignored
python3 scripts/apply_templates.py               # collections, fields, relations, presets
python3 scripts/build_index_sql.py               # regenerate schema/asset_index.sql
psql "$DATABASE_URL" -f schema/asset_index.sql   # projection triggers (read model)
psql "$DATABASE_URL" -f schema/checks.sql        # IP / card / currency / FQDN constraints
```

**B. From the committed sync files (reviewable diffs)**

```bash
npm install -g @directus/cli@12
d6s profile add pdam --url https://pdam.example.com --token <admin-static-token>
d6s sync diff --to pdam       # always preview first
d6s sync push --to pdam       # collections, fields, relations, presets, dashboards
```

Then `d6s sync pull --from pdam` to capture anything done in the Studio.

`d6s` refuses to run when the target Directus version or database vendor differs from the
version recorded in `directus/default/schema/metadata.json`. Keep the image tag in
`docker-compose.yml` and the sync metadata in step.

### Optional: acceptance dataset

```bash
python3 scripts/seed_example.py    # Cloudflare×3 domains, Hetzner×2 VPS, OpenAI→ChatGPT→Wise card
```


### Pull changes back

```bash
d6s sync pull --from pdam      # writes directus/default/**; commit the diff
```

## Repository layout

```
docker-compose.yml          Directus container (pinned version, proxy_net)
.env.example                Required/optional environment variables
directus/default/schema/    One JSON file per collection (fields, relations, interfaces)
directus/default/data/      Configuration records: presets (list views), dashboards, roles…
directus.config.json        CLI profile (URLs only, never tokens)
docs/                       Architecture, data model, template system, agent API, plan
scripts/                    Schema generation, index maintenance, backup helpers
```

## Operations

```bash
docker compose logs -f            # runtime logs
docker compose pull && docker compose up -d   # upgrade (bump tag, then d6s sync diff)
docker exec postgres pg_dump -U admin pdam > pdam-$(date +%F).sql   # backup
```

Backups need **both** halves: the PostgreSQL dump (content) and `directus/` (schema +
configuration). Content is never in the sync files by design.

## Credentials

- Admin login: the email in `.env` (`ADMIN_EMAIL`), password `ADMIN_PASSWORD`.
- A static admin token is required for `d6s` and for MCP clients; store it outside the repo
  (`~/.directus/credentials.json` is written by the CLI, `.admin-token` is git-ignored here).

## License

Application code and configuration: MIT. Directus itself is licensed under the
Monospace Sustainable Core License (MSCL-1.0-GPL); internal self-hosted use is a permitted
purpose, and the grant converts to GPL-3.0 four years after release.
