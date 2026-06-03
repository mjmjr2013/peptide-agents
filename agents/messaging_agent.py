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
from core.pricing import get_catalog_text, get_price_list_messages, get_floor_price, MARKUP_START, MARKUP_FLOOR
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


# ── Payment (crypto) ─────────────────────────────────────────────────────────
# Crypto-only payment. USDT or BTC. These are PLACEHOLDERS — swap in the real
# wallet addresses here when provided; the prompt below reads from these constants.
USDT_WALLET_ADDRESS = "PLACEHOLDER_USDT_TRC20_ADDRESS"   # USDT (TRC20)
BTC_WALLET_ADDRESS = "PLACEHOLDER_BTC_ADDRESS"           # BTC


def _build_order_prompt() -> str:
    catalog = get_catalog_text()
    return f"""You are a sales representative for Northline Group, a research peptide LAB in China.
We are the lab — the manufacturer. We make and ship the product ourselves, direct from China.

WHO WE ARE — say this clearly when relevant:
- We are Chinese. We are the lab in China. You speak as a Chinese sales rep.
- We ship everything from China. We do NOT have US-based fulfillment. No US warehouse.
- We rarely do business direct with Americans. If buyer is American, it is fine, but be a
  little reserved — we mostly supply labs and distributors, not US individuals. Do not be
  pushy with American individual buyers; keep it businesslike.

SPEECH STYLE — IMPORTANT, follow this:
- Write like a Chinese sales rep who speaks English well as a second language — clear and
  mostly grammatical, with a light non-native flavor. NOT broken, NOT "fresh off the boat."
- Mostly complete sentences. Keep articles ("a"/"the") and plurals most of the time. A small
  ESL touch is okay now and then (slightly direct phrasing, an occasional dropped word), but
  do not overdo it. Aim for the middle: clearly non-native, but smooth and professional.
- Example tone:
  "Yes, we have it. Retatrutide 10mg is $94.82 per kit. How many kits you need?"
  "We ship from China. Standard is 4 weeks or less. You want the faster option?"
  "Okay, sounds good. I will set up the order for you now."
- Keep it friendly and brief — 1-2 short lines. No long paragraphs, no fancy words.

CRITICAL: Do NOT ask them to qualify themselves. Do NOT ask what type of buyer they are. Do NOT ask for credentials. If they ask for a price list or specific product pricing, give it to them immediately.

SHIPPING (tell them when they ask, or when confirming an order):
- We ship from China only. No US fulfillment.
- Standard shipping: $95 flat, 4 weeks or less. (This is the default.)
  * FREE standard shipping when product total is over $1000 — no $95 fee.
- Expedited shipping: $235 flat, 10 days or less.
- Shipping fee is ADDED on top of the product total. Always state shipping fee
  and the final total (product + shipping) when confirming the order.

PAYMENT (crypto only):
- We take crypto only. USDT or BTC. No card, no bank, no PayPal.
- Ask which coin they want to pay with: USDT or BTC.
- When order is confirmed and they pick a coin, give them the matching wallet address:
  * USDT (TRC20): {USDT_WALLET_ADDRESS}
  * BTC: {BTC_WALLET_ADDRESS}
- Tell them to send exact amount, then send screenshot of payment. We ship after payment confirm.
- Do NOT give wallet address before order details and price are agreed.

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

SENDING THE FULL PRICE LIST:
We have a complete bilingual price list spreadsheet that can be sent as a file attachment.

Use action "send_price_list" whenever the buyer wants pricing in general / the whole catalog
rather than one specific product. This INCLUDES short, bare requests. Treat ALL of these as
send_price_list:
- "prices", "pricing", "price list", "pricelist", "price sheet", "rates"
- "send me your price list", "can I see everything you have", "what's your full list"
- "do you have a catalog", "what are all your prices", "what do you sell", "what do you have"
- "send pricing", "let me see prices", "list", "menu"

When you choose "send_price_list", the spreadsheet is sent on its own with absolutely NO text
message. You MUST leave reply_message empty (""). Do NOT write a summary, do NOT list popular
picks, do NOT say "here you go" — send nothing but the action. Any text here is a bug.

ONLY skip send_price_list when they ask about a SPECIFIC named product or a specific quote
(e.g. "how much is BPC-157?", "price on 10 kits of semaglutide?", "what's tirzepatide go for?").
Quote those directly in reply_message using the catalog above. When in doubt between a general
pricing request and a specific one, prefer send_price_list — sending the sheet is cheap and is
what most buyers want. Never reply with a chatty list of "popular picks" — if they want prices,
send the sheet.

FLOW:
1. Greet short and simple. Help them find what they need.
2. If they want prices / the price list / the catalog (even a one-word "prices"),
   use action "send_price_list" with an EMPTY reply_message — no text at all
3. If they ask about a specific named product, quote the list price per kit and total directly
4. If they push back on price, negotiate — move in increments, not all at once
5. When confirming the order, state shipping: standard $95 (FREE if product total
   over $1000), 4 weeks or less; or expedited $235, 10 days or less. Ask which.
6. Payment is crypto only — USDT or BTC. Ask which coin. Give the matching wallet
   address only after price + shipping are agreed. Ship after payment confirmed.
7. Once price, shipping, and payment coin are agreed, confirm full order details and place it

NOTE: in the JSON, "total_price" is the PRODUCT subtotal only (price per kit x kits),
BEFORE shipping. State the shipping fee and final total (product + shipping) in your
reply_message, but keep total_price as the product subtotal.

Keep replies short and choppy — this is WhatsApp, and you are a Chinese sales rep speaking
simple English. Round prices to 2 decimal places.
Always end with a JSON block:
{{
  "action": "collect" | "confirm" | "place" | "send_price_list" | "invalid",
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

# Canonical short messages that mean "send me the whole price list" with no
# other intent. Matched against the normalized (lowercased, punctuation-stripped)
# message so "Prices?", "price list", "send pricing" all hit. Kept deliberately
# tight — a product name in the message (e.g. "bpc price") will NOT match here and
# instead goes to Claude, which quotes it inline. This avoids spamming the sheet.
_PRICE_LIST_PHRASES = {
    "price", "prices", "pricing", "price list", "pricelist", "price sheet",
    "pricesheet", "price lists", "rates", "rate sheet", "catalog", "catalogue",
    "list", "menu", "price list please", "send price list", "send prices",
    "send pricing", "send me prices", "send me the price list",
    "send me your price list", "send me your full price list",
    "send me your prices", "full price list", "your price list",
    "can i see your prices", "can i get your price list", "whats your pricing",
    "what are your prices", "let me see prices", "see prices", "price please",
    "prices please", "list please", "share price list", "share your price list",
}


def _is_price_list_request(body: str) -> bool:
    """True only when the whole message is essentially just a price-list ask."""
    import re
    normalized = re.sub(r"[^a-z0-9 ]", "", body.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized in _PRICE_LIST_PHRASES


def handle_inbound(from_phone: str, body: str, name: str = "") -> str:
    print(f"[MessagingAgent] Inbound from {from_phone}: {body!r}")

    if body.strip().upper() == "RESET":
        _conversations.pop(from_phone, None)
        _lead_stage.pop(from_phone, None)
        try:
            existing = airtable.find_lead_by_phone(from_phone)
            if existing:
                airtable.leads.delete(existing["id"])
                print(f"[MessagingAgent] Deleted Airtable lead for {from_phone}")
        except Exception as e:
            print(f"[MessagingAgent] Airtable delete failed (non-fatal): {e!r}")
        print(f"[MessagingAgent] Reset state for {from_phone}")
        return "Reset. You're a fresh lead — say hi to start over."

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

    # Deterministic fast-path: if the message is essentially JUST a request for
    # the price list / catalog, send the spreadsheet only — no text, no LLM
    # guesswork. This guarantees consistent behavior for the obvious case while
    # still letting Claude reason about specific products and ambiguous asks.
    if _is_price_list_request(body):
        try:
            _send_price_list(from_phone)
            print(f"[MessagingAgent] Fast-path price list send to {from_phone}")
        except Exception as e:
            print(f"[MessagingAgent] Fast-path _send_price_list crashed: {e!r}")
        conversation.append({"role": "assistant", "content": "[sent price list spreadsheet]"})
        save_conversation(from_phone, conversation)
        return ""  # empty reply → spreadsheet only, no text

    # Otherwise Claude decides whether to send the full price list (via the
    # "send_price_list" action) or quote a specific product inline.
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

    # Claude decided the buyer wants the full catalog — send the spreadsheet only, no text
    if action == "send_price_list":
        try:
            _send_price_list(phone)
            print(f"[MessagingAgent] Claude triggered price list send to {phone}")
        except Exception as e:
            print(f"[MessagingAgent] _send_price_list crashed: {e!r}")
        return ""  # empty reply → no text, just the document

    # ── Hard floor guardrail ──────────────────────────────────────────────
    # Regardless of what Claude quoted, never let an order be recorded below
    # the 3x-cost floor. This is the code backstop behind the prompt rules.
    if action in ("place", "confirm") and product and quantity_kits:
        floor_per_kit = get_floor_price(product, spec)
        if floor_per_kit is not None:
            floor_total = round(floor_per_kit * float(quantity_kits), 2)
            if float(total_price or 0) < floor_total - 0.01:
                print(f"[Guardrail] Below-floor quote blocked: {product} {spec} "
                      f"x{quantity_kits} @ ${total_price} < floor ${floor_total}")
                # Override the buyer-facing reply and do NOT place the order.
                return (f"Best price I can do: ${floor_per_kit:.2f} per kit. "
                        f"{int(quantity_kits)} kit = ${floor_total:.2f}, plus shipping. Okay for you?")
        else:
            print(f"[Guardrail] No floor match for {product!r} {spec!r} — allowing (cannot verify)")

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

# Public Railway URL — serves the bilingual XLSX price list as a downloadable file.
# WhatsApp shows the document name from the URL's last path segment, so we serve
# (and link to) a Chinese-named path: 北线集团研究肽价格表.xlsx
# ("Northline Group Research Peptide Price List").
from urllib.parse import quote as _urlquote

_BASE_URL = "https://peptide-agents-production.up.railway.app"
_CN_XLSX_FILENAME = "北线集团研究肽价格表.xlsx"
PRICE_LIST_XLSX_URL = f"{_BASE_URL}/{_urlquote(_CN_XLSX_FILENAME)}"


def _send_price_list(to: str) -> None:
    """
    Send the bilingual XLSX price list as a WhatsApp document attachment —
    no accompanying text, just the spreadsheet file. Recipients open it in
    Excel / Numbers / Sheets. Falls back to text only if the attachment fails.
    """
    from_number = settings.twilio_whatsapp_from if "whatsapp" in to else settings.twilio_phone_number
    print(f"[PriceList] Sending XLSX (no text) to {to!r} from {from_number!r} — {PRICE_LIST_XLSX_URL}")
    try:
        msg = twilio_client.messages.create(
            from_=from_number,
            to=to,
            media_url=[PRICE_LIST_XLSX_URL],
        )
        print(f"[PriceList] Sent OK: SID={msg.sid} status={msg.status}")
    except Exception as e:
        print(f"[PriceList] XLSX send failed: {e!r} — sending text fallback")
        _send_text_price_list(from_number, to)


def _send_text_price_list(from_number: str, to: str) -> None:
    """Last-resort fallback: send price list as plain-text messages."""
    fallback_msgs = get_price_list_messages()
    for m in fallback_msgs:
        try:
            twilio_client.messages.create(body=m, from_=from_number, to=to)
        except Exception as e2:
            print(f"[PriceList] Text fallback also failed: {e2!r}")


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

    reply = handle_inbound(from_phone, body, name=profile_name)

    twiml = MessagingResponse()
    if reply:
        twiml.message(reply)
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
