from __future__ import annotations
"""
Messaging Agent — qualifies inbound leads via SMS, then takes their order inline.
Full flow: qualify → collect order → confirm → write to Airtable → notify fulfillment.
"""
import json
from datetime import datetime, timezone

from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from core.claude_client import claude
from core.airtable_client import airtable
from core.pricing import get_catalog_text, get_price_list_messages, MARKUP_START, MARKUP_FLOOR
from config import settings

QUALIFY_PROMPT = """You are a professional sales qualifier for Northline Group, a peptide research supply company.

Your role is to qualify inbound leads via SMS and gather the information needed to route them appropriately.

Buyer types and how to handle them:
- **Research lab**: High-value. Confirm institution name, PI name, products of interest. Move to Qualified quickly.
- **Distributor**: Medium-high value. Confirm company, territory, distribution license.
- **Individual**: Confirm they are a licensed researcher/professional. Products are for research use only.

Qualification criteria:
- Research labs: provide institution name
- Distributors: confirm distribution license
- Individuals: confirm licensed research professional

Keep responses concise (1-3 sentences max) — this is SMS. Be professional but warm.
Always end with a JSON block:
{
  "action": "continue" | "qualify" | "disqualify",
  "buyer_type": "Research lab" | "Distributor" | "Individual" | null,
  "reply_message": "...",
  "notes": "..."
}"""


def _build_order_prompt() -> str:
    catalog = get_catalog_text()
    return f"""You are a sales agent for Northline Group, a peptide research supply company.

Someone has reached out to us — they are an inbound lead. Be welcoming, helpful, and share pricing freely.

CRITICAL: Do NOT ask them to qualify themselves. Do NOT ask what type of buyer they are. Do NOT ask for credentials. If they ask for a price list or specific product pricing, give it to them immediately.

PRICING RULES:
- All prices are per kit (10 vials). We sell by the kit only.
- List price is 6x our cost. Floor price is 3x our cost. Never go below floor.
- Start every quote at list price (6x).
- You have authority to negotiate down toward the floor based on:
  * Order volume: larger orders (5+ kits) justify moving toward 4-5x
  * High-volume signals (e.g. "200 kits/week") — move aggressively toward floor to win the account
  * Repeat/serious buyers: reward commitment
  * Never volunteer a discount — only move if they push back on price
  * Never reveal our cost or markup structure
  * Retatrutide 10mg at $99.99/kit is already exceptional market pricing — hold firm here, don't discount unless volume is very high (20+ kits)

CATALOG (List Price = 6x cost | Floor = 3x cost):
{catalog}

FLOW:
1. Greet warmly and help them find what they need
2. If they ask for a price list, share the relevant products and prices directly
3. If they ask about a specific product, quote the list price per kit and total
4. If they push back on price, negotiate — move in increments, not all at once
5. Once price is agreed, confirm the full order details and place it

Keep replies short — this is WhatsApp. Round prices to 2 decimal places.
Always end with a JSON block:
{{
  "action": "collect" | "confirm" | "place" | "invalid",
  "product": "...",
  "spec": "...",
  "quantity_kits": 0,
  "total_price": 0.0,
  "reply_message": "...",
  "notes": "..."
}}"""

twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

# ── Conversation state ─────────────────────────────────────────────────────────
_conversations: dict[str, list[dict]] = {}
_lead_stage: dict[str, str] = {}  # phone -> "qualifying" | "ordering"


def get_conversation(phone: str) -> list[dict]:
    return _conversations.get(phone, [])


def save_conversation(phone: str, messages: list[dict]):
    _conversations[phone] = messages


def get_stage(phone: str) -> str:
    return _lead_stage.get(phone, "qualifying")


def set_stage(phone: str, stage: str):
    _lead_stage[phone] = stage


# ── Core logic ─────────────────────────────────────────────────────────────────

