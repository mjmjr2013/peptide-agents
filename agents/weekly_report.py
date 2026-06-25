from __future__ import annotations
"""
Fulfillment reports — two independent cadences, two separate audiences:

  • Warehouse manifest — DAILY. Per-order customer + address + items so the warehouse
    can make shipping labels and send tracking to clients fast. NO costs/supplier info.
  • Supplier bulk order — WEEKLY. Total kits per product (SKU) to purchase from the
    supplier. NO customer names/addresses/prices.

Each run selects only orders not yet processed for THAT cadence (independent flags
`manifested` and `bulk_ordered`), so nothing is missed or double-counted. Files are
emailed (SendGrid) to settings.report_email for you to review and forward.
"""
import base64
import io
import json
import urllib.request
import urllib.error
from datetime import datetime

from openpyxl import Workbook

from core.airtable_client import airtable
from config import settings

_XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _item_rows(orders: list[dict]) -> list[dict]:
    rows = []
    for o in orders:
        for it in airtable.get_items_for_order(o):
            f = it["fields"]
            rows.append({"product": f.get("product", ""), "spec": f.get("spec", ""),
                         "kits": int(f.get("kits") or 0), "sku": f.get("supplier_sku", "")})
    return rows


def build_supplier_bulk(orders: list[dict], label: str) -> bytes:
    """Aggregate kits per product — supplier-facing. No customer data, no prices."""
    agg: dict[tuple, int] = {}
    for r in _item_rows(orders):
        agg[(r["sku"], r["product"], r["spec"])] = agg.get((r["sku"], r["product"], r["spec"]), 0) + r["kits"]
    wb = Workbook(); ws = wb.active; ws.title = "Bulk Order"
    ws.append([f"Northline Group — Bulk Purchase Order — {label}"])
    ws.append(["SKU", "Product", "Spec", "Total Kits"])
    for (sku, prod, spec), kits in sorted(agg.items(), key=lambda x: (x[0][1], x[0][2])):
        ws.append([sku, prod, spec, kits])
    ws.append([]); ws.append(["", "", "TOTAL KITS", sum(agg.values())])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def build_warehouse_manifest(orders: list[dict], label: str) -> bytes:
    """Per-order customer + address + items — warehouse-facing. No costs/supplier."""
    wb = Workbook(); ws = wb.active; ws.title = "Manifest"
    ws.append([f"Northline Group — Warehouse Shipping Manifest — {label}"])
    ws.append(["Order Ref", "Ship To", "Address", "Phone", "Items"])
    for o in sorted(orders, key=lambda x: x["fields"].get("order_ref", "")):
        f = o["fields"]
        items = "; ".join(f"{int(it['fields'].get('kits') or 0)}x {it['fields'].get('product','')} "
                          f"{it['fields'].get('spec','')}".strip() for it in airtable.get_items_for_order(o))
        addr = ", ".join(x for x in [f.get("address_line1"), f.get("address_line2"), f.get("city"),
               f.get("state_province"), f.get("postal_code"), f.get("country")] if x)
        ws.append([f.get("order_ref", ""), f.get("ship_name", ""), addr, f.get("ship_phone", ""), items])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _send_email(subject: str, body: str, attachments: list[tuple[str, bytes]]) -> bool:
    if not (settings.sendgrid_api_key and settings.report_email and settings.sendgrid_from_email):
        print("[reports] SendGrid not configured (SENDGRID_API_KEY/SENDGRID_FROM_EMAIL/REPORT_EMAIL) — skipping email")
        return False
    payload = {
        "personalizations": [{"to": [{"email": settings.report_email}]}],
        "from": {"email": settings.sendgrid_from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
        "attachments": [{"content": base64.b64encode(d).decode(), "filename": n,
                         "type": _XLSX_CT, "disposition": "attachment"} for n, d in attachments],
    }
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {settings.sendgrid_api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
        print(f"[reports] emailed '{subject}' to {settings.report_email}")
        return True
    except urllib.error.HTTPError as e:
        print(f"[reports] SendGrid error {e.code}: {e.read().decode()[:200]}")
        return False


def run_daily_manifest() -> dict:
    """DAILY: warehouse manifest for all paid orders not yet manifested."""
    orders = airtable.get_unmanifested_paid_orders()
    if not orders:
        print("[reports] daily manifest: no new paid orders")
        return {"manifest_orders": 0, "emailed": False}
    day = datetime.now().strftime("%Y-%m-%d")
    data = build_warehouse_manifest(orders, day)
    body = (f"Warehouse shipping manifest — {day}\nNew paid orders to label & ship: {len(orders)}\n\n"
            f"Attached: warehouse_manifest_{day}.xlsx (customer names, addresses, items).")
    emailed = _send_email(f"Northline warehouse manifest — {day}", body,
                          [(f"warehouse_manifest_{day}.xlsx", data)])
    airtable.mark_manifested([o["id"] for o in orders])
    return {"manifest_orders": len(orders), "emailed": emailed}


def run_supplier_bulk() -> dict:
    """WEEKLY: supplier bulk purchase order for all paid orders not yet bulk-ordered."""
    orders = airtable.get_unbulked_paid_orders()
    if not orders:
        print("[reports] supplier bulk: no new paid orders")
        return {"bulk_orders": 0, "emailed": False}
    label = f"week ending {airtable.week_tag()}"
    data = build_supplier_bulk(orders, label)
    kits = sum(r["kits"] for r in _item_rows(orders))
    body = (f"Supplier bulk purchase order — {label}\nOrders: {len(orders)} · Total kits: {kits}\n\n"
            f"Attached: supplier_bulk_{airtable.week_tag()}.xlsx (aggregate quantities only).")
    emailed = _send_email(f"Northline supplier bulk order — {label}", body,
                          [(f"supplier_bulk_{airtable.week_tag()}.xlsx", data)])
    airtable.mark_bulk_ordered([o["id"] for o in orders])
    return {"bulk_orders": len(orders), "kits": kits, "emailed": emailed}


def run_for_week(week: str) -> dict:
    """Manual preview: email BOTH reports for a given week tag. Does NOT mark anything."""
    orders = airtable.get_paid_orders_for_week(week)
    if not orders:
        return {"week": week, "orders": 0}
    _send_email(f"Northline reports preview — week {week}",
                f"Preview for week {week}: {len(orders)} paid orders (nothing marked).",
                [(f"supplier_bulk_{week}.xlsx", build_supplier_bulk(orders, f"week {week}")),
                 (f"warehouse_manifest_{week}.xlsx", build_warehouse_manifest(orders, f"week {week}"))])
    return {"week": week, "orders": len(orders)}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "daily":
        print(json.dumps(run_daily_manifest(), indent=2))
    elif cmd == "weekly":
        print(json.dumps(run_supplier_bulk(), indent=2))
    else:
        print(json.dumps(run_for_week(cmd), indent=2))
