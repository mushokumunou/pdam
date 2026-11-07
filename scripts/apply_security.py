#!/usr/bin/env python3
"""Phase 7 / 8A: create the PDAM security boundary as Directus policies (configuration as code).

Creates (idempotently):
  policy "PDAM User"   app_access=true   -> humans use Studio + dashboard, no schema/policy changes
  policy "PDAM Agent"  app_access=false  -> agents use REST/MCP only, no Studio
  role   "PDAM User"  / "PDAM Agent"     -> one policy each
  user   PDAM_USER_EMAIL (role PDAM User), PDAM_AGENT_EMAIL (role PDAM Agent, static token)

Credentials are NEVER written to the repo:
  PDAM_USER_PASSWORD / PDAM_AGENT_TOKEN come from the environment.
  Missing values are generated and written to git-ignored files (.pdam-user-password / .agent-token),
  and only a notice is printed.

Permissions are enforced by Directus itself: business collections get read/create/update,
junctions additionally get delete (needed to remove a tag), asset_index is read-only,
no policy can touch schema, dashboards, policies, roles, access or users.

    DIRECTUS_URL=... DIRECTUS_TOKEN_FILE=.admin-token python3 scripts/apply_security.py
"""
from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ADMIN_TOKEN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()

USER_POLICY = "PDAM User"
AGENT_POLICY = "PDAM Agent"
USER_ROLE = "PDAM User"
AGENT_ROLE = "PDAM Agent"
USER_EMAIL = os.environ.get("PDAM_USER_EMAIL", "pdam@8w8.xyz")
AGENT_EMAIL = os.environ.get("PDAM_AGENT_EMAIL", "agent@8w8.xyz")

def discover_collections():
    """Asset/ref collections and their junctions come from the template specs, so adding a
    template does not require editing this file."""
    import glob
    specs = [json.load(open(p)) for p in glob.glob("templates/*.json")]
    assets = sorted(s["slug"] for s in specs if s.get("group") == "assets")
    refs = sorted(s["slug"] for s in specs if s.get("group") == "reference")
    junctions = sorted({f"{s['slug']}_{f['to']}" for s in specs for f in s.get("fields", [])
                        if f.get("type") in ("tags", "m2m")})
    return assets, refs, junctions


ASSETS, REFS, JUNCTIONS = discover_collections()
PROJECTION = ["asset_index"]
# schema discovery: read-only, no write action is ever granted on these
SCHEMA_READ = ["directus_collections", "directus_fields", "directus_relations"]

# what the Studio (human) needs on top of the business model
STUDIO_READ = ["directus_dashboards", "directus_panels", "directus_notifications"]
STUDIO_PRESET = "directus_presets"


def api(method, path, body=None):
    path = path.replace(" ", "%20")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "pdam-security/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def must(code, body, what):
    if code not in (200, 201, 204):
        raise SystemExit(f"{what} failed: {code} {json.dumps(body)[:400]}")


# --------------------------------------------------------------------------- policies / roles

def ensure_policy(name, app_access, description):
    code, body = api("GET", "/policies?limit=200&fields=id,name,app_access,admin_access")
    must(code, body, "list policies")
    for p in body["data"]:
        if p["name"] == name:
            api("PATCH", f"/policies/{p['id']}",
                {"app_access": app_access, "admin_access": False, "description": description})
            print(f"= policy {name} ({p['id']}) app_access={app_access}")
            return p["id"]
    code, body = api("POST", "/policies", {"name": name, "icon": "shield",
                                           "description": description,
                                           "admin_access": False, "app_access": app_access,
                                           "enforce_tfa": False})
    must(code, body, f"create policy {name}")
    print(f"+ policy {name} ({body['data']['id']}) app_access={app_access}")
    return body["data"]["id"]


def ensure_access(role_id, policy_id):
    """Role -> policy binding lives in directus_access; POST /access is the only writable path
    (PATCH /roles with `policies` expects access ids and returns 403)."""
    code, body = api("GET", "/access?limit=200&fields=id,role,user,policy")
    must(code, body, "list access")
    for a in body["data"]:
        if a.get("role") == role_id and a.get("policy") == policy_id:
            return
    code, body = api("POST", "/access", {"role": role_id, "policy": policy_id})
    must(code, body, "create access")


def ensure_role(name, policy_id):
    code, body = api("GET", "/roles?limit=200&fields=id,name")
    must(code, body, "list roles")
    for r in body["data"]:
        if r["name"] == name:
            ensure_access(r["id"], policy_id)
            print(f"= role {name} ({r['id']})")
            return r["id"]
    code, body = api("POST", "/roles", {"name": name, "icon": "badge",
                                        "description": f"{name} role"})
    must(code, body, f"create role {name}")
    rid = body["data"]["id"]
    ensure_access(rid, policy_id)
    print(f"+ role {name} ({rid})")
    return rid


