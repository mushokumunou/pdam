# Known limitations (accepted, Directus 12.6.1)

Everything here was observed on the live instance. None of it blocks real asset entry; each item
lists what it costs and what would change it. Do not "fix" these casually — several are upstream
behaviours, not PDAM bugs.

| # | limitation | observed behaviour | impact | would change if |
|---|---|---|---|---|
| 1 | **Field groups are unusable** | alias fields with `special: ["group"]` are omitted from bulk `GET /fields`; Studio then renders neither the group nor its children (upstream directus/directus#28379) | forms are flat and ordered by `meta.sort`, no collapsible sections | upstream fixes the issue; then re-add groups in specs (metadata only) |
| 2 | **Unscoped MCP update hits the whole collection** | `items update` with neither `keys` nor `query.filter` updates every row (it rewrote two test rows) | `scripts/agent_guard.py` rejects it on the supported agent path; a raw MCP client can still do it | server-side row rules available, or Directus requires a target |
| 3 | **Row-level permission rules are licence-gated** | `POST /permissions` with `permissions: {...}` answers `custom_permission_rules_enabled is a restricted resource` | permissions are collection-level; no "agent may only touch its own rows" | a licence that enables custom rules |
| 4 | **`RESTRICT` surfaces as HTTP 500** | deleting a provider in use returns 500 with an FK message, not 409 | clients must treat 500-on-delete as "still referenced" | Directus maps FK errors |
| 5 | **`d6s` panel diff is a permanent no-op** | sync files null `date_created`/`user_created`, the API returns them, so `diff` always reports 10 panel updates | use `d6s sync diff --mode add` to prove parity | upstream normalises these fields |
| 6 | **Dashboard → source asset navigation** | list panels open the item drawer of their own collection (`@click="startEditing(row)"`); projection rows cannot route to `content/<template>/<id>` | cross-type rows are intentionally not clickable (projection must not be edited); navigating to the asset needs a small navigation extension | a lightweight navigation extension exists |
| 7 | **MCP is intermittently 403 with an empty body** | repeated `/mcp` calls occasionally return bare 403 (per-user limiter) | verifiers retry; agents should too | rate-limit configuration is tuned |
| 8 | **`asset_index` has no native write protection** | readonly fields are UI/API hints; an admin can PATCH a projection row | admin bypasses it by design; non-admins are denied by permission | a DB trigger rejecting writes outside `pdam_idx_*` |
| 9 | **No per-field history for assets** | Directus activity/revisions exist for system collections; asset edits are not versioned by default | recent-changes panel is "last write", not an audit trail | enable collection versioning per template |
| 10 | **Secrets are references only** | `secret_references` stores provider/item/type; no Vaultwarden integration | credentials must be looked up manually in the vault | Phase 7B secret-manager integration |
| 11 | **JSON Schema is not enforced with a library** | `jsonschema` is not installed on the host; `validate_templates.py` implements the same rules natively | schema.json is documentation + optional cross-check | `pip install jsonschema` (the script auto-detects it) |
