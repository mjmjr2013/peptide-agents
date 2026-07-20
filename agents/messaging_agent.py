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
from datetime import datetime, timedelta, timezone

from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from core.claude_client import claude
from core.airtable_client import airtable
from core.pricing import (
    get_catalog_text, get_price_list_messages, get_floor_price, get_list_price,
    max_discount_for_qty, HANDOFF_KITS, MARKUP_START, MARKUP_FLOOR,
)
from core.price_image import get_sku
from core.proof_media import get_media_catalog_text
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
# Crypto-only payment. USDT (Ethereum/ERC-20, received at settings.eth_address) or BTC
# (settings.btc_address). The agent never hands out an address; the system code generates
# exact payment instructions (unique amount + address) after an order is placed — see
# _payment_instructions / _wallet_address.


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
    media_catalog = get_media_catalog_text()
    proof_section = (
        "PROOF / LEGITIMACY MEDIA — you can send real photos/videos of our lab and product:\n"
        "- When a prospect asks for proof we are legit / a real lab, asks to SEE the product, the\n"
        "  lab, the vials, or wants pictures/video before they trust us, you may send one of the\n"
        "  assets below. Use action \"send_media\" with \"media_key\" set to the BEST-FITTING asset\n"
        "  for what they asked. Use your judgement — match the asset's description to their request\n"
        "  (e.g. they doubt we are a real lab → a lab video; they want to see the product → a vial\n"
        "  photo). Put a short warm caption in reply_message (it is sent WITH the media).\n"
        "- Send at most ONE asset per request. Do not spam media. If nothing fits well, just answer\n"
        "  warmly in words instead.\n"
        "- Send each asset only ONCE per conversation. If the history shows you already sent a proof\n"
        "  clip/photo (a line like '[sent proof video/photo: ...]'), do NOT send it again — just\n"
        "  reply in words. Never send the same video twice.\n"
        "Available assets (media_key — description):\n" + media_catalog + "\n\n"
    ) if media_catalog else ""
    return f"""You are a sales representative for Northline Group, a research peptide LAB in China.
We are the lab — the manufacturer. We make and ship the product ourselves, direct from China.

WHO WE ARE — say this clearly when relevant:
- We are Chinese. We are the lab in China. You speak as a Chinese sales rep.
- We ship everything from China. We do NOT have US-based fulfillment. No US warehouse.
- We rarely do business direct with Americans. If buyer is American, it is fine, but be a
  little reserved — we mostly supply labs and distributors, not US individuals. Do not be
  pushy with American individual buyers; keep it businesslike.

PERSONA — IMPORTANT:
- Your name is Lily. You are a warm, kind Chinese woman who works in sales at Northline Group.
  Gentle, caring, polite, a little motherly. You make the customer feel looked after.
- You are a REAL PERSON, not a bot, assistant, AI, or "agent". NEVER call yourself an "agent",
  "service agent", "assistant", "bot", or "AI", and never say you are automated. If a customer
  asks if you are a robot/AI, deflect warmly and humanly ("Haha no dear, it is just me, Lily 😊").
  If you introduce yourself at all, do it by name: "I am Lily from Northline Group".
- Address the customer as "dear" often and naturally — sprinkle it through your messages
  (e.g. "Yes dear", "Okay dear", "No problem, dear"). Warm but not over-the-top.
- ALMOST EVERY question you ask should end with "dear" — make it a habit. e.g.
  "What are you looking for, dear?", "How many kits, dear?", "Which would you prefer, dear?",
  "Is that everything, dear?". A bare question with no "dear" should be rare. But do not stack
  it twice in one short message — once is enough.
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
- TRACKING: tell the customer they will receive their tracking number within 1-3 days
  from the date the order is placed. Mention this warmly when confirming the order or
  after they pay, e.g. "You get your tracking number within 1-3 days, dear."

PAYMENT:
- We accept both BTC and USDT. Frame it warmly and positively — e.g. "We accept both BTC and
  USDT, dear — which would you prefer to use?" Do NOT say "crypto only" and do NOT lead with
  what we don't take.
- Only if the customer specifically asks about card / bank / PayPal, gently say we handle
  payment by crypto (BTC or USDT).
- When ALL items, shipping, and the coin are agreed, use action "place". Do NOT give the
  wallet address or the amount yourself — the system sends exact payment instructions
  (amount + address) automatically once you place the order. USDT is received on the
  Ethereum (ERC-20) network; BTC on Bitcoin.
- After they pay they message you; the system verifies the payment on-chain and then asks
  for the shipping address. We ship after payment is confirmed.

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

UNDERSTANDING PRODUCT REQUESTS — read carefully, customers describe peptides loosely:
- Match the customer's words to the EXACT catalog product. Only ever put on the order what the
  customer actually asked for — NEVER add extra products, quantities, or specs they did not
  request. If your draft order has items they never mentioned, that is a bug — remove them.
- CJC-1295 comes in two forms, and they are DIFFERENT products:
  * "CJC-1295 (no DAC)" — also called "no dac", "without dac", "mod grf", "modified GRF 1-29".
  * "CJC-1295 (with DAC)" — "with dac", "dac".
  If a customer says "no dac" they mean the no-DAC version — pick that exact product. If they
  don't specify DAC, ask gently which they want, dear.
- "CJC+Ipamorelin Blend" is its OWN single product (CJC-1295 and Ipamorelin pre-mixed together).
  When a customer asks for "the cjc/ipamorelin blend" or "cjc 1295 / ipamorelin together", that
  is the CJC+Ipamorelin Blend — do NOT quote CJC-1295 and Ipamorelin as two separate lines, and
  do NOT confuse it with plain "CJC-1295 (no DAC)" or plain "Ipamorelin". If they say the blend
  should be "no dac", that is fine — it is the no-DAC blend; just note "no DAC" in the spec.
- Specs are per-vial mg and we sell by the kit (10 vials). "5mg/5mg, 10mg total" describes the
  blend strength — match it to the closest catalog spec and confirm, e.g. "CJC+Ipamorelin Blend
  10mg, no DAC — how many kits, dear?".
- If a request is ambiguous or you are unsure which exact product they mean, ASK a short
  clarifying question (ending in "dear") rather than guessing or dumping a long list. Never
  invent a multi-product "full order" the customer did not ask for.

BIG MULTI-PRODUCT ORDERS — never drop items:
- A customer may order MANY products in one go (8, 10, or more line items). You MUST capture and
  list EVERY product they asked for — never drop, skip, shorten, or summarize the list. If the
  customer has named ten products, your order has ten lines.
- Carry the WHOLE running order forward. When you re-state or confirm the order (e.g. after they
  add or change something), include ALL previously agreed items PLUS the change — do not shorten
  to the first few. Re-read the whole conversation and rebuild the complete list each time.
- If a customer says "you forgot X" or "you shortened it again", that is a failure — apologize
  briefly and immediately give the COMPLETE list with every item, dear.
- Put every item in the JSON line_items array (one entry per product/spec), and make the
  reply_message list match the line_items exactly. The list in your message and the JSON must
  agree and include everything.

{proof_section}SENDING THE FULL PRICE LIST:
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
1. Greet a new customer warmly ONLY ONCE, on your very first message to them — introduce
   yourself by name and invite them to tell you what they need. e.g. "Hello dear! This is Lily
   from Northline Group 😊 What are you looking for today?" Vary the wording naturally; never
   send the same greeting twice. If you have ALREADY greeted them earlier in this conversation
   (there is any prior message from you above), do NOT greet again — just respond naturally to
   what they said, like a real person would (e.g. if they say "hello" again, "Yes dear, I'm
   here 😊 what can I get for you?"). Never mention any sale, promo, or discount in a greeting.
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
THINK BEFORE YOU REPLY:
- The JSON's FIRST field is "thinking" — a short PRIVATE scratchpad. It is NEVER sent to the
  customer. Use it to reason for a moment before you speak, exactly like a real salesperson
  would pause to think: What did the customer actually say/ask? Have I already greeted them
  (is there a prior message from me)? What is the natural, human next thing to say? Am I about
  to repeat myself or sound robotic — if so, say it differently. Are the price/quantity/shipping
  right? Then write "reply_message" based on that reasoning.
- Keep "thinking" to 1-3 short sentences. Always fill it in before "reply_message".
- "reply_message" must read like a real human typed it — natural, warm, never a canned or
  duplicated line. If your reply would be nearly identical to something you already said, change it.

Always end with a JSON block (fill "thinking" FIRST, then the rest):
{{
  "thinking": "private reasoning — never shown to the customer",
  "action": "collect" | "confirm" | "place" | "send_price_list" | "send_media" | "handoff" | "invalid",
  "line_items": [{{"product": "...", "spec": "...", "quantity_kits": 0, "unit_price": 0}}],
  "shipping": "standard" | "expedited" | null,
  "coin": "USDT" | "BTC" | null,
  "media_key": "... (only for action send_media: the key of the asset to send)",
  "reply_message": "...",
  "notes": "..."
}}"""

twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

# ── Conversation state ─────────────────────────────────────────────────────────
# Per-phone lock so two messages arriving in quick succession from the same number
# are processed one at a time (otherwise both can see an empty history and the agent
# greets twice). Plus the last outbound text per phone, to suppress duplicate replies.
import threading as _threading
_phone_locks: dict[str, _threading.Lock] = {}
_phone_locks_guard = _threading.Lock()
_last_outbound: dict[str, str] = {}
# Proof assets already sent to each prospect (phone -> set of media keys), so the
# agent never re-sends the same lab video/photo on later turns. Cleared on RESET.
_sent_media: dict[str, set] = {}
# Sentinel returned by _handle_ordering when it already sent media + recorded the
# turn in history itself — tells the caller not to append/send another message.
_MEDIA_SENT = "\x00__media_sent__"


def _lock_for(phone: str) -> _threading.Lock:
    with _phone_locks_guard:
        lk = _phone_locks.get(phone)
        if lk is None:
            lk = _phone_locks[phone] = _threading.Lock()
        return lk


def _norm(t: str) -> str:
    return " ".join((t or "").lower().split())


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
    """Instructions text WITHOUT the address — the address is sent as its own separate
    bare message right after (easy copy/paste; nothing else in that bubble)."""
    if coin == "USDT":
        return (f"Perfect, dear! 😊 Please send exactly *{expected:.2f} USDT* on the *Ethereum "
                f"(ERC-20)* network. If you send from Coinbase or another exchange, choose "
                f"*Ethereum* as the network (not Solana, Tron, or Base), dear. I will send the "
                f"wallet address in the next message by itself so you can copy it easily. Please "
                f"send the *exact* amount so I can match your payment, and after you send it, "
                f"please share a screenshot of the transaction with me, dear — then I will "
                f"confirm with our finance department.")
    return (f"Perfect, dear! 😊 Please send exactly *{expected:.8f} BTC* (about ${charge_usd:.2f} "
            f"at today's rate). I will send the wallet address in the next message by itself so "
            f"you can copy it easily, dear. Please send the exact amount, and after you send it, "
            f"please share a screenshot of the transaction with me — then I will confirm with "
            f"our finance department.")


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