# --------------------------------------------------------------------------- permissions

def ensure_permission(policy_id, collection, action, fields="*", filter_=None):
    code, body = api("GET", "/permissions?limit=500&fields=id,collection,action,policy,fields")
    must(code, body, "list permissions")
    for p in body["data"]:
        if p["policy"] == policy_id and p["collection"] == collection and p["action"] == action:
            return False
    payload = {"policy": policy_id, "collection": collection, "action": action,
               "fields": fields, "permissions": filter_ or {}, "validation": {}}
    code, body = api("POST", "/permissions", payload)
    must(code, body, f"permission {collection}.{action}")
    return True


def grant(policy_id, pairs, label):
    added = 0
    for collection, action in pairs:
        if ensure_permission(policy_id, collection, action):
            added += 1
    print(f"  {label}: {added} new permission rows")


def business_matrix(junction_delete=True):
    rows = []
    for c in ASSETS + REFS:
        rows += [(c, "read"), (c, "create"), (c, "update")]
    for c in JUNCTIONS:
        rows += [(c, "read"), (c, "create")]
        if junction_delete:
            rows.append((c, "delete"))
    for c in PROJECTION:
        rows.append((c, "read"))
    for c in SCHEMA_READ:
        rows.append((c, "read"))
    return rows


# --------------------------------------------------------------------------- users

def ensure_user(email, role_id, password=None, token=None, first_name=None):
    code, body = api("GET", "/users?limit=200&fields=id,email,role,status")
    must(code, body, "list users")
    existing = next((u for u in body["data"] if u["email"] == email), None)
    if existing:
        print(f"= user {email} ({existing['id']})")
        uid = existing["id"]
        patch = {"role": role_id, "status": "active"}
        if password:
            patch["password"] = password
        api("PATCH", f"/users/{uid}", patch)
    else:
        payload = {"email": email, "role": role_id, "status": "active",
                   "first_name": first_name or email.split("@")[0]}
        if password:
            payload["password"] = password
        code, body = api("POST", "/users", payload)
        must(code, body, f"create user {email}")
        uid = body["data"]["id"]
        print(f"+ user {email} ({uid})")
    if token:
        api("PATCH", f"/users/{uid}", {"token": token})
        print(f"  static token set for {email}")
    return uid


def secret_or_generate(env_name, path, label, make=secrets.token_urlsafe):
    value = os.environ.get(env_name)
    if value:
        print(f"  {label}: from ${env_name}")
        return value
    if os.path.exists(path):
        print(f"  {label}: reused from {path}")
        return open(path).read().strip()
    value = make(32)
    with open(path, "w") as fh:
        fh.write(value + "\n")
    os.chmod(path, 0o600)
    print(f"  {label}: generated -> {path} (git-ignored)")
    return value


def main():
    code, me = api("GET", "/users/me")
    must(code, me, "admin auth")
    print(f"admin: {me['data']['email']}")

    user_policy = ensure_policy(USER_POLICY, True,
                                "Humans: Studio + dashboard + asset CRUD, no schema/policy changes, "
                                "no hard delete")
    agent_policy = ensure_policy(AGENT_POLICY, False,
                                 "Agents: REST/MCP only, asset CRUD, no hard delete, no schema or "
                                 "policy changes")

    user_role = ensure_role(USER_ROLE, user_policy)
    agent_role = ensure_role(AGENT_ROLE, agent_policy)

    print("permissions:")
    grant(user_policy, business_matrix(), "PDAM User")
    for c in STUDIO_READ:
        ensure_permission(user_policy, c, "read")
    # NOTE: row-level permission rules are licence-restricted on this instance
    # (`custom_permission_rules_enabled`), so presets are granted collection-wide.
    ensure_permission(user_policy, STUDIO_PRESET, "read")
    ensure_permission(user_policy, STUDIO_PRESET, "create")
    ensure_permission(user_policy, STUDIO_PRESET, "update")
    print("  PDAM User: studio read/preset rows checked")

    grant(agent_policy, business_matrix(), "PDAM Agent")

    pw = secret_or_generate("PDAM_USER_PASSWORD", ".pdam-user-password", "human password",
                            lambda n: "Pdam-" + secrets.token_urlsafe(n))
    tok = secret_or_generate("PDAM_AGENT_TOKEN", ".agent-token", "agent token")

    ensure_user(USER_EMAIL, user_role, password=pw, first_name="PDAM")
    ensure_user(AGENT_EMAIL, agent_role, token=tok, first_name="PDAM Agent")
    print("done")


if __name__ == "__main__":
    main()
