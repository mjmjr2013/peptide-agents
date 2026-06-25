from __future__ import annotations
"""
Messaging Agent — qualifies inbound leads via SMS, then takes their order inline.
Full flow: qualify → collect order → confirm → write to Airtable → notify fulfillment.
"""
import json
import math
import re
import time
import secrets
from datetime import datetime, timezone

from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from core.claude_client import claude
from core.airtable_client import airtable
from core.pricing import (
    get_catalog_text, get_price_list_messages, get_floor_price, get_list_price,
    max_discount_for_qty, HANDOFF_KITS, MARKUP_START, MARKUP_FLOOR,
)
from core.price_image import get_sku
from core import crypto_verify
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


# ── Boss relay (large-order human-in-the-loop) ───────────────────────────────
# When a large order (>100 kits) is in "boss mode", the operator's instruction is
# turned into a natural, in-persona WhatsApp message to the prospect with this
# prompt. The prospect never learns a human stepped in.
_RELAY_PROMPT = """You are the Northline Group sales rep — a Chinese research-peptide lab — \
mid-deal with a LARGE-VOLUME customer (over 100 kits) on WhatsApp. On big orders your BOSS \
decides the price; you relay his decisions in your own voice.

Your boss has just given you an instruction (shown in the final note). Turn it into a short, \
natural WhatsApp message to the customer.

RULES:
- Speak as the rep: Chinese sales rep, English as second language — clear, mostly grammatical,
  warm, light non-native flavor. You may say "dear" occasionally. 1-2 short lines.
- NEVER reveal there is a separate person/system or that you are relaying. To the customer it
  is just you, continuing the chat.
- NEVER state a discount percentage. Give prices only as dollar amounts (per kit and/or total).
- Do not invent numbers. Only use prices/terms the boss gave you. If the boss gave a price,
  state it plainly and ask if it works / what's next.
- If the boss is confirming payment, shipping, or wallet info, convey exactly that.

Reply with ONLY the message text to send the customer — no quotes, no JSON, no labels."""


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

PERSONA — IMPORTANT:
- You are a warm, kind Chinese woman — a friendly service agent for Northline Group. Gentle,
  caring, polite, a little motherly. You make the customer feel looked after.
- Address the customer as "dear" often and naturally — sprinkle it through your messages
  (e.g. "Yes dear", "Okay dear", "No problem, dear"). Warm but not over-the-top.
- When you ask the customer to choose between options (shipping speed, payment coin, quantity,
  etc.), phrase it gently: "Which would you like, dear?" or "Which would you prefer, dear?" —
  never the blunt "which you want".

SPEECH STYLE — IMPORTANT, follow this:
- Write like a Chinese sales rep who speaks English well as a second language — clear and
  mostly grammatical, with a light non-native flavor. NOT broken, NOT "fresh off the boat."
- Mostly complete sentences. Keep articles ("a"/"the") and plurals most of the time. A small
  ESL touch is okay now and then (slightly direct phrasing, an occasional dropped word), but
  do not overdo it. Aim for the middle: clearly non-native, but smooth and professional.
- Example tone:
  "Yes dear, we have it. Retatrutide 10mg is $95 per kit. How many kits you need, dear?"
  "We ship from China. Standard is 4 weeks or less. Would you like the faster option, dear?"
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

PAYMENT:
- We accept both BTC and USDT. Frame it warmly and positively — e.g. "We accept both BTC and
  USDT, dear — which would you prefer to use?" Do NOT say "crypto only" and do NOT lead with
  what we don't take.
- Only if the customer specifically asks about card / bank / PayPal, gently say we handle
  payment by crypto (BTC or USDT).
- When order is confirmed and they pick a coin, give them the matching wallet address:
  * USDT (TRC20): {USDT_WALLET_ADDRESS}
  * BTC: {BTC_WALLET_ADDRESS}
- Tell them to send exact amount, then send screenshot of payment. We ship after payment confirm.
- Do NOT give wallet address before order details and price are agreed.