# Cost guardrail: the whole history is re-sent to Claude on every reply, so an
# endlessly chatty prospect makes each message pricier than the last. Bound it.
# (Matches the ~30-row redeploy rebuild in handle_inbound — Lily keeps recent
# context; the full transcript stays in Airtable.)
_MAX_HISTORY = 40


def get_conversation(phone: str) -> list[dict]:
    conv = _conversations.get(phone, [])
    if len(conv) > _MAX_HISTORY:
        conv = conv[-_MAX_HISTORY:]
        while conv and conv[0].get("role") != "user":  # API needs a user turn first
            conv = conv[1:]
        _conversations[phone] = conv
    return conv


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
    airtable.log_message(phone, "outbound", text)  # operator-relayed reply → transcript
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
        _last_outbound.pop(from_phone, None)
        _sent_media.pop(from_phone, None)
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

    # ── Redeploy recovery: rebuild conversation memory from the Airtable transcript ──
    # In-memory history is wiped by every deploy, but every message is logged to the
    # Messages table. If we have no in-memory history for this phone, rebuild the last
    # ~30 messages so Lily keeps full context mid-conversation (no amnesia, no
    # re-greeting). Messages after the customer's last RESET only.
    if not conversation:
        try:
            rows = airtable.get_recent_messages_for_phone(from_phone, limit=30)
        except Exception as e:
            rows = []
            print(f"[MessagingAgent] transcript rebuild failed: {e!r}")
        cut = 0
        for i, r in enumerate(rows):
            f = r["fields"]
            if f.get("direction") == "inbound" and (f.get("body") or "").strip().upper() == "RESET":
                cut = i + 1
        rebuilt = []
        for r in rows[cut:]:
            f = r["fields"]
            role = "user" if f.get("direction") == "inbound" else "assistant"
            text = (f.get("body") or "").strip()
            if not text:
                continue
            if rebuilt and rebuilt[-1]["role"] == role:  # API needs alternating roles
                rebuilt[-1]["content"] += "\n" + text
            else:
                rebuilt.append({"role": role, "content": text})
        if rebuilt and rebuilt[0]["role"] == "assistant":
            rebuilt = rebuilt[1:]  # history must start with the customer
        if rebuilt:
            conversation = rebuilt
            save_conversation(from_phone, conversation)
            print(f"[MessagingAgent] Rebuilt {len(rebuilt)} messages for {from_phone} from transcript")

    first_contact = len(conversation) == 0  # nothing said yet → greet warmly

    # ── Redeploy recovery: paid order waiting on a shipping address ──
    # If a deploy landed between payment confirmation and address collection, the
    # in-memory stage is lost; re-enter awaiting_address so their address is parsed
    # into the order instead of being treated as ordinary chat.
    if stage not in ("awaiting_payment", "awaiting_address", "manual") and from_phone not in _pending_payments:
        try:
            _po = airtable.get_paid_order_awaiting_address_for_phone(from_phone)
        except Exception as e:
            _po = None
            print(f"[MessagingAgent] address-stage recovery lookup failed: {e!r}")
        if _po:
            _pending_payments[from_phone] = {
                "order_id": _po["id"], "coin": (_po["fields"].get("coin") or "").upper(),
                "expected": 0.0, "since": 0.0,
                "charge_usd": float(_po["fields"].get("total_price") or 0),
                "ref": _po["fields"].get("order_ref", ""),
            }
            set_stage(from_phone, "awaiting_address")
            stage = "awaiting_address"
            print(f"[MessagingAgent] Recovered awaiting-address order {_po['fields'].get('order_ref')} for {from_phone}")

    # Recover in-flight payment state after a redeploy. In-memory _pending_payments /
    # stage are volatile — a deploy wipes them and would otherwise STRAND a customer
    # who already paid (their pings would be treated as a new chat, order never
    # verified). If Airtable still shows an awaiting order for this phone, re-enter
    # awaiting_payment so their payment is verified normally.
    if stage not in ("awaiting_payment", "awaiting_address", "manual") and from_phone not in _pending_payments:
        try:
            _ao = airtable.get_awaiting_order_for_phone(from_phone)
        except Exception as e:
            _ao = None
            print(f"[MessagingAgent] awaiting-order recovery lookup failed: {e!r}")
        if _ao:
            f = _ao["fields"]
            _pending_payments[from_phone] = {
                "order_id": _ao["id"], "coin": (f.get("coin") or "").upper(),
                "expected": float(f.get("expected_amount") or 0),
                "since": time.time() - 7 * 86400,  # wide window; matching is by amount
                "charge_usd": float(f.get("total_price") or 0), "ref": f.get("order_ref", ""),
            }
            set_stage(from_phone, "awaiting_payment")
            stage = "awaiting_payment"
            print(f"[MessagingAgent] Recovered awaiting order {f.get('order_ref')} for {from_phone} from Airtable")

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

    # ── Awaiting crypto payment: "finance department" verification flow ──────
    # Per Daniel: on the customer's "I paid" ping, reply IMMEDIATELY with a human
    # "let me verify with the finance department" beat, then verify on-chain in the
    # background and send the result proactively ~30–60s later.
    if stage == "awaiting_payment":
        conversation.append({"role": "user", "content": body})
        pend = _pending_payments.get(from_phone)
        if not pend:
            set_stage(from_phone, "ordering")
        elif not pend.get("verifying"):
            pend["verifying"] = True
            save_conversation(from_phone, conversation)

            def _verify_later(phone=from_phone, pend=pend):
                time.sleep(40)  # the human "checking with finance" beat
                try:
                    # Other awaiting orders' unique amounts (same coin) — an overpaid tx
                    # must never be claimable by the wrong order.
                    try:
                        others = [float(o["fields"].get("expected_amount") or 0)
                                  for o in airtable.get_awaiting_orders()
                                  if o["id"] != pend["order_id"]
                                  and (o["fields"].get("coin") or "").upper() == pend["coin"].upper()]
                    except Exception:
                        others = []
                    res = crypto_verify.verify_payment(pend["coin"], _wallet_address(pend["coin"]),
                                                       pend["expected"], pend["since"],
                                                       other_amounts=others)
                    if res:
                        try:
                            airtable.mark_order_paid(pend["order_id"], res.get("tx_hash", ""), _now_iso())
                        except Exception as e:
                            print(f"[MessagingAgent] mark_paid failed: {e!r}")
                        set_stage(phone, "awaiting_address")
                        msg = ("Okay dear, finance has confirmed — payment received! 🎉 Thank you so "
                               "much. Now please send your shipping details so we can deliver: full "
                               "name, street address, city, state/province, postal code, and country.")
                    else:
                        is_btc = pend["coin"].upper() == "BTC"
                        msg = (("Dear, finance doesn't see it on the network just yet — BTC usually "
                                "needs one confirmation, which can take 10–30 minutes. Nothing is "
                                "wrong; message me in a little while and I will check again. 🙏")
                               if is_btc else
                               ("Dear, finance doesn't see it just yet — it can take a few minutes to "
                                "confirm. Message me shortly and I will check again. 🙏"))
                    conv = get_conversation(phone)
                    conv.append({"role": "assistant", "content": msg})
                    save_conversation(phone, conv)
                    _send_to_prospect(phone, msg)
                except Exception as e:
                    print(f"[MessagingAgent] background verify failed: {e!r}")
                finally:
                    pend["verifying"] = False

            import threading
            threading.Thread(target=_verify_later, daemon=True).start()
            reply = "Okay dear, wait one moment while I verify with the finance department 😊"
            conversation.append({"role": "assistant", "content": reply})
            save_conversation(from_phone, conversation)
            return reply
        else:
            # A verification is already in flight — reassure with VARIED messages so
            # repeated pings never get silence or a robotic repeat.
            pend["checks"] = pend.get("checks", 0) + 1
            waits = [
                "One moment, dear — I am still with the finance department confirming it. I will "
                "message you the second they finish. 😊",
                "Still checking with finance, dear — I have not gone anywhere, I promise. 🙏",
                "Almost done, dear 💛 Finance is just confirming it on the network. I will tell you "
                "the moment it clears.",
            ]
            reply = waits[(pend["checks"] - 1) % len(waits)]
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
                 f"You will receive your tracking number within 1-3 days from today. "
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
            return ("Hello dear! This is Lily from Northline Group 😊 Here is our full price "
                    "list. Tell me which product you need, dear, and how many.")
        return ""  # empty reply → spreadsheet only, no text

    # Otherwise Claude decides whether to send the full price list (via the
    # "send_price_list" action) or quote a specific product inline.
    reply = _handle_ordering(from_phone, conversation, existing_lead)

    # _handle_ordering already recorded its turn (proof media sent) — send nothing more.
    if reply == _MEDIA_SENT:
        return ""

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
        max_tokens=1024,
    )

    response_text = _extract_text(response)
    action_data = _parse_json(response_text)
    reply = action_data.get("reply_message", "Thanks for reaching out, dear! Who am I speaking with?")
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

    # Generous ceiling: adaptive thinking tokens + a long multi-item order JSON must
    # both fit, or the line_items list gets truncated and products silently drop.
    response = claude.create(
        system=_build_order_prompt() + buyer_context,
        messages=conversation,
        max_tokens=2048,
    )

    response_text = _extract_text(response)
    action_data = _parse_json(response_text)
    reply = action_data.get("reply_message", "What product are you looking for, dear, and how many kits?")
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

    # Proof/legitimacy media requested — send the chosen lab video or photo ONCE.
    if action == "send_media":
        key = (action_data.get("media_key", "") or "").strip()
        already = _sent_media.get(phone, set())
        # Never re-send an asset this prospect already got, and don't flood with more
        # than 2 proof clips in one conversation — answer in words instead. (Returning
        # a normal text reply; the caller appends + sends it.)
        if key in already or len(already) >= 2:
            return reply or "I already shared that with you, dear 😊 What would you like to order?"
        sent = _send_proof_media(phone, key, caption=reply)
        if not sent:
            return reply or ("Of course, dear — we are a real lab in China. Let me get "
                             "something to show you, one moment 😊")
        _sent_media.setdefault(phone, set()).add(key)
        # Record the send in history so the model knows it already sent this (prevents
        # the re-send loop), then signal the caller to NOT append again.
        conversation.append({"role": "assistant", "content": f"[sent proof video/photo: {key}]"})
        save_conversation(phone, conversation)
        return _MEDIA_SENT

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
        # Send the wallet address as its OWN bare message (nothing else in the bubble)
        # so the customer can long-press → copy cleanly. Delayed a beat so it arrives
        # AFTER the instructions reply below.
        def _send_bare_address():
            time.sleep(2)
            try:
                _send_to_prospect(phone, addr)
            except Exception as e:
                print(f"[MessagingAgent] bare-address send failed: {e!r}")
        import threading
        threading.Thread(target=_send_bare_address, daemon=True).start()
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
    return {"action": "continue", "reply_message": "What product are you looking for, dear, and how many kits?"}


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


