#!/usr/bin/env python3
"""Secret-boundary lint: PDAM must never grow a field that stores secret material.

Checks both the template specs (source of truth for schema changes) and the live Directus schema.

Rule: a field may *reference* a secret (name ends with `_reference`/`_ref`, or is `reference`),
but must not hold the value itself (`password`, `api_token`, `private_key`, `totp_secret`,
`recovery_codes`, `card_number`, `cvv`, …).

Exit code 1 on violation, so it can gate any verification run.

    DIRECTUS_URL=... DIRECTUS_TOKEN_FILE=.admin-token python3 scripts/lint_secret_schema.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
TOKEN_FILE = os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")

# Names that mean "this column holds the secret itself".
FORBIDDEN = {
    "password", "passwd", "pwd", "pass", "passphrase", "password_hash", "hash",
    "api_token", "access_token", "refresh_token", "auth_token", "bearer_token",
    "token", "secret", "secret_value", "secret_key", "client_secret",
    "private_key", "ssh_private_key", "signing_key", "encryption_key",
    "totp", "totp_secret", "otp", "otp_secret", "mfa_secret",
    "recovery_code", "recovery_codes", "backup_codes", "seed_phrase", "mnemonic",
    "card_number", "pan", "cvv", "cvc", "cvv2", "pin",
}
# Suffixes that make a name a *reference* to secret metadata rather than material.
REFERENCE_SUFFIXES = ("_reference", "_ref", "_provider", "_type", "_url", "_id")
REFERENCE_EXACT = {"reference", "secret_reference", "secret_provider", "secret_type",
                   "last_four", "mask", "masked"}


def is_reference(name: str) -> bool:
    return name in REFERENCE_EXACT or name.endswith(REFERENCE_SUFFIXES)


def violation(name: str) -> bool:
    if is_reference(name):
        return False
    return name in FORBIDDEN


def lint_specs():
    bad = []
    for path in sorted(glob.glob("templates/*.json")):
        spec = json.load(open(path))
        for f in spec.get("fields", []):
            if violation(f["field"]):
                bad.append(f"{path}:{spec['slug']}.{f['field']}")
    return bad


def lint_live():
    token = open(TOKEN_FILE).read().strip()
    req = urllib.request.Request(f"{BASE}/collections?fields=collection,meta.group",
                                 headers={"Authorization": f"Bearer {token}",
                                          "User-Agent": "pdam-lint/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        collections = json.loads(r.read())["data"]
    targets = [c["collection"] for c in collections
               if (c.get("meta") or {}).get("group") in ("assets", "reference", "derived")]
    bad = []
    for coll in sorted(targets):
        try:
            req = urllib.request.Request(f"{BASE}/fields/{coll}",
                                         headers={"Authorization": f"Bearer {token}",
                                                  "User-Agent": "pdam-lint/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                fields = json.loads(r.read())["data"]
        except urllib.error.HTTPError as e:
            bad.append(f"{coll}: cannot read fields ({e.code})")
            continue
        for f in fields:
            if violation(f["field"]):
                bad.append(f"live:{coll}.{f['field']}")
    return targets, bad


def self_test() -> int:
    """Prove the lint actually fires (and does not fire on legitimate references)."""
    must_fail = ["password", "api_token", "private_key", "totp_secret", "recovery_codes",
                 "card_number", "cvv", "client_secret"]
    must_pass = ["secret_reference", "secret_provider", "secret_type", "reference",
                 "last_four", "vault_url", "provider", "name"]
    bad = [n for n in must_fail if not violation(n)]
    wrong = [n for n in must_pass if violation(n)]
    for n in must_fail:
        print(f"  {'OK ' if violation(n) else 'MISS'}  flags {n}")
    for n in must_pass:
        print(f"  {'OK ' if not violation(n) else 'FALSE POSITIVE'}  allows {n}")
    if bad or wrong:
        print(f"self-test FAILED (missed={bad}, false_positives={wrong})")
        return 1
    print("self-test PASS")
    return 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    spec_bad = lint_specs()
    targets, live_bad = lint_live()
    print(f"checked {len(glob.glob('templates/*.json'))} template specs, "
          f"{len(targets)} live collections")
    for item in spec_bad + live_bad:
        print(f"VIOLATION  {item}")
    if spec_bad or live_bad:
        print("\nSecret material must live in the external secret manager; "
              "PDAM may only store a reference (provider + reference + type).")
        sys.exit(1)
    print("PASS  no secret-material fields in specs or live schema")


if __name__ == "__main__":
    main()
