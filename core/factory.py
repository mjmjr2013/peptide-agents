from __future__ import annotations
"""
Label-factory hand-off.

When a white-label order is PAID, the customer's artwork and the print spec go to the
label factory by EMAIL. Deliberately not WhatsApp: the factory is a mainland-China
contact, and Meta silently drops freeform business messages sent outside the 24-hour
window — Twilio reports success and the message never arrives. That exact failure lost
every warehouse manifest 7/3-7/5 (HANDOFF §15/§21), and a print job that vanishes
silently would strand a paid order.

The email carries the artwork as a real attachment rather than a link, so nothing
depends on an Airtable URL that expires in ~2h.
"""
import smtplib
from email.message import EmailMessage

from config import settings


def build_spec_text(order_ref: str, designs: list[tuple[str, str, int]],
                    min_per_design: int, note: str = "") -> str:
    """Plain, non-persona print spec. The factory is a vendor, not a customer —
    no Lily voice, no marketing, just what to print."""
    lines = [f"Northline Group — white label print job",
             f"Order: {order_ref}",
             "",
             f"{len(designs)} designs, minimum {min_per_design} stickers per design.",
             "",
             "The attached artwork is the customer's BRAND TEMPLATE — a single example label.",
             "Reproduce that design for EACH product below, setting the product name and the",
             "strength to the values listed here. Do NOT copy the product name or the mg from",
             "the template image itself; it is only an example and its strength will not match",
             "most of these lines.",
             ""]
    for product, spec, kits in sorted(designs):
        lines.append(f"  - {product} {spec}   ({kits} vial kit{'s' if kits != 1 else ''})")
    if note:
        lines += ["", note]
    lines += ["", "Reply to this email if anything is unclear before printing."]
    return "\n".join(lines)


def send_label_job(order_ref: str, designs: list[tuple[str, str, int]],
                   artwork: list[tuple[str, bytes, str]],
                   min_per_design: int, note: str = "") -> bool:
    """Email the factory a print job. `artwork` is [(filename, bytes, content_type)].
    Returns True only on a confirmed SMTP send, so the caller can leave
    `factory_notified` unset and retry rather than assume delivery."""
    recipients = settings.factory_emails
    if not (settings.gmail_user and settings.gmail_app_password and recipients):
        print("[factory] not configured (GMAIL_USER/GMAIL_APP_PASSWORD/FACTORY_EMAIL) — skipping")
        return False
    if not designs:
        print(f"[factory] {order_ref}: no branded designs — nothing to print")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"Northline label print job — {order_ref} ({len(designs)} designs)"
    msg["From"] = settings.gmail_user
    msg["To"] = ", ".join(recipients)
    msg.set_content(build_spec_text(order_ref, designs, min_per_design, note))

    for filename, data, content_type in artwork:
        maintype, _, subtype = (content_type or "image/jpeg").partition("/")
        msg.add_attachment(data, maintype=maintype or "image",
                           subtype=subtype or "jpeg", filename=filename)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=45) as s:
            s.starttls()
            s.login(settings.gmail_user, settings.gmail_app_password)
            s.send_message(msg)
        print(f"[factory] emailed print job {order_ref} to {', '.join(recipients)} "
              f"({len(designs)} designs, {len(artwork)} attachment(s))")
        return True
    except Exception as e:
        print(f"[factory] SMTP send failed for {order_ref}: {e!r}")
        return False
