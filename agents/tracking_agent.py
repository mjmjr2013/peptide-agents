from __future__ import annotations
"""
Tracking Agent — monitors fulfillment status, sends buyer updates via SMS/email.
Polls Airtable for orders in-flight and notifies buyers of status changes.
"""
import json
import time
from datetime import datetime, timezone

import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content
from twilio.rest import Client as TwilioClient

from core.claude_client import claude
from core.airtable_client import airtable
from config import settings

SYSTEM_PROMPT = """You are a customer communication specialist for Northline Group, a peptide research supply company.

Your job is to draft concise, professional status update messages for buyers whose orders have progressed.

Guidelines:
- Keep SMS messages under 160 characters when possible
- Email messages should be professional and include order details
- For research labs, use formal language and include COA/documentation references
- For distributors, include expected delivery window
- For individuals, be warm but remind them of research-use-only policy

Status messages:
- "Sent to lab": "Your order for {product} has been sent to our synthesis partner. Estimated completion: {eta}."
- "In production": "Your {product} order is now in synthesis. We'll notify you when it ships."
- "Shipped": "Your order has shipped! Tracking: {tracking}. Cold-chain maintained."
- "Delivered": "Your {product} order has been delivered. Please confirm receipt and let us know if you have any questions."

Output JSON:
{
  "sms_message": "...",
  "email_subject": "...",
  "email_body": "..."
}"""

# Legacy buyer-notification path (not started in prod). SendGrid was retired in
# favour of Gmail SMTP for reports; this client is kept inert so the module still
# imports. getattr fallback avoids a hard crash if the setting is absent.
sg_client = sendgrid.SendGridAPIClient(api_key=getattr(settings, "sendgrid_api_key", "") or "disabled")
twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

# Track last-known status to detect changes
_status_cache: dict[str, str] = {}


def check_and_notify_all() -> list[dict]:
    """
    Check all in-flight orders (Sent to lab, In production, Shipped) and
    send buyer updates for any that have changed status since last check.
    """
    in_flight_statuses = ["Sent to lab", "In production", "Shipped"]
    results = []

    for status in in_flight_statuses:
        orders = airtable.get_orders_by_status(status)
        for order in orders:
            result = _process_order(order)
            if result:
                results.append(result)

    return results


def notify_buyer_of_status(order_record_id: str) -> dict:
    """Force-send a status update for a specific order."""
    order = airtable.get_order(order_record_id)
    return _process_order(order, force=True) or {"skipped": True}


def _process_order(order: dict, force: bool = False) -> dict | None:
    order_id = order["id"]
    fields = order["fields"]
    current_status = fields.get("status", "")
    cached_status = _status_cache.get(order_id)

    # Skip if status hasn't changed (unless forced)
    if not force and cached_status == current_status:
        return None

    _status_cache[order_id] = current_status

    # Fetch linked lead for contact info
    lead_ids = fields.get("lead_id", [])
    if not lead_ids:
        return None

    lead = airtable.get_lead(lead_ids[0])
    lead_fields = lead["fields"]

    buyer_phone = lead_fields.get("phone", "")
    buyer_email = lead_fields.get("email", "")
    buyer_name = lead_fields.get("name", "Researcher")
    buyer_type = lead_fields.get("buyer_type", "Individual")

    product = fields.get("product", "your order")
    tracking = fields.get("tracking_number", "")
    quantity_mg = fields.get("quantity_mg", 0)

    # Ask Claude to draft the update messages
    messages = [
        {
            "role": "user",
            "content": f"""Draft a status update for this order.

Buyer: {buyer_name} ({buyer_type})
Product: {product} {quantity_mg}mg
New status: {current_status}
Tracking number: {tracking or 'not yet assigned'}
Order ID: {order_id}

Draft both an SMS and an email update.""",
        }
    ]

    response = claude.create(
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=512,
    )

    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text += block.text

    message_data = _parse_message_json(response_text)
    sms_text = message_data.get("sms_message", f"Northline Group update: {product} order is now {current_status}.")
    email_subject = message_data.get("email_subject", f"Order Update — {product}")
    email_body = message_data.get("email_body", sms_text)

    notifications_sent = []

    # Send SMS if phone available
    if buyer_phone:
        sms_result = _send_sms(buyer_phone, sms_text)
        notifications_sent.append({"channel": "sms", **sms_result})

    # Send email if email available
    if buyer_email:
        email_result = _send_email(buyer_email, buyer_name, email_subject, email_body)
        notifications_sent.append({"channel": "email", **email_result})

    print(f"[TrackingAgent] Order {order_id}: {current_status} → notified {buyer_name}")

    return {
        "order_id": order_id,
        "status": current_status,
        "buyer": buyer_name,
        "notifications": notifications_sent,
    }


def update_tracking_number(order_record_id: str, tracking_number: str) -> dict:
    """
    Call this when you receive a tracking number from the lab.
    Updates Airtable and triggers a shipping notification.
    """
    airtable.update_order(
        order_record_id,
        tracking_number=tracking_number,
        status="Shipped",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return notify_buyer_of_status(order_record_id)


def mark_delivered(order_record_id: str) -> dict:
    """Mark an order as delivered and send confirmation to buyer."""
    airtable.update_order(
        order_record_id,
        status="Delivered",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    return notify_buyer_of_status(order_record_id)


def _send_sms(to: str, body: str) -> dict:
    try:
        msg = twilio_client.messages.create(
            body=body,
            from_=settings.twilio_phone_number,
            to=to,
        )
        return {"success": True, "sid": msg.sid}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _send_email(to_email: str, to_name: str, subject: str, body: str) -> dict:
    try:
        message = Mail(
            from_email=Email(getattr(settings, "gmail_user", ""), settings.company_name),
            to_emails=To(to_email, to_name),
            subject=subject,
            plain_text_content=Content("text/plain", body),
        )
        response = sg_client.send(message)
        return {"success": response.status_code in (200, 201, 202), "status_code": response.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _parse_message_json(text: str) -> dict:
    try:
        start = text.rfind("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


def poll(interval_seconds: int = 600):
    """Poll for order status changes every 10 minutes."""
    print(f"[TrackingAgent] Polling every {interval_seconds}s. Ctrl-C to stop.")
    while True:
        try:
            results = check_and_notify_all()
            if results:
                print(f"[TrackingAgent] Sent {len(results)} notification(s)")
        except Exception as e:
            print(f"[TrackingAgent] Error: {e}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    import sys

    if "--poll" in sys.argv:
        poll()
    elif len(sys.argv) == 2:
        result = notify_buyer_of_status(sys.argv[1])
        print(json.dumps(result, indent=2))
    else:
        results = check_and_notify_all()
        print(json.dumps(results, indent=2))