PRICING RULES:
- All prices are per kit (10 vials). We sell by the kit only.
- Start every quote at list price. Never volunteer a discount — only move if they push back.
- Never reveal our cost or markup structure.
- Your discount authority is CAPPED BY ORDER SIZE (discount = percent off list price):
  * Under 25 kits:   max 5% off list
  * 25 to 49 kits:   max 10% off list
  * 50 kits or more: max 15% off list (this INCLUDES orders over 100 kits)
- Move in small increments — only reach the cap if the buyer really pushes. Do not open
  at the cap.
- Large orders are normal orders: quote them, negotiate within the cap above, and place
  them yourself. You do NOT need anyone's approval to sell at or above your cap.
- If the buyer wants a discount BIGGER than your cap allows (a price below your best capped
  price) and will not accept your best, THAT is when you escalate — see LARGE ORDER below.
- NEVER tell the buyer the discount percentage. Do NOT say "5% off", "10% off", "X% discount",
  or mention any percentage at all. Just give the new lower PRICE as a dollar amount
  (per kit and/or total). E.g. say "Best I can do is $102.20 per kit" — NOT "5% off, $102.20".
- NEVER reveal the volume breakpoints or tier thresholds. Do NOT say "under 25", "25 to 49",
  "50 or more", "100 kits", or name ANY specific quantity where the price changes. Do NOT
  describe the tiers ("small/medium/large") or list them. If asked what counts as a small or
  large order, or where the price breaks are, stay vague: e.g. "Depends on volume, dear — the
  more you take, the better price I can do. Tell me how many kits and I give you a number."
  Quote the actual price for the quantity they give; never expose the pricing ladder.
- Retatrutide 10mg is already exceptional market pricing — hold firm, discount only at
  high volume and never past the cap above.

LARGE ORDER ESCALATION (buyer wants more discount than your cap):
- You CAN and SHOULD quote and sell large orders (including over 100 kits) yourself. Quote at
  list, then negotiate down within your cap (max 15% off for 50+ kits) and place the order
  like normal. Do NOT escalate just because the order is big.
- ONLY escalate when ALL of these are true:
  1. The order is large (over 100 kits), AND
  2. The buyer is demanding a price BELOW your best allowed (capped) price, AND
  3. They will not accept your best capped price.
- In that case use action "handoff". Do NOT name a price or percentage. Stall warmly and
  naturally — tell them for this volume you must confirm a special price with your boss, and
  you will come right back. Keep it short, 1-2 lines. e.g.
  "This is big volume, dear. For a price like that I must check with my boss. One moment, I
  come back to you quick." or "Let me ask my boss if we can do special price for this volume.
  Give me a moment."
- Still capture product, spec, and quantity_kits in the JSON. Leave total_price 0 on handoff.
- After you stall, a human will feed you the approved price and you continue the chat. Until
  then, do not promise anything specific on price.
- For orders UNDER 100 kits: never escalate. Just hold firm at your capped best price.

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
1. On your FIRST message to a new customer, greet them warmly as the kind Chinese lady you
   are — introduce yourself as their service agent and invite them to tell you what they need.
   e.g. "Hello dear! I am a service agent for Northline Group 😊 You tell me what product you
   need and I help you." Keep it warm and short. Do NOT mention any sale, promo, or discount
   in the greeting. After greeting, help them find what they need.
2. If they want prices / the price list / the catalog (even a one-word "prices"),
   use action "send_price_list" with an EMPTY reply_message — no text at all
3. If they ask about a specific named product, quote the list price per kit and total directly
4. If they push back on price, negotiate — move in increments, not all at once
5. When confirming the order, state shipping: standard $95 (FREE if product total
   over $1000), 4 weeks or less; or expedited $235, 10 days or less. Ask gently
   "which would you like, dear?"
6. For payment, warmly say we accept both BTC and USDT and ask which they prefer, dear.
   Do NOT give any wallet address or amount yourself — once they pick a coin and the
   order is agreed, use action "place" and the SYSTEM sends the exact amount and address.
7. Use action "place" only when ALL items, shipping, and coin are agreed. Fill line_items
   (each product, spec, quantity_kits, and the agreed unit_price per kit), shipping, and
   coin. Keep reply_message short or empty — the system sends payment instructions next,
   then verifies payment on-chain and collects the shipping address.