def handle_inbound(from_phone: str, body: str, name: str = "") -> str:
    print(f"[MessagingAgent] Inbound from {from_phone}: {body!r}")

    conversation = get_conversation(from_phone)
    stage = get_stage(from_phone)
    existing_lead = airtable.find_lead_by_phone(from_phone)

    # If lead is already Qualified or Converted, go straight to ordering
    if existing_lead and existing_lead["fields"].get("status") in ("Qualified", "Converted"):
        stage = "ordering"
        set_stage(from_phone, "ordering")

    conversation.append({"role": "user", "content": body})

    # Inbound leads skip qualification — go straight to pricing/ordering
    if stage == "qualifying":
        set_stage(from_phone, "ordering")
        stage = "ordering"

    # Detect price list requests — send full formatted catalog
    price_keywords = ["price list", "pricelist", "price sheet", "full list", "catalog",
                      "catalogue", "all products", "all prices", "send prices", "full catalog"]
    if any(kw in body.lower() for kw in price_keywords):
        reply = _send_price_list(from_phone)
        conversation.append({"role": "assistant", "content": "[Price list sent]"})
        save_conversation(from_phone, conversation)
        return reply

    reply = _handle_ordering(from_phone, conversation, existing_lead)

    conversation.append({"role": "assistant", "content": reply})
    save_conversation(from_phone, conversation)
    return reply


def _handle_qualifying(phone: str, conversation: list[dict], existing_lead: dict | None, name: str) -> str:
    lead_context = ""
    if existing_lead:
        f = existing_lead["fields"]
        lead_context = f"\n\nExisting CRM record: name={f.get('name','')}, status={f.get('status','')}, buyer_type={f.get('buyer_type','')}"

    response = claude.create(
        system=QUALIFY_PROMPT + lead_context,
        messages=conversation,
        max_tokens=512,
    )

    response_text = _extract_text(response)
    action_data = _parse_json(response_text)
    reply = action_data.get("reply_message", "Thanks for reaching out! Who am I speaking with?")
    action = action_data.get("action", "continue")
    buyer_type = action_data.get("buyer_type")
    notes = action_data.get("notes", "")

    if action == "qualify":
        lead = _upsert_lead(phone, name, buyer_type, notes, existing_lead, "Qualified")
        set_stage(phone, "ordering")
        # Transition message into ordering
        reply = f"{reply} What product are you looking for and how many mg do you need?"

    elif action == "disqualify":
        if existing_lead:
            airtable.update_lead_status(existing_lead["id"], "Dead", notes=notes)

    return reply


def _handle_ordering(phone: str, conversation: list[dict], existing_lead: dict | None) -> str:
    buyer_type = ""
    lead_id = ""
    if existing_lead:
        buyer_type = existing_lead["fields"].get("buyer_type", "")
        lead_id = existing_lead["id"]

    buyer_context = f"\n\nBuyer type: {buyer_type}" if buyer_type else ""

    response = claude.create(
        system=_build_order_prompt() + buyer_context,
        messages=conversation,
        max_tokens=512,
    )

    response_text = _extract_text(response)
    action_data = _parse_json(response_text)
    reply = action_data.get("reply_message", "What product and quantity are you looking for?")
    action = action_data.get("action", "collect")
    product = action_data.get("product", "")
    spec = action_data.get("spec", "")
    quantity_kits = action_data.get("quantity_kits", 0)
    total_price = action_data.get("total_price", 0.0)
    notes = action_data.get("notes", "")

    if action == "place" and lead_id and product and quantity_kits:
        try:
            order = airtable.create_order(
                lead_id=lead_id,
                product=f"{product} {spec}".strip(),
                quantity_mg=float(quantity_kits),  # storing kits in quantity field
                total_price=float(total_price),
            )
            airtable.update_lead_status(lead_id, "Converted", notes=notes)
            print(f"[MessagingAgent] Order placed: {order['id']} — {product} {spec} x{quantity_kits} kits @ ${total_price}")
        except Exception as e:
            print(f"[MessagingAgent] Order creation error: {e}")

    return reply


