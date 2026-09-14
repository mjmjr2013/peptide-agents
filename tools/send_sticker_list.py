#!/usr/bin/env python3
"""Send the sticker factory a simple vial-label print list for the current orders.

Manifest-style: one row per SKU — code, quantity (vials), and the label image
itself, inline. No spreadsheet, no zip. The factory reads the picture and prints
that many of it.

"Current orders" = paid, non-legacy, not yet shipped (tracking not sent) — the
same "still needs fulfilling" set the warehouse manifest works from.

    python3 -m tools.send_sticker_list            # writes a preview HTML to look at
    python3 -m tools.send_sticker_list --send      # emails it to FACTORY_EMAIL

Images ride inline (CID in the email, data: URIs in the preview). Reusable: run it
again whenever a new batch of orders needs labels.
"""
from __future__ import annotations
import base64
import mimetypes
import smtplib
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from config import settings
from core.airtable_client import airtable
from core import catalog
from agents.warehouse_payout import _order_items


def _current_label_needs():
    """(order_ids, rows) where rows = [{sku, product, spec, kits, vials, img_path}].
    order_ids is what to mark stickers_sent after a successful send. The factory
    email itself does NOT name the orders — it does not need them."""
    orders = airtable.get_orders_needing_stickers()
    order_ids, agg = [], {}
    for o in orders:
        order_ids.append(o["id"])
        items, _ = _order_items(o)
        for i in items:
            it = catalog.get(i["sku"]) or catalog.find(i["product"], i["spec"])
            sku = it.sku if it else i["sku"]
            a = agg.setdefault(sku, {
                "sku": sku,
                "product": it.sheet_product if it else i["product"],
                "spec": it.sheet_spec if it else i["spec"],
                "kits": 0,
                "img_path": catalog.label_path(sku)})
            a["kits"] += i["kits"]
    rows = sorted(agg.values(), key=lambda r: r["sku"])
    for r in rows:
        r["vials"] = r["kits"] * 10
    return order_ids, rows


def _render_html(rows, img_src) -> str:
    """img_src(row) -> the value for <img src=...>. Lets the email use cid: and the
    preview use a data: URI from the same template."""
    trs = []
    for r in rows:
        src = img_src(r)
        img = (f'<img src="{src}" alt="{r["sku"]}" '
               f'style="max-width:240px;height:auto;border:1px solid #ddd;border-radius:4px">'
               if src else '<span style="color:#b00">no artwork on file</span>')
        trs.append(
            f'<tr>'
            f'<td style="padding:10px 12px;font:600 15px system-ui,Arial;white-space:nowrap">{r["sku"]}</td>'
            f'<td style="padding:10px 12px;font:14px system-ui,Arial;color:#333">{r["product"]} {r["spec"]}</td>'
            f'<td style="padding:10px 12px;font:600 16px system-ui,Arial;text-align:center;white-space:nowrap">{r["vials"]}</td>'
            f'<td style="padding:10px 12px">{img}</td>'
            f'</tr>')
    total_v = sum(r["vials"] for r in rows)
    return f"""\
<div style="font:14px system-ui,Arial;color:#111;max-width:760px">
  <h2 style="margin:0 0 4px">Northline Group — vial label print list</h2>
  <p style="margin:0 0 16px;color:#444">{len(rows)} codes · {total_v} vial labels total.
     Please print the quantity shown of each label below (each kit = 10 vials).</p>
  <table style="border-collapse:collapse;width:100%">
    <thead><tr style="background:#f3f4f6;text-align:left">
      <th style="padding:10px 12px;font:600 13px system-ui,Arial">Code</th>
      <th style="padding:10px 12px;font:600 13px system-ui,Arial">Product</th>
      <th style="padding:10px 12px;font:600 13px system-ui,Arial;text-align:center">Qty (vials)</th>
      <th style="padding:10px 12px;font:600 13px system-ui,Arial">Label</th>
    </tr></thead>
    <tbody>{''.join(trs)}</tbody>
    <tfoot><tr style="background:#f3f4f6">
      <td colspan="2" style="padding:10px 12px;font:600 14px system-ui,Arial">TOTAL</td>
      <td style="padding:10px 12px;font:700 16px system-ui,Arial;text-align:center">{total_v}</td>
      <td></td></tr></tfoot>
  </table>
  <p style="margin:16px 0 0;color:#444">Please confirm you can print these and the timeline. Thank you.</p>
</div>"""


def _data_uri(path: Path) -> str:
    if not path:
        return ""
    ct = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{ct};base64," + base64.b64encode(path.read_bytes()).decode()


def preview() -> Path:
    order_ids, rows = _current_label_needs()
    html = _render_html(rows, lambda r: _data_uri(r["img_path"]))
    out = Path(__file__).resolve().parent.parent / "sticker_list_preview.html"
    out.write_text(html)
    total_v = sum(r["vials"] for r in rows)
    print(f"{len(order_ids)} order(s), {len(rows)} codes, {total_v} vial labels")
    print(f"preview written: {out}")
    return out


def send() -> bool:
    order_ids, rows = _current_label_needs()
    if not rows:
        print("nothing to send — no current orders need labels")
        return False
    recipients = settings.factory_emails
    if not (settings.gmail_user and settings.gmail_app_password and recipients):
        print("[factory] not configured (GMAIL_USER/GMAIL_APP_PASSWORD/FACTORY_EMAIL)")
        return False

    cids = {}
    def img_src(r):
        if not r["img_path"]:
            return ""
        cid = make_msgid(domain="northline")
        cids[r["sku"]] = (cid, r["img_path"])
        return f"cid:{cid[1:-1]}"   # src uses the id without the angle brackets

    html = _render_html(rows, img_src)
    total_v = sum(r["vials"] for r in rows)

    msg = EmailMessage()
    msg["Subject"] = f"Northline Group — vial label print list ({len(rows)} codes, {total_v} labels)"
    msg["From"] = settings.gmail_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Vial label print list. {len(rows)} codes, {total_v} vial labels "
        "(each kit = 10 vials). This email is best viewed as HTML — it shows each "
        "label image with the quantity to print.")
    msg.add_alternative(html, subtype="html")

    html_part = msg.get_payload()[1]
    for sku, (cid, path) in cids.items():
        ct = mimetypes.guess_type(str(path))[0] or "image/png"
        maintype, subtype = ct.split("/", 1)
        html_part.add_related(path.read_bytes(), maintype=maintype,
                              subtype=subtype, cid=cid, filename=f"{sku}.png")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=45) as s:
            s.starttls()
            s.login(settings.gmail_user, settings.gmail_app_password)
            s.send_message(msg)
        airtable.mark_stickers_sent(order_ids)
        print(f"[factory] sent sticker list to {', '.join(recipients)} "
              f"({len(order_ids)} order(s), {len(rows)} codes, {total_v} labels); "
              f"orders marked stickers_sent")
        return True
    except Exception as e:
        print(f"[factory] SMTP send failed: {e!r}")
        return False


if __name__ == "__main__":
    if "--send" in sys.argv:
        raise SystemExit(0 if send() else 1)
    preview()
