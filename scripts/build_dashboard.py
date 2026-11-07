#!/usr/bin/env python3
"""Phase 6: build the "Personal Digital Assets" dashboard from native Insights panels.

Panel option schemas are taken from Directus 12.6.1 source (app/src/panels/*/index.ts):
  metric       {collection, field, function, sortField, filter, prefix, suffix}
  metric-list  {collection, groupByField, aggregateField, aggregateFunction, limit, sortDirection, filter}
  list         {collection, limit, sortField, sortDirection, displayTemplate, linkToItem, filter}
  pie-chart    {collection, column, function, donut, showLabels, legend, filter}
  label        {text, color, textAlign, fontWeight, fontSize}

Everything reads `asset_index` (cross-type) or `subscriptions` (billing detail). No new tables,
no copied business data, no custom frontend.

    DIRECTUS_URL=... DIRECTUS_TOKEN_FILE=.admin-token python3 scripts/build_dashboard.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("DIRECTUS_URL", "https://pdam.8w8.xyz").rstrip("/")
TOKEN = open(os.environ.get("DIRECTUS_TOKEN_FILE", ".admin-token")).read().strip()
DASHBOARD_NAME = "Personal Digital Assets"
TB = "asset_index"

NOT_ARCHIVED_EXPIRED = {"status": {"_nin": ["archived", "expired"]}}


def api(method, path, body=None):
    path = path.replace(" ", "%20")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "pdam-dashboard/1.0", "Accept": "application/json"})
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


def panel(name, ptype, x, y, w, h, options, icon="insert_chart", color="#6644FF", note=None,
          show_header=True):
    return {"name": name, "icon": icon, "color": color, "show_header": show_header, "note": note,
            "type": ptype, "position_x": x, "position_y": y, "width": w, "height": h,
            "options": options}


def metric(name, filter_, icon, color, x, y, w=3, h=4, note=None):
    return panel(name, "metric", x, y, w, h,
                 {"collection": TB, "field": "asset_id", "function": "count",
                  "sortField": "asset_id", "filter": filter_, "abbreviate": False, "decimals": 0},
                 icon, color, note)


def label(text, x, y, w=12):
    return panel(text, "label", x, y, w, 1,
                 {"text": text, "color": "#6644FF", "textAlign": "left", "fontWeight": "600",
                  "fontSize": "16"}, "label", "#6644FF", show_header=False)


PANELS = [
    # 1. overview
    metric("Total Assets", {}, "inventory_2", "#6644FF", 0, 0, w=3, h=4,
           note="Rows in asset_index (all asset types)"),
    panel("Assets by Type", "metric-list", 3, 0, 9, 8,
          {"collection": TB, "groupByField": "template", "aggregateField": "asset_id",
           "aggregateFunction": "count", "sortDirection": "desc", "limit": 10, "filter": {},
           "numberStyle": "decimal", "notation": "standard"},
          "stacked_bar_chart", "#00C3ED",
          "Count per asset type, straight from the cross-type projection"),

    # 2. attention
    label("Needs attention", 0, 8),
    panel("Expiring Soon (30 days)", "list", 0, 9, 6, 10,
          {"collection": TB,
           "filter": {"_and": [{"expires_on": {"_nnull": True}},
                               {"expires_on": {"_lte": "$NOW(+30 days)"}},
                               {"expires_on": {"_gte": "$NOW"}},
                               NOT_ARCHIVED_EXPIRED]},
           "sortField": "expires_on", "sortDirection": "asc", "limit": 8,
           "displayTemplate": "{{name}} · {{template}} · {{expires_on}}", "linkToItem": False},
          "event_busy", "#E35169",
          "Domains, VPS and cards whose expires_on falls in the next 30 days"),
    panel("Upcoming Renewals", "list", 6, 9, 6, 10,
          {"collection": "subscriptions",
           "filter": {"_and": [{"renewal_on": {"_nnull": True}},
                               {"status": {"_nin": ["archived", "canceled"]}}]},
           "sortField": "renewal_on", "sortDirection": "asc", "limit": 8,
           "displayTemplate": "{{name}} · {{price}} {{currency}} · {{renewal_on}}",
           "linkToItem": True},
          "autorenew", "#FFA439", "Next charge per active subscription (native join, no copy)"),

    # 3. health
    label("Asset health", 0, 19),
    panel("Status Distribution", "pie-chart", 0, 20, 4, 10,
          {"collection": TB, "column": "status", "function": "count", "donut": True,
           "showLabels": True, "legend": True, "decimals": 0, "filter": {}},
          "donut_large", "#2ECDA7", "asset_index.status — NULL would appear as its own slice"),
    panel("Not Active", "list", 4, 20, 8, 10,
          {"collection": TB, "filter": {"status": {"_nin": ["active"]}},
           "sortField": "updated_at", "sortDirection": "desc", "limit": 8,
           "displayTemplate": "{{name}} · {{template}} · {{status}}", "linkToItem": False},
          "report", "#E35169", "Anything not active: inactive, expired, archived"),

    # 4. recent
    label("Recent activity", 0, 30),
    panel("Recently Updated", "list", 0, 31, 12, 10,
          {"collection": TB, "filter": {}, "sortField": "updated_at", "sortDirection": "desc",
           "limit": 10, "displayTemplate": "{{name}} · {{template}} · {{provider_name}}",
           "linkToItem": False},
          "history", "#3399FF", "Last writes across every asset type"),
]


def main():
    code, body = api("GET", "/dashboards?limit=100")
    if code != 200:
        raise SystemExit(f"cannot read dashboards: {code} {body}")
    dash = next((d for d in body["data"] if d["name"] == DASHBOARD_NAME), None)
    if dash:
        did = dash["id"]
        api("PATCH", f"/dashboards/{did}", {"icon": "dashboard", "color": "#6644FF",
                                            "note": "Overview of every digital asset"})
        print(f"= dashboard {DASHBOARD_NAME} ({did})")
    else:
        code, body = api("POST", "/dashboards", {"name": DASHBOARD_NAME, "icon": "dashboard",
                                                 "color": "#6644FF",
                                                 "note": "Overview of every digital asset"})
        if code not in (200, 201):
            raise SystemExit(f"create dashboard failed: {code} {body}")
        did = body["data"]["id"]
        print(f"+ dashboard {DASHBOARD_NAME} ({did})")

    code, body = api("GET", "/panels?limit=200&fields=id,dashboard,name")
    existing = [p for p in body["data"] if p["dashboard"] == did]
    for p in existing:
        api("DELETE", f"/panels/{p['id']}")
    print(f"- removed {len(existing)} panels")

    for p in PANELS:
        payload = dict(p, dashboard=did)
        code, body = api("POST", "/panels", payload)
        if code not in (200, 201):
            raise SystemExit(f"panel {p['name']} failed: {code} {json.dumps(body)[:300]}")
    print(f"+ created {len(PANELS)} panels")


if __name__ == "__main__":
    main()