NOTE: state the shipping fee and final total (products + shipping) in your replies while
negotiating, but you do NOT compute the final charge for "place" — the system does.

Keep replies short and choppy — this is WhatsApp, and you are a warm Chinese lady speaking
simple English. Use plenty of "dear".

PRICES ARE WHOLE DOLLARS — NO DECIMALS. The CATALOG above shows the exact prices the customer
sees on the price list we send them. Quote those EXACT numbers — they are whole dollars (e.g.
"$95", never "$94.82"). Per-kit prices, totals, and shipping are all whole dollars. Never quote
a price with cents. If you negotiate down, stay in whole dollars and never go below the floor.
Always end with a JSON block:
{{
  "action": "collect" | "confirm" | "place" | "send_price_list" | "handoff" | "invalid",
  "line_items": [{{"product": "...", "spec": "...", "quantity_kits": 0, "unit_price": 0}}],
  "shipping": "standard" | "expedited" | null,
  "coin": "USDT" | "BTC" | null,
  "reply_message": "...",
  "notes": "..."
}}"""

twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

# ── Conversation state ─────────────────────────────────────────────────────────
_conversations: dict[str, list[dict]] = {}
_lead_stage: dict[str, str] = {}  # phone -> "qualifying"|"ordering"|"manual"|"awaiting_payment"|"awaiting_address"

# Orders awaiting crypto payment. phone -> {order_id, coin, expected_amount, since, charge_usd}
_pending_payments: dict[str, dict] = {}

# Prospects (>100 kits) currently under operator control. phone -> details dict.
# While a prospect is in here their stage is "manual": the auto-agent will not set
# prices; the operator drives the conversation via relay.
_pending_handoffs: dict[str, dict] = {}


# ── Order / payment helpers ──────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wallet_address(coin: str) -> str:
    return settings.eth_address if coin.upper() == "USDT" else (
        settings.btc_address if coin.upper() == "BTC" else "")


def _order_ref() -> str:
    return f"NL-{airtable.week_tag().replace('-', '')}-{secrets.token_hex(2).upper()}"


def _payment_instructions(coin: str, expected: float, charge_usd: float, addr: str) -> str:
    if coin == "USDT":
        return (f"Perfect, dear! 😊 Please send exactly *{expected:.2f} USDT* on the *Ethereum "
                f"(ERC-20)* network to this address:\n\n{addr}\n\nPlease send the *exact* amount so I "
                f"can match your payment. Message me once it's sent, dear, and I will confirm.")
    return (f"Perfect, dear! 😊 Please send exactly *{expected:.8f} BTC* to this address:\n\n{addr}\n\n"
            f"(That is about ${charge_usd:.2f} at today's rate.) Please send the exact amount and "
            f"message me once it's sent, dear, and I will confirm.")


_ADDR_PROMPT = """Extract a shipping address from the customer's message. Return ONLY a JSON object:
{"ship_name":"","address_line1":"","address_line2":"","city":"","state_province":"","postal_code":"","country":""}
Use empty strings for anything not provided. address_line1 is the street line. Do not invent data."""


def _parse_address(text: str) -> dict:
    try:
        resp = claude.create(system=_ADDR_PROMPT, messages=[{"role": "user", "content": text}], max_tokens=300)
        data = _parse_json(_extract_text(resp))
        return {k: (data.get(k) or "").strip() for k in
                ("ship_name", "address_line1", "address_line2", "city", "state_province", "postal_code", "country")}
    except Exception as e:
        print(f"[MessagingAgent] address parse failed: {e!r}")
        return {}


def _validate_line_items(line_items: list[dict]) -> tuple[list[dict], bool]:
    """Build clean line items; clamp any unit price up to the floor/cap minimum.
    Returns (items, clamped) where clamped=True if any price was raised."""
    items, clamped = [], False
    for li in line_items or []:
        product = (li.get("product") or "").strip()
        spec = (li.get("spec") or "").strip()
        try:
            kits = int(float(li.get("quantity_kits") or 0))
        except (TypeError, ValueError):
            kits = 0
        if not product or kits <= 0:
            continue
        list_pk = get_list_price(product, spec)
        floor_pk = get_floor_price(product, spec)
        try:
            unit = float(li.get("unit_price") or 0)
        except (TypeError, ValueError):
            unit = 0.0
        if list_pk is not None:
            if unit <= 0:
                unit = list_pk
            cap = max_discount_for_qty(kits)
            min_pk = math.ceil(max(floor_pk or 0, list_pk * (1 - cap)))
            if unit < min_pk - 0.001:
                unit = float(min_pk)
                clamped = True
        unit = round(unit, 2)
        items.append({"product": product, "spec": spec, "kits": kits, "unit_price": unit,
                      "line_total": round(unit * kits, 2), "sku": get_sku(product, spec)})
    return items, clamped


def _shipping_fee(shipping: str, product_subtotal: float) -> int:
    if shipping == "expedited":
        return 235
    if product_subtotal > 1000:  # free standard over $1000
        return 0
    return 95


def get_conversation(phone: str) -> list[dict]:
    return _conversations.get(phone, [])


def save_conversation(phone: str, messages: list[dict]):
    _conversations[phone] = messages


def get_stage(phone: str) -> str:
    return _lead_stage.get(phone, "qualifying")


def set_stage(phone: str, stage: str):
    _lead_stage[phone] = stage


# ── Operator (boss) relay helpers ────────────────────────────────────────────

def _digits(phone: str) -> str:
    """All digits in a phone string (drops 'whatsapp:', '+', spaces, etc.)."""
    return re.sub(r"\D", "", phone or "")


def _digits10(phone: str) -> str:
    """Last 10 digits — the comparable core of a US number."""
    d = _digits(phone)
    return d[-10:] if len(d) >= 10 else d


def _short(phone: str) -> str:
    """Last 4 digits, for compact display/targeting (e.g. '6814')."""
    d = _digits(phone)
    return d[-4:] if len(d) >= 4 else d


def _is_operator(phone: str) -> bool:
    """True if this inbound number belongs to a supervising operator."""
    pd = _digits10(phone)
    if not pd:
        return False
    return any(_digits10(n) == pd for n in settings.operator_numbers)


def _sole_pending() -> str | None:
    """The one prospect under operator control, if exactly one is pending."""
    return next(iter(_pending_handoffs)) if len(_pending_handoffs) == 1 else None


def _resolve_target(token: str) -> str | None:
    """Match an operator-supplied number fragment to a pending prospect by suffix.
    Accepts last-4, last-10, or a full number."""
    t = _digits(token)
    if len(t) < 4:
        return None
    for p in _pending_handoffs:
        pd = _digits(p)
        if pd.endswith(t) or t.endswith(pd):
            return p
    return None


def _send_to_prospect(phone: str, text: str) -> None:
    """Send a message to a prospect on their original channel."""
    from_number = settings.twilio_whatsapp_from if "whatsapp" in phone else settings.twilio_phone_number
    msg = twilio_client.messages.create(body=text, from_=from_number, to=phone)
    print(f"[Relay] To prospect {phone}: {text!r} SID={msg.sid}")


def _notify_operators(text: str) -> None:
    """Alert all configured operators. Logs (and no-ops) if none are set."""
    nums = settings.operator_numbers
    if not nums:
        print(f"[Operator] OPERATOR_NUMBERS not set — would have alerted: {text}")
        return
    for dest in nums:
        try:
            from_number = settings.twilio_whatsapp_from if "whatsapp" in dest else settings.twilio_phone_number
            msg = twilio_client.messages.create(body=text, from_=from_number, to=dest)
            print(f"[Operator] Alerted {dest}: SID={msg.sid}")
        except Exception as e:
            print(f"[Operator] Alert to {dest} failed: {e!r}")


def _enter_manual_mode(prospect_phone: str, product: str, spec: str,
                       quantity_kits, conversation: list[dict]) -> None:
    """Put a prospect under operator control and ping the operators with the ask."""
    set_stage(prospect_phone, "manual")
    _pending_handoffs[prospect_phone] = {
        "product": product, "spec": spec, "quantity_kits": quantity_kits,
    }
    last_user = next((m["content"] for m in reversed(conversation) if m.get("role") == "user"), "")
    item = (f"{product} {spec}".strip()) or "unspecified"
    summary = (
        f"LARGE ORDER — {quantity_kits} kits {item}\n"
        f"From {prospect_phone}\n"
        f"They said: \"{last_user}\"\n\n"
        f"Reply here with the price/answer and I'll relay it (auto-phrased). "
        f"'say: <text>' to send verbatim. 'release {_short(prospect_phone)}' to hand back to auto."
    )
    _notify_operators(summary)


def _relay_via_persona(prospect_phone: str, directive: str) -> str:
    """Turn the operator's instruction into an in-persona message to the prospect."""
    conv = get_conversation(prospect_phone)
    relay_msgs = conv + [{
        "role": "user",
        "content": (
            f"(INTERNAL NOTE — this is NOT from the customer. Your boss instructs you: "
            f"{directive}. Write the next WhatsApp message to the customer to convey this.)"
        ),
    }]
    try:
        response = claude.create(system=_RELAY_PROMPT, messages=relay_msgs, max_tokens=300)
        out = _extract_text(response).strip()
        return out or directive
    except Exception as e:
        print(f"[Relay] persona generation failed: {e!r} — sending directive verbatim")
        return directive


