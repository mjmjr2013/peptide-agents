from __future__ import annotations
"""
Fulfillment reports — two independent cadences, two separate audiences:

  • Warehouse manifest — DAILY. Per-order customer + address + items so the warehouse
    can make shipping labels and send tracking to clients fast. NO costs/supplier info.
    Sent over WhatsApp (Twilio) to settings.warehouse_whatsapp.
  • Supplier bulk order — WEEKLY. Total kits per product (SKU) to purchase from the
    supplier. NO customer names/addresses/prices. Emailed (SendGrid) to
    settings.report_emails for you/your brother to review and forward to the supplier.

Each run selects only orders not yet processed for THAT cadence (independent flags
`manifested` and `bulk_ordered`), so nothing is missed or double-counted.
"""
import io
import json
import smtplib
from email.message import EmailMessage
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
    recipients = settings.report_emails
    if not (settings.gmail_user and settings.gmail_app_password and recipients):
        print("[reports] Gmail SMTP not configured (GMAIL_USER/GMAIL_APP_PASSWORD/REPORT_EMAIL) — skipping email")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.gmail_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    for name, data in attachments:
        maintype, subtype = _XLSX_CT.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls()
            s.login(settings.gmail_user, settings.gmail_app_password)
            s.send_message(msg)
        print(f"[reports] emailed '{subject}' to {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"[reports] SMTP send failed: {e!r}")
        return False


def build_warehouse_whatsapp(orders: list[dict], label: str) -> list[str]:
    """Per-order customer + address + items as WhatsApp text. No costs/supplier.

    Returns a list of message chunks (WhatsApp body cap ~1600 chars), one order
    never split across chunks."""
    header = f"📦 Northline shipping manifest — {label}\n{len(orders)} order(s) to label & ship:\n"
    blocks = []
    for o in sorted(orders, key=lambda x: x["fields"].get("order_ref", "")):
        f = o["fields"]
        items = "\n".join(f"  • {int(it['fields'].get('kits') or 0)}x {it['fields'].get('product','')} "
                          f"{it['fields'].get('spec','')}".rstrip() for it in airtable.get_items_for_order(o))
        addr = "\n".join(x for x in [f.get("address_line1"), f.get("address_line2"),
               " ".join(y for y in [f.get("city"), f.get("state_province"), f.get("postal_code")] if y),
               f.get("country")] if x)
        phone = f.get("ship_phone", "")
        blocks.append(f"\n──────────\n*{f.get('order_ref','')}* — {f.get('ship_name','')}\n"
                      f"{addr}\n{('☎ ' + phone) if phone else ''}\nItems:\n{items}".rstrip())
    chunks, cur = [], header
    for b in blocks:
        if len(cur) + len(b) > 1500:
            chunks.append(cur); cur = ""
        cur += b
    if cur.strip():
        chunks.append(cur)
    return chunks


def _send_whatsapp(chunks: list[str]) -> bool:
    if not (settings.warehouse_whatsapp and settings.twilio_whatsapp_from):
        print("[reports] WhatsApp not configured (WAREHOUSE_WHATSAPP/TWILIO_WHATSAPP_FROM) — skipping daily manifest send")
        return False
    from agents.messaging_agent import twilio_client
    to = settings.warehouse_whatsapp
    if not to.startswith("whatsapp:"):
        to = "whatsapp:" + to
    try:
        for c in chunks:
            msg = twilio_client.messages.create(body=c, from_=settings.twilio_whatsapp_from, to=to)
            print(f"[reports] WhatsApp manifest chunk to {to} SID={msg.sid}")
        return True
    except Exception as e:
        print(f"[reports] WhatsApp send failed: {e!r}")
        return False


def run_daily_manifest() -> dict:
    """DAILY: ping the warehouse rep over WhatsApp with a link to the tracking page
    (a phone-friendly sheet showing every paid order still needing a tracking number).
    The rep enters tracking on the page; it writes to Airtable and texts the customer.
    """
    orders = airtable.get_orders_needing_tracking()
    n = len(orders)
    if n == 0:
        print("[reports] daily manifest: no orders need tracking")
        return {"pending": 0, "sent": False}
    from agents.messaging_agent import _BASE_URL
    link = f"{_BASE_URL}/manifest?token={settings.manifest_token}"
    body = (f"📦 Good day, dear! You have *{n}* order(s) ready to ship. Please open the "
            f"sheet and enter the tracking number for each one — it goes straight to the "
            f"customer:\n{link}")
    sent = _send_whatsapp([body])
    return {"pending": n, "sent": sent}


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
