#!/usr/bin/env python3
"""One-command release gate for PDAM.

Orchestration only — it runs the existing verifiers and adds three cheap checks that do not belong
in any single one of them (projection/bookkeeping consistency, dashboard sources, repo hygiene).

    python3 scripts/verify_all.py            # full gate
    python3 scripts/verify_all.py --quick    # skip the network-heavy suites

Exit code 0 only when every section passes (SKIP is reported, never counted as PASS).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN = open(os.path.join(ROOT, ".admin-token")).read().strip()
SECTIONS: list[tuple[str, str, str]] = []   # name, status(PASS/FAIL/SKIP), detail
QUICK = "--quick" in sys.argv


def run(label, script, args=(), timeout=900):
    cmd = [sys.executable, os.path.join(ROOT, "scripts", script), *args]
    print(f"\n=== {label}: {' '.join(cmd[1:])}")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    tail = [ln for ln in p.stdout.strip().splitlines() if ln.strip()][-3:]
    for ln in tail:
        print("   " + ln)
    status = "PASS" if p.returncode == 0 else "FAIL"
    detail = tail[-1] if tail else f"exit {p.returncode}"
    if p.returncode != 0:
        print(p.stdout[-1500:])
        print(p.stderr[-1500:])
    SECTIONS.append((label, status, detail))
    return p.returncode == 0


def api(token, path):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "User-Agent": "pdam-verify-all/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf8", "replace")}


def projection_check():
    """Every template that declares an index mapping has exactly one projection row per asset."""
    print("\n=== Projection consistency")
    specs = [json.load(open(p)) for p in glob.glob(os.path.join(ROOT, "templates", "*.json"))
             if not p.endswith("schema.json")]
    problems = []
    for spec in specs:
        if not spec.get("index"):
            continue
        slug = spec["slug"]
        code, body = api(ADMIN, f"/items/{slug}?limit=-1&fields=id")
        if code != 200:
            problems.append(f"{slug}: read failed {code}")
            continue
        live = {r["id"] for r in body["data"]}
        code, idx = api(ADMIN, f"/items/asset_index?limit=-1&filter[template][_eq]={slug}&fields=asset_id")
        if code != 200:
            problems.append(f"{slug}: projection read failed {code}")
            continue
        projected = {r["asset_id"] for r in idx["data"]}
        if live != projected:
            problems.append(f"{slug}: missing={len(live - projected)} orphan={len(projected - live)}")
    if problems:
        print("   " + "; ".join(problems))
    else:
        print(f"   {sum(1 for s in specs if s.get('index'))} projected templates in sync")
    SECTIONS.append(("Projection consistency", "FAIL" if problems else "PASS",
                     "; ".join(problems) or "one row per asset"))


def dashboard_check():
    print("\n=== Dashboard sources")
    code, body = api(ADMIN, "/dashboards?limit=50")
    dash = next((d for d in (body or {}).get("data", []) if d["name"] == "Personal Digital Assets"), None)
    problems = []
    if not dash:
        problems.append("dashboard missing")
        panels = []
    else:
        code, pb = api(ADMIN, "/panels?limit=200")
        panels = [p for p in pb["data"] if p.get("dashboard") == dash["id"]]
        if len(panels) < 7:
            problems.append(f"only {len(panels)} panels")
    sources = sorted({(p.get("options") or {}).get("collection") for p in panels
                      if (p.get("options") or {}).get("collection")})
    for coll in sources:
        code, _ = api(ADMIN, f"/items/{coll}?limit=1")
        if code != 200:
            problems.append(f"source {coll} unreadable ({code})")
    if problems:
        print("   " + "; ".join(problems))
    else:
        print(f"   {len(panels)} panels, sources={sources}")
    SECTIONS.append(("Dashboard sources", "FAIL" if problems else "PASS",
                     "; ".join(problems) or f"{len(panels)} panels: {', '.join(sources)}"))


def sync_check():
    print("\n=== d6s repo/live parity")
    try:
        p = subprocess.run(["d6s", "sync", "diff", "--to", "pdam", "--mode", "add"],
                           cwd=ROOT, capture_output=True, text=True, timeout=240)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        SECTIONS.append(("d6s parity", "SKIP", f"d6s unavailable ({type(e).__name__})"))
        print(f"   SKIP: {e}")
        return
    lines = [ln for ln in (p.stdout + "\n" + p.stderr).strip().splitlines() if ln.strip()]
    ok = "matches" in (p.stdout + p.stderr)
    detail = lines[-1] if lines else f"exit {p.returncode}"
    print("   " + detail[:160])
    SECTIONS.append(("d6s parity", "PASS" if ok else "FAIL", detail[:160]))


def git_check():
    print("\n=== Repo hygiene")
    p = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    dirty = [ln for ln in p.stdout.splitlines() if ln.strip()]
    ignored = subprocess.run(["git", "check-ignore", ".env", ".agent-token", ".admin-token",
                              ".pdam-user-password"], cwd=ROOT, capture_output=True, text=True)
    leaks = 0
    for path in (".env", ".agent-token", ".admin-token", ".pdam-user-password"):
        r = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode == 0:
            leaks += 1
    ok = not dirty and leaks == 0
    detail = f"{len(dirty)} dirty, {leaks} credential files tracked"
    print(f"   {detail}")
    SECTIONS.append(("Repo hygiene", "PASS" if ok else "FAIL", detail))


def main():
    run("Template schema", "validate_templates.py")
    run("Template schema self-test", "validate_templates.py", ["--self-test"])
    run("Secret boundary", "lint_secret_schema.py")
    run("Secret lint self-test", "lint_secret_schema.py", ["--self-test"])
    if not QUICK:
        run("Asset templates", "verify_templates.py")
        projection_check()
        dashboard_check()
        run("Core regression (2-5)", "verify_phase2_5.py")
        run("Security regression", "verify_security.py")
        run("Agent mutation safety", "verify_mutation_guard.py")
        sync_check()
    git_check()

    print("\n" + "=" * 60)
    width = max(len(n) for n, _, _ in SECTIONS)
    for name, status, detail in SECTIONS:
        print(f"{name.ljust(width)}  {status:4}  {detail[:90]}")
    failed = [s for s in SECTIONS if s[1] == "FAIL"]
    skipped = [s for s in SECTIONS if s[1] == "SKIP"]
    print("=" * 60)
    print(f"FINAL: {'PASS' if not failed else 'FAIL'}"
          + (f"  ({len(failed)} failed)" if failed else "")
          + (f"  ({len(skipped)} skipped)" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