def _send_proof_media(to: str, key: str, caption: str = "") -> bool:
    """Send a proof/legitimacy asset (lab video or product photo) to a prospect as
    a WhatsApp media attachment. Returns True if a media message was sent."""
    from core.proof_media import get_media_by_key, PROOF_DIR
    entry = get_media_by_key(key)
    if not entry:
        print(f"[Proof] No proof asset for key {key!r} — skipping")
        return False
    from_number = settings.twilio_whatsapp_from if "whatsapp" in to else settings.twilio_phone_number
    url = f"{_BASE_URL}/proof/{_urlquote(entry['file'])}"
    try:
        msg = twilio_client.messages.create(
            from_=from_number, to=to, media_url=[url],
            body=(caption or None),
        )
        airtable.log_message(to, "outbound", f"📎 [sent proof {entry['type']}: {key}] {caption}".strip())
        print(f"[Proof] Sent {key!r} ({entry['file']}) to {to}: SID={msg.sid}")
        return True
    except Exception as e:
        print(f"[Proof] Send of {key!r} failed: {e!r}")
        return False


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


def _whatsapp_window_open(phone: str) -> bool:
    """True if the customer messaged us within ~24h — WhatsApp only delivers
    freeform business messages inside that window (error 63016 outside it).
    Judged from the durable Airtable transcript; on any doubt, assume CLOSED
    (the approved template is always deliverable, freeform is not)."""
    try:
        rows = airtable.get_recent_messages_for_phone(phone, limit=30)
        last_in = next((r["fields"].get("sent_at") for r in reversed(rows)
                        if r["fields"].get("direction") == "inbound"), None)
        if not last_in:
            return False
        dt = datetime.fromisoformat(last_in.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt < timedelta(hours=23, minutes=30)
    except Exception as e:
        print(f"[Window] check failed for {phone}: {e!r} — assuming closed")
        return False


def send_tracking_to_customer(phone: str, tracking: str, name: str = "") -> bool:
    """Text a customer their shipping tracking number in Lily's voice (WhatsApp).
    Uses the approved WhatsApp template when their 24h session window is closed.
    Returns True if sent. Best-effort — logs and returns False on failure."""
    if not phone or not tracking:
        return False
    from_number = settings.twilio_whatsapp_from if "whatsapp" in phone else settings.twilio_phone_number
    dear = f"{name}, " if name else ""
    body = (f"Wonderful news, {dear}dear! 🎉 Your shipment is booked and your tracking number is "
            f"*{tracking.strip()}*. It will show movement once the carrier scans it in — I will "
            f"also send you a photo of your vials before they go out. Thank you so much for your "
            f"order, dear — message me anytime! 😊")
    try:
        if _whatsapp_window_open(phone) or not settings.tracking_content_sid:
            msg = twilio_client.messages.create(body=body, from_=from_number, to=phone)
        else:
            msg = twilio_client.messages.create(
                content_sid=settings.tracking_content_sid,
                content_variables=json.dumps({"1": tracking.strip()}),
                from_=from_number, to=phone)
            body = f"[template] Your order has shipped. Tracking: {tracking.strip()}"
        airtable.log_message(phone, "outbound", body)  # transcript
        print(f"[Tracking] Sent tracking to {phone}: SID={msg.sid}")
        return True
    except Exception as e:
        print(f"[Tracking] Send to {phone} failed: {e!r}")
        return False


def send_vial_photo_to_customer(phone: str, media_url: str, name: str = "") -> bool:
    """WhatsApp a customer the photo of their packed vials in Lily's voice.
    Uses the approved WhatsApp media template when their 24h window is closed.
    Returns True if sent. Best-effort — logs and returns False on failure."""
    if not phone or not media_url:
        return False
    from_number = settings.twilio_whatsapp_from if "whatsapp" in phone else settings.twilio_phone_number
    dear = f"{name}, " if name else ""
    body = (f"Look, {dear}dear! 😊 Your vials are packed and ready — I wanted you to see them "
            f"before they ship. Everything is prepared with care. Thank you again for your "
            f"order, dear! 🙏")
    try:
        if _whatsapp_window_open(phone) or not settings.vial_content_sid:
            msg = twilio_client.messages.create(body=body, from_=from_number, to=phone,
                                                media_url=[media_url])
            airtable.log_message(phone, "outbound", body + " [sent vial photo]")
        else:
            msg = twilio_client.messages.create(
                content_sid=settings.vial_content_sid,
                content_variables=json.dumps({"1": media_url}),
                from_=from_number, to=phone)
            airtable.log_message(phone, "outbound", "[template] vial photo sent")
        print(f"[VialPhoto] Sent vial photo to {phone}: SID={msg.sid}")
        return True
    except Exception as e:
        print(f"[VialPhoto] Send to {phone} failed: {e!r}")
        return False


# Cost guardrail: cap paid Claude replies per prospect per day. A troll chatting
# with Lily all day would otherwise run an open-ended Anthropic bill (auto-reload
# would keep feeding it). Past the cap Lily sends a canned, time-aware excuse —
# "it's very late here" only when it actually IS late in China (persona lives in
# HK), a busy-day excuse otherwise — with NO Claude call, and ops get one alert
# email per phone per day. Real buyers close an order in 10–30 messages and never
# see this. Day boundary = China midnight, matching "tomorrow" in the night line.
from zoneinfo import ZoneInfo

_CHINA_TZ = ZoneInfo("Asia/Hong_Kong")
_daily_counts: dict[str, dict] = {}  # phone -> {"day", "n", "alerted"}

_CAP_NIGHT_REPLIES = [
    "So sorry dear, it is very late here now and I must sleep soon 😊 I will reply to "
    "you tomorrow when I am back, okay? Rest well! 🙏",
    "Dear, it is almost midnight here! 🌙 Let me continue with you tomorrow when I am "
    "fresh — I don't want to give you wrong information when I am sleepy 😊",
]
_CAP_DAY_REPLIES = [
    "So sorry dear, it is very busy at the warehouse today! 🙏 I will come back to you "
    "as soon as I am free, okay? Thank you for your patience 😊",
    "Dear, please give me a little time — many customers today! 😊 I will message you "
    "as soon as I can, I promise 🙏",
]


def _over_daily_cap(phone: str) -> bool:
    """Count this inbound; True once the prospect exceeds today's reply cap."""
    day = datetime.now(_CHINA_TZ).strftime("%Y-%m-%d")
    rec = _daily_counts.get(phone)
    if not rec or rec["day"] != day:
        rec = {"day": day, "n": 0, "alerted": False}
        _daily_counts[phone] = rec
    rec["n"] += 1
    if rec["n"] <= settings.agent_daily_msg_cap:
        return False
    if not rec["alerted"]:
        rec["alerted"] = True
        try:
            from agents.weekly_report import _send_email
            _send_email(f"NOTICE: prospect {phone} hit the daily message cap",
                        f"{phone} sent {rec['n']} messages today (cap "
                        f"{settings.agent_daily_msg_cap}). They are now getting canned "
                        f"'I'm busy/asleep, dear' replies instead of Claude-generated ones, "
                        f"so they cost ~nothing. The cap resets at midnight China time.\n\n"
                        f"If this is a legitimate big customer, just wait for the reset or "
                        f"raise AGENT_DAILY_MSG_CAP in Railway. If it's abuse, block the "
                        f"number in the Twilio console (Messaging → Settings → deny list).", [])
        except Exception as e:
            print(f"[Cap] alert email failed: {e!r}")
    return True


def _cap_reply() -> str:
    """A canned Lily excuse that matches the actual time of day in China."""
    hour = datetime.now(_CHINA_TZ).hour
    pool = _CAP_NIGHT_REPLIES if (hour >= 22 or hour < 7) else _CAP_DAY_REPLIES
    return secrets.choice(pool)


# If the brain is unreachable (Anthropic API outage / exhausted credits / any bug),
# the customer still gets a warm human answer instead of silence, and we return 200
# so Twilio doesn't retry and double-log the inbound. Ops get an emailed alert at
# most once per hour so the outage can't stay silent (that is how the exhausted-
# credits outage of 2026-07-08 went unnoticed until a live prospect was ghosted).
_FALLBACK_REPLIES = [
    "So sorry dear, give me just a little moment — I will be right back with you! 😊",
    "One moment please dear, I am just checking something for you — back very soon! 🙏",
    "Sorry to keep you waiting dear! I will get right back to you in a few minutes 😊",
]
_last_outage_alert = 0.0


def _alert_outage(err: str):
    global _last_outage_alert
    now = time.time()
    if now - _last_outage_alert < 3600:
        return
    _last_outage_alert = now
    try:
        from agents.weekly_report import _send_email
        _send_email("ALERT: Northline agent cannot reply to customers",
                    f"handle_inbound is raising — customers are getting the canned holding reply.\n\n"
                    f"Error: {err}\n\nIf this mentions credit balance, top up the Anthropic API "
                    f"account (console.anthropic.com → Plans & Billing).", [])
    except Exception as e:
        print(f"[MessagingAgent] outage alert email failed: {e!r}")


def twilio_webhook_handler(form_data: dict) -> str:
    from_phone = form_data.get("From", "")
    body = form_data.get("Body", "").strip()
    profile_name = form_data.get("ProfileName", "")

    # Transcript log (Airtable) — inbound. Best-effort; never blocks the reply.
    if body:
        airtable.log_message(from_phone, "inbound", body)

    # Serialize per-phone so rapid back-to-back messages are handled one at a time
    # (this alone prevents the double-greet race). We intentionally do NOT suppress
    # "duplicate" replies here: a customer waiting on payment may ping several times,
    # and each ping MUST get an answer — silencing repeats made the agent look like it
    # ghosted (and, to a paying customer, like a scam).
    with _lock_for(from_phone):
        # Daily cost cap (skip for RESET and operator control messages)
        if (body.strip().upper() != "RESET" and not _is_operator(from_phone)
                and _over_daily_cap(from_phone)):
            reply = _cap_reply()
        else:
            try:
                reply = handle_inbound(from_phone, body, name=profile_name)
            except Exception as e:
                import traceback
                traceback.print_exc()
                _alert_outage(repr(e))
                reply = secrets.choice(_FALLBACK_REPLIES)

    if reply:
        airtable.log_message(from_phone, "outbound", reply)

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
