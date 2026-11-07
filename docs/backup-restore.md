# Backup, restore and credential recovery

Two different things must be backed up, and neither replaces the other.

| what | where | contains | restore mechanism |
|---|---|---|---|
| **Asset data** | PostgreSQL database `pdam` | rows of every typed collection, junctions, `asset_index` contents, users, activity | `pg_restore` of a dump |
| **Shape of the system** | git repo (`directus/`, `templates/`, `schema/`, `scripts/`) | collections, fields, relations, presets, dashboards, panels, policies, roles, permissions | `d6s sync push` + `psql -f schema/asset_index.sql` |
| uploads (only if used) | `/opt/data/pdam/uploads` | files attached through Directus | copy the directory |
| runtime config | `/opt/stacks/pdam/.env` (0600) | DB password, `SECRET`, admin bootstrap | restore the file out-of-band (never into git) |

**A `git clone` restores the shape, never the data. A database restore alone without the repo
restores data into a schema nobody can reproduce.** Keep both.

## 1. Taking a backup

```bash
# asset data (custom format, compressed)
docker exec postgres pg_dump -U admin -d pdam -Fc -f /tmp/pdam-$(date +%F-%H%M).dump
docker cp postgres:/tmp/pdam-$(date +%F-%H%M).dump ./backups/

# shape of the system
git -C pdam tag -a "backup/$(date +%F)" -m "schema snapshot" && git -C pdam push --tags   # optional
```

Size reference: a fresh instance with ~10 assets dumps to ~240 KB.

## 2. Restoring (verified procedure)

Restore into a **new database**, never over the live one:

```bash
docker exec postgres psql -U admin -d postgres -c 'CREATE DATABASE pdam_restore;'
docker exec postgres pg_restore -U admin -d pdam_restore /tmp/pdam-20261107-003324.dump
```

Then point a throwaway Directus at `DB_DATABASE=pdam_restore` if you need to inspect it in the UI,
or just query it with `psql`. To make a restored database the live one: stop the container, rename
the databases, start, and run `d6s sync diff --to pdam` to confirm the shape matches the repo.

Rebuild the projection after any restore or suspicious write:

```bash
docker exec postgres psql -U admin -d pdam -c 'select pdam_index_rebuild();'
```

### What the restore test actually proved (Phase 10)

Dump of the live database restored into `pdam_restore_test` in the same PostgreSQL instance:

```
vps 2->2   domains 3->3   accounts 3->3   subscriptions 1->1   payment_methods 1->1
certificates 0->0   providers 4->4   tags 3->3   secret_references 1->1
asset_index 10 -> 10
TRUNCATE asset_index -> 0 rows -> pdam_index_rebuild() -> 10 rows   (projection is reproducible)
junction data intact: example-test.com retains its tag row
```

The temporary database was dropped afterwards; production was never touched.

## 3. Projection is not a backup

`asset_index` is derived. Never treat a dump of it as the backup of your assets, and never restore
only that table. If it is truncated, rebuild it from the typed collections.

## 4. Credential recovery (no secrets in this file)

| leaked | action |
|---|---|
| **Agent static token** | admin: `PATCH /users/<agent-id>` with a new `token`, update the runtime env/secret store, restart whatever holds the old one, then re-run `python3 scripts/verify_security.py` |
| **PDAM User password** | admin: `PATCH /users/<id> {"password": "…"}` (or Studio → User), delete stale `directus_sessions`, re-verify |
| **Admin password** | `PATCH /users/<admin-id> {"password": …}` from an authenticated admin session; if the session is also lost, reset via the container: `docker exec -it pdam npx directus users passwd --email admin@…` |
| **`SECRET` / DB password** | rotate in `/opt/stacks/pdam/.env`, restart the container; rotating `SECRET` invalidates sessions and static tokens (re-issue them) |

Revocation is not enough on its own: after rotating an agent token, confirm the old one fails
(`curl -H "Authorization: Bearer <old>" …/server/ping` → 401/403) and that the new one passes the
verification suite.

## 5. Suggested cadence

- database dump: daily (or before every schema change), keep ≥ 7 rotations
- repo: every change is already a commit; tag before upgrades
- uploads: only if the instance stores files
- an untested backup is not a backup: repeat the restore-into-temp-db check quarterly
