from __future__ import annotations
"""
Fulfillment Agent — routes confirmed orders to the supplier via WhatsApp.
Selects orders in Pending status and sends order details via Twilio WhatsApp.
"""
import json
from datetime import datetime, timezone

from twilio.rest import Client as TwilioClient

from core.claude_client import claude
from core.airtable_client import airtable
from config import settings

SYSTEM_PROMPT = """You are a fulfillment coordinator for Northline Group, a peptide research supply company.

Your job is to draft a clear, professional WhatsApp order message to send to our peptide synthesis supplier.

The message must include:
- Order reference number (Airtable order ID)
- Compound name
- Quantity in mg
- Required purity: research grade, >98%
- Required documentation: COA and HPLC report
- Shipping: cold chain required
- Any special instructions

Keep it concise and professional — this is a WhatsApp message, not a formal letter.
Write it as if you are the business owner placing the order directly.

Output a JSON block like:
{
  "whatsapp_message": "..."
}"""

twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)


def route_order_to_supplier(order_record_id: str) -> dict:
    """
    Send a WhatsApp order message to the supplier for a pending order.
    """
    order = airtable.get_order(order_record_id)
    order_fields = order["fields"]

    product = order_fields.get("product", "")
    quantity_mg = order_fields.get("quantity_mg", 0)
    total_price = order_fields.get("total_price", 0)

    if order_fields.get("status") != "Pending":
        return {
            "success": False,
            "error": f"Order status is '{order_fields.get('status')}', expected 'Pending'",
        }

    if not settings.supplier_whatsapp:
        return {"success": False, "error": "SUPPLIER_WHATSAPP not set in .env"}

    # Ask Claude to draft the WhatsApp message
    messages = [
        {
            "role": "user",
            "content": f"""Draft a WhatsApp order message to our supplier.

Order ID: {order_record_id}
Product: {product}
Quantity: {quantity_mg}mg
Order value: ${total_price}

Write a clear order message to send via WhatsApp.""",
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

    message_data = _parse_json(response_text)
    whatsapp_message = message_data.get("whatsapp_message", "")

    if not whatsapp_message:
        return {"success": False, "error": "Claude could not draft message", "response": response_text}

    # Send via Twilio WhatsApp
    send_result = _send_whatsapp(settings.supplier_whatsapp, whatsapp_message)

    if not send_result["success"]:
        return send_result

    # Update order status in Airtable
    airtable.update_order(order_record_id, status="Sent to lab")

    print(f"[FulfillmentAgent] Order {order_record_id} sent to supplier via WhatsApp — {product} {quantity_mg}mg")

    return {
        "success": True,
        "order_record_id": order_record_id,
        "product": product,
        "quantity_mg": quantity_mg,
        "message_sent": whatsapp_message,
    }


def route_all_pending_orders() -> list[dict]:
    """Process all orders currently in Pending status."""
    pending = airtable.get_pending_orders()
    print(f"[FulfillmentAgent] Found {len(pending)} pending order(s)")

    results = []
    for order in pending:
        result = route_order_to_supplier(order["id"])
        result["order_id"] = order["id"]
        results.append(result)

    return results


def _send_whatsapp(to: str, body: str) -> dict:
    try:
        msg = twilio_client.messages.create(
            body=body,
            from_=settings.twilio_whatsapp_from,
            to=f"whatsapp:{to}",
        )
        print(f"[FulfillmentAgent] WhatsApp sent to {to}: SID={msg.sid}")
        return {"success": True, "sid": msg.sid}
    except Exception as e:
        print(f"[FulfillmentAgent] WhatsApp error: {e}")
        return {"success": False, "error": str(e)}


def _parse_json(text: str) -> dict:
    try:
        start = text.rfind("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2:
        result = route_order_to_supplier(sys.argv[1])
        print(json.dumps(result, indent=2))
    else:
        results = route_all_pending_orders()
        print(json.dumps(results, indent=2))