def _upsert_lead(phone: str, name: str, buyer_type: str | None,
                 notes: str, existing_lead: dict | None, status: str) -> dict | None:
    if existing_lead:
        airtable.update_lead_status(existing_lead["id"], status, notes=notes)
        if buyer_type:
            airtable.leads.update(existing_lead["id"], {"buyer_type": buyer_type})
        return existing_lead
    else:
        airtable.create_lead(
            name=name or phone,
            email="",
            phone=phone,
            buyer_type=buyer_type or "Individual",
            source="Direct",
            notes=notes,
        )
        lead = airtable.find_lead_by_phone(phone)
        if lead:
            airtable.update_lead_status(lead["id"], status)
        return lead


def _extract_text(response) -> str:
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
    return text


def _parse_json(text: str) -> dict:
    # Try to find the last complete JSON block
    try:
        start = text.rfind("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    # Try finding any JSON block from the start
    try:
        import re
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        pass
    # Extract reply_message from plain text as last resort
    if text.strip():
        # Use whatever Claude said as the reply
        clean = text.split("{")[0].strip() if "{" in text else text.strip()
        if clean:
            return {"action": "continue", "reply_message": clean}
    return {"action": "continue", "reply_message": "What product and quantity are you looking for?"}


# ── Twilio helpers ─────────────────────────────────────────────────────────────

def _send_price_list(to: str) -> str:
    """Send the price list as a single image via WhatsApp."""
    from core.price_image import generate_price_list_image_cn, CN_OUTPUT_PATH

    # Regenerate image if it doesn't exist
    if not CN_OUTPUT_PATH.exists():
        generate_price_list_image_cn()

    ngrok_url = _get_ngrok_url()
    image_url = f"{ngrok_url}/price-list.png"
    from_number = settings.twilio_whatsapp_from if "whatsapp" in to else settings.twilio_phone_number

    try:
        twilio_client.messages.create(
            body="Here's our current price list — all prices per kit (10 vials). Reply with a product name to get a specific quote or place an order. 🧬",
            from_=from_number,
            to=to,
            media_url=[image_url],
        )
        print(f"[MessagingAgent] Price list image sent to {to}")
        return ""  # Image already sent, no additional reply needed
    except Exception as e:
        print(f"[MessagingAgent] Image send failed, falling back to text: {e}")
        messages = get_price_list_messages()
        for msg in messages[:-1]:
            try:
                twilio_client.messages.create(body=msg, from_=from_number, to=to)
            except Exception:
                pass
        return messages[-1]


def _get_ngrok_url() -> str:
    """Get the current ngrok public URL."""
    try:
        import requests as req
        data = req.get("http://localhost:4040/api/tunnels", timeout=2).json()
        return data["tunnels"][0]["public_url"]
    except Exception:
        return ""


def send_sms(to: str, body: str):
    msg = twilio_client.messages.create(
        body=body,
        from_=settings.twilio_phone_number,
        to=to,
    )
    print(f"[MessagingAgent] Sent SMS to {to}: SID={msg.sid}")
    return msg.sid


def twilio_webhook_handler(form_data: dict) -> str:
    from_phone = form_data.get("From", "")
    body = form_data.get("Body", "").strip()
    profile_name = form_data.get("ProfileName", "")

    reply_text = handle_inbound(from_phone, body, name=profile_name)

    twiml = MessagingResponse()
    twiml.message(reply_text)
    return str(twiml)


def initiate_outreach(phone: str, lead_id: str, buyer_type: str):
    templates = {
        "Research lab": f"Hi, this is {settings.company_name}! We specialize in research peptides for labs. What compounds are you working with?",
        "Distributor": f"Hi from {settings.company_name}! Interested in our wholesale program. What's your distribution territory?",
        "Individual": f"Hi, this is {settings.company_name}. Our peptides are for research use only. Can you confirm your research affiliation?",
    }
    message = templates.get(buyer_type, f"Hi, this is {settings.company_name}! How can we help you?")
    send_sms(phone, message)
    airtable.update_lead_status(lead_id, "Contacted", notes=f"Outbound initiated: {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    import sys
    phone = sys.argv[1] if len(sys.argv) > 1 else "+15550000000"
    message = sys.argv[2] if len(sys.argv) > 2 else "Hi, I'm a researcher at MIT interested in BPC-157."
    reply = handle_inbound(phone, message, name="Test User")
    print(f"\nReply: {reply}")
