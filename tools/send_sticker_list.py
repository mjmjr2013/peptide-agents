#!/usr/bin/env python3
"""Send the sticker factory a vial-label print list for the current orders, as a PDF.

One row per SKU — code, product, quantity (vials), and the label image itself. The
factory asked for a PDF file (2026-09-14), so the list is built as a PDF and emailed
as an attachment. No order numbers — the factory does not need them.

"Current orders" = paid, non-legacy, not yet shipped (tracking not sent), not yet
sticker-sent — the same "still needs fulfilling" set the warehouse manifest works from.

    python3 -m tools.send_sticker_list            # writes a preview PDF to look at
    python3 -m tools.send_sticker_list --send      # emails the PDF to FACTORY_EMAIL

The PDF is built with Pillow (already a dependency) so nothing new has to install on
Railway. Reusable: run it again whenever a new batch of orders needs labels.
"""
from __future__ import annotations
import io
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from config import settings
from core.airtable_client import airtable
from core import catalog
from agents.warehouse_payout import _order_items


def _current_label_needs():
    """(order_ids, rows) where rows = [{sku, product, spec, kits, vials, img_path}].
    order_ids is what to mark stickers_sent after a successful send. The list itself
    never names the orders — the factory does not need them."""
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


# ── PDF ──────────────────────────────────────────────────────────────────────
# US Letter at 150 dpi. Built with Pillow: each page is a raster image, and the
# label artwork is pasted straight in, so what the factory prints is exactly the
# artwork on file — no font substitution or vector redraw to go wrong.
_PAGE_W, _PAGE_H = 1275, 1650
_MARGIN = 60
_COL_CODE, _COL_PROD, _COL_QTY, _COL_IMG = 60, 250, 640, 800
_IMG_W = 400                      # label drawn this wide; height keeps aspect
_ROW_H = 150


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    from matplotlib import font_manager
    prop = font_manager.FontProperties(family="DejaVu Sans",
                                       weight="bold" if bold else "normal")
    try:
        return ImageFont.truetype(font_manager.findfont(prop), size)
    except Exception:
        return ImageFont.load_default()


def build_sticker_pdf(rows: list[dict]) -> bytes:
    from PIL import Image, ImageDraw
    f_title = _font(34, bold=True)
    f_sub = _font(20)
    f_head = _font(20, bold=True)
    f_code = _font(24, bold=True)
    f_cell = _font(20)
    f_qty = _font(26, bold=True)
    total_v = sum(r["vials"] for r in rows)

    pages: list[Image.Image] = []
    img = draw = None
    y = 0

    def new_page(first: bool):
        nonlocal img, draw, y
        img = Image.new("RGB", (_PAGE_W, _PAGE_H), "white")
        draw = ImageDraw.Draw(img)
        y = _MARGIN
        if first:
            draw.text((_MARGIN, y), "Northline Group — vial label print list", font=f_title, fill="black")
            y += 46
            draw.text((_MARGIN, y),
                      f"{len(rows)} codes · {total_v} vial labels total · each kit = 10 vials",
                      font=f_sub, fill="#444444")
            y += 40
        # column header
        draw.rectangle([_MARGIN, y, _PAGE_W - _MARGIN, y + 34], fill="#f0f1f3")
        draw.text((_COL_CODE + 6, y + 7), "Code", font=f_head, fill="black")
        draw.text((_COL_PROD, y + 7), "Product", font=f_head, fill="black")
        draw.text((_COL_QTY, y + 7), "Qty (vials)", font=f_head, fill="black")
        draw.text((_COL_IMG, y + 7), "Label", font=f_head, fill="black")
        y += 44
        pages.append(img)

    new_page(True)
    for r in rows:
        if y + _ROW_H > _PAGE_H - _MARGIN:
            new_page(False)
        cy = y + _ROW_H // 2
        draw.text((_COL_CODE + 6, cy - 12), r["sku"], font=f_code, fill="black")
        draw.text((_COL_PROD, cy - 10), f"{r['product']} {r['spec']}", font=f_cell, fill="#222222")
        draw.text((_COL_QTY + 20, cy - 13), str(r["vials"]), font=f_qty, fill="black")
        if r.get("img_path"):
            try:
                label = Image.open(r["img_path"]).convert("RGB")
                w = _IMG_W
                h = int(label.height * (w / label.width))
                label = label.resize((w, h))
                img.paste(label, (_COL_IMG, y + (_ROW_H - h) // 2))
            except Exception:
                draw.text((_COL_IMG, cy - 10), "(artwork unavailable)", font=f_cell, fill="#b00000")
        else:
            draw.text((_COL_IMG, cy - 10), "no artwork on file", font=f_cell, fill="#b00000")
        draw.line([_MARGIN, y + _ROW_H, _PAGE_W - _MARGIN, y + _ROW_H], fill="#dddddd")
        y += _ROW_H

    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return buf.getvalue()


def preview() -> Path:
    order_ids, rows = _current_label_needs()
    if not rows:
        print("no current orders need labels — nothing to preview")
        return None
    pdf = build_sticker_pdf(rows)
    out = Path(__file__).resolve().parent.parent / "sticker_list_preview.pdf"
    out.write_bytes(pdf)
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

    total_v = sum(r["vials"] for r in rows)
    pdf = build_sticker_pdf(rows)

    msg = EmailMessage()
    msg["Subject"] = f"Northline Group — vial label print list ({len(rows)} codes, {total_v} labels)"
    msg["From"] = settings.gmail_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        f"Please find attached the vial label print list — {len(rows)} codes, "
        f"{total_v} vial labels (each kit = 10 vials). Each row shows the label image "
        f"and how many to print. Please confirm you can print these and the timeline. "
        f"Thank you.")
    msg.add_attachment(pdf, maintype="application", subtype="pdf",
                       filename="northline_sticker_list.pdf")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=45) as s:
            s.starttls()
            s.login(settings.gmail_user, settings.gmail_app_password)
            s.send_message(msg)
        airtable.mark_stickers_sent(order_ids)
        print(f"[factory] sent sticker list PDF to {', '.join(recipients)} "
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