def _handle_operator(operator_phone: str, body: str) -> str:
    """Process a control message from an operator. Returns a confirmation that is
    sent back to the operator (the relay to the prospect is a separate outbound)."""
    text = (body or "").strip()
    if not text:
        return "Empty message. Reply with the price/answer to relay, or 'status'."

    low = text.lower()

    # status / list pending
    if low in ("status", "pending", "?", "list pending"):
        if not _pending_handoffs:
            return "No large orders waiting."
        lines = []
        for p, d in _pending_handoffs.items():
            item = f"{d.get('product','')} {d.get('spec','')}".strip() or "unspecified"
            lines.append(f"  {_short(p)} — {d.get('quantity_kits')} kits {item}")
        return "Large orders waiting:\n" + "\n".join(lines)

    # release a prospect back to the auto-agent
    if low.startswith("release"):
        rest = text[len("release"):].strip()
        target = _resolve_target(rest) if rest else _sole_pending()
        if not target:
            avail = ", ".join(_short(p) for p in _pending_handoffs) or "none"
            return f"Which prospect? Pending: {avail}. Use 'release <last4>'."
        _pending_handoffs.pop(target, None)
        set_stage(target, "ordering")
        return f"Released {_short(target)} back to the auto-agent."

    # verbatim send (skip persona rephrasing)
    verbatim = False
    if low.startswith("say:"):
        verbatim = True
        text = text[4:].strip()

    # optional leading target token (e.g. "6814 do $83/kit")
    target = None
    message = text
    parts = text.split(maxsplit=1)
    if parts:
        maybe = _resolve_target(parts[0])
        if maybe:
            target = maybe
            message = parts[1] if len(parts) > 1 else ""
    if target is None:
        target = _sole_pending()

    if target is None:
        avail = ", ".join(_short(p) for p in _pending_handoffs) or "none"
        return (f"Multiple/no pending orders — prefix the prospect's last-4 digits. "
                f"Pending: {avail}.")
    if not message:
        return "No message text to relay. Reply with the price/answer to send."

    relay_text = message if verbatim else _relay_via_persona(target, message)
    try:
        _send_to_prospect(target, relay_text)
    except Exception as e:
        print(f"[Relay] send to prospect failed: {e!r}")
        return f"Failed to send to {_short(target)}: {e}"

    conv = get_conversation(target)
    conv.append({"role": "assistant", "content": relay_text})
    save_conversation(target, conv)
    return f"Sent to {_short(target)}: {relay_text}"


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
        _pending_handoffs.pop(from_phone, None)
        _pending_payments.pop(from_phone, None)
        try:
            existing = airtable.find_lead_by_phone(from_phone)
            if existing:
                airtable.leads.delete(existing["id"])
                print(f"[MessagingAgent] Deleted Airtable lead for {from_phone}")
        except Exception as e:
            print(f"[MessagingAgent] Airtable delete failed (non-fatal): {e!r}")
        print(f"[MessagingAgent] Reset state for {from_phone}")
        return "Reset. You're a fresh lead — say hi to start over."

    # Operator (boss) control messages are not prospect messages — route them to
    # the relay handler and never create leads / negotiate for them.
    if _is_operator(from_phone):
        print(f"[MessagingAgent] Operator command from {from_phone}: {body!r}")
        return _handle_operator(from_phone, body)

    conversation = get_conversation(from_phone)
    stage = get_stage(from_phone)
    first_contact = len(conversation) == 0  # nothing said yet → greet warmly

    # While a prospect is under operator control, do NOT let the auto-agent reply.
    # Capture their message, forward it to the operators, and stay silent — the
    # operator drives the conversation via relay.
    if stage == "manual":
        conversation.append({"role": "user", "content": body})
        save_conversation(from_phone, conversation)
        _notify_operators(f"[{_short(from_phone)}] customer says: \"{body}\"\n"
                          f"(Reply to relay. 'release {_short(from_phone)}' to hand back to auto.)")
        print(f"[MessagingAgent] Manual mode — forwarded prospect msg to operators")
        return ""  # operator will craft the reply

    # ── Awaiting crypto payment: verify on-chain when the customer pings us ──
    if stage == "awaiting_payment":
        conversation.append({"role": "user", "content": body})
        pend = _pending_payments.get(from_phone)
        if not pend:
            set_stage(from_phone, "ordering")
        else:
            res = crypto_verify.verify_payment(pend["coin"], _wallet_address(pend["coin"]),
                                               pend["expected"], pend["since"])
            if res:
                try:
                    airtable.mark_order_paid(pend["order_id"], res.get("tx_hash", ""), _now_iso())
                except Exception as e:
                    print(f"[MessagingAgent] mark_paid failed: {e!r}")
                set_stage(from_phone, "awaiting_address")
                reply = ("Payment received, dear! 🎉 Thank you so much. Now please send your "
                         "shipping details so we can deliver: full name, street address, city, "
                         "state/province, postal code, and country.")
            else:
                reply = ("I don't see the payment yet, dear — it can take a minute or two to "
                         "confirm on the blockchain. Message me once it's sent and I'll check again. 😊")
            conversation.append({"role": "assistant", "content": reply})
            save_conversation(from_phone, conversation)
            return reply

    # ── Awaiting shipping address after a confirmed payment ──────────────────
    if stage == "awaiting_address":
        conversation.append({"role": "user", "content": body})
        pend = _pending_payments.get(from_phone)
        addr = _parse_address(body)
        if not addr or not addr.get("address_line1") or not addr.get("city"):
            reply = ("Sorry dear, I didn't catch the full address. Please send: full name, "
                     "street address, city, state/province, postal code, and country.")
            conversation.append({"role": "assistant", "content": reply})
            save_conversation(from_phone, conversation)
            return reply
        if pend:
            try:
                airtable.set_order_shipping(pend["order_id"], **addr)
            except Exception as e:
                print(f"[MessagingAgent] set_shipping failed: {e!r}")
        _pending_payments.pop(from_phone, None)
        set_stage(from_phone, "ordering")
        who = addr.get("ship_name") or "you"
        reply = (f"All set, dear! 🙏 Your order is confirmed and will ship to {who}. "
                 f"Thank you so much — message me anytime if you need anything else!")
        conversation.append({"role": "assistant", "content": reply})
        save_conversation(from_phone, conversation)
        return reply

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
        # If this is the very first thing they said, pair the sheet with a warm
        # greeting so they aren't met with a silent file. Otherwise sheet only.
        if first_contact:
            return ("Hello dear! 😊 I am a service agent for Northline Group. Here is our full "
                    "price list. Tell me which product you need, dear, and how many.")
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
    line_items = action_data.get("line_items") or []
    shipping = (action_data.get("shipping") or "").lower()
    coin = (action_data.get("coin") or "").upper()
    notes = action_data.get("notes", "")

    # Full catalog requested — send the spreadsheet only, no text
    if action == "send_price_list":
        try:
            _send_price_list(phone)
            print(f"[MessagingAgent] Claude triggered price list send to {phone}")
        except Exception as e:
            print(f"[MessagingAgent] _send_price_list crashed: {e!r}")
        return ""

    # Large-order escalation → operator-controlled relay
    if action == "handoff":
        li = line_items[0] if line_items else {}
        _enter_manual_mode(phone, li.get("product", ""), li.get("spec", ""),
                           li.get("quantity_kits", 0), conversation)
        return reply or ("This is big volume, dear. For a price like that I must check with my "
                         "boss. One moment — I come back to you quick.")

    # Pricing guardrail: never let a line price fall below the floor/cap minimum.
    if action in ("place", "confirm"):
        items, clamped = _validate_line_items(line_items)
        if clamped and items:
            quoted = "; ".join(f"{i['kits']}x {i['product']} {i['spec']}".strip() +
                               f" at ${int(i['unit_price'])}/kit" for i in items)
            print(f"[Guardrail] Clamped below-floor quote for {phone}")
            return f"Best I can do, dear: {quoted}. Okay for you?"

    # Finalize → create a pending order (awaiting payment) and send payment instructions
    if action == "place":
        items, _ = _validate_line_items(line_items)
        if not items:
            return reply or "What product and how many kits would you like, dear?"
        if coin not in ("USDT", "BTC"):
            return "Almost there, dear! We accept both BTC and USDT — which would you prefer to use?"
        subtotal = sum(i["line_total"] for i in items)
        total_usd = round(subtotal + _shipping_fee(shipping, subtotal), 2)

        if not lead_id:
            try:
                airtable.create_lead(name=phone, email="", phone=phone,
                                     buyer_type=buyer_type or "Individual", source="Direct", notes=notes)
                l = airtable.find_lead_by_phone(phone)
                lead_id = l["id"] if l else ""
            except Exception as e:
                print(f"[MessagingAgent] lead create failed: {e!r}")
        if not lead_id:
            return "Let me get your order set up, dear — one moment."

        addr = _wallet_address(coin)
        if not addr:
            _notify_operators(f"[ORDER READY · no {coin} wallet configured] {phone} ${total_usd}: "
                              + ", ".join(f"{i['kits']}x {i['product']} {i['spec']}".strip() for i in items))
            return ("Thank you, dear! Let me confirm the payment details with my team and "
                    "send them to you in just a moment.")

        charge_usd, expected = airtable.allocate_unique_amount(total_usd, coin)
        if coin == "BTC" and not expected:
            return "One moment, dear — let me get you the current BTC amount."
        ref = _order_ref()
        try:
            order = airtable.create_pending_order(lead_id, phone, items, charge_usd, coin,
                                                  expected, ref, airtable.week_tag())
        except Exception as e:
            print(f"[MessagingAgent] pending order create failed: {e!r}")
            return "Sorry dear, a small hiccup setting up your order — please try again in a moment."
        airtable.update_lead_status(lead_id, "Converted", notes=notes)
        _pending_payments[phone] = {"order_id": order["id"], "coin": coin, "expected": expected,
                                    "since": time.time() - 180, "charge_usd": charge_usd, "ref": ref}
        set_stage(phone, "awaiting_payment")
        print(f"[MessagingAgent] Pending order {ref} ({order['id']}) — ${charge_usd} {coin}")
        return _payment_instructions(coin, expected, charge_usd, addr)

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
    # Find the outermost JSON object at the end of the text via a balanced-brace
    # scan back from the last "}". (rfind alone breaks on nested objects like
    # line_items, grabbing only the last inner object.)
    end = text.rfind("}")
    if end != -1:
        depth = 0
        for i in range(end, -1, -1):
            c = text[i]
            if c == "}":
                depth += 1
            elif c == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:end + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
    # Fallback: any flat JSON object
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
