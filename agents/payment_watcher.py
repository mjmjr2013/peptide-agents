from __future__ import annotations
"""
Payment watcher — proactive on-chain verification of awaiting orders.

Historically, payment verification only ran when the CUSTOMER messaged
("checking with finance…"), so a paid order could sit unverified until they
nagged. This watcher runs from the in-process scheduler every ~5 minutes:
for every order with payment_status='awaiting', it checks the chain (with the
same bounded-overpay matching and cross-order guard as the reactive path).
On a match it marks the order paid and messages the customer — in Lily's
voice inside their WhatsApp 24h window, or via the approved
`northline_payment_received` template outside it — asking for the shipping
address, and moves their conversation stage to awaiting_address.

Chain lookups are free APIs (Etherscan / mempool.space); a cycle with no
awaiting orders costs one Airtable call.
"""
import json
from datetime import datetime, timezone

from config import settings
from core.airtable_client import airtable
from core import crypto_verify

_CONFIRM_MSG = ("Okay dear, finance has confirmed — payment received! 🎉 Thank you so "
                "much. Now please send your shipping details so we can deliver: full "
                "name, street address, city, state/province, postal code, and country.")


def _notify_paid(phone: str) -> bool:
    """Tell the customer their payment cleared + ask for the address. Freeform in
    the 24h window; approved template outside it. Best-effort."""
    from agents.messaging_agent import (_whatsapp_window_open, twilio_client,
                                        get_conversation, save_conversation)
    from_number = settings.twilio_whatsapp_from if "whatsapp" in phone else settings.twilio_phone_number
    try:
        if _whatsapp_window_open(phone) or not settings.payment_content_sid:
            msg = twilio_client.messages.create(body=_CONFIRM_MSG, from_=from_number, to=phone)
            logged = _CONFIRM_MSG
        else:
            msg = twilio_client.messages.create(content_sid=settings.payment_content_sid,
                                                from_=from_number, to=phone)
            logged = "[template] payment received — please send shipping details"
        airtable.log_message(phone, "outbound", logged)
        conv = get_conversation(phone)
        conv.append({"role": "assistant", "content": logged})
        save_conversation(phone, conv)
        print(f"[PayWatch] payment-confirmed message to {phone}: SID={msg.sid}")
        return True
    except Exception as e:
        print(f"[PayWatch] notify {phone} failed: {e!r}")
        return False


def check_awaiting_payments() -> dict:
    """One watcher cycle. Returns counts for the scheduler log."""
    from agents.messaging_agent import _wallet_address, set_stage, _pending_payments
    try:
        awaiting = airtable.get_awaiting_orders()
    except Exception as e:
        print(f"[PayWatch] airtable fetch failed: {e!r}")
        return {"awaiting": 0, "paid": 0}
    paid = 0
    for o in awaiting:
        f = o["fields"]
        coin = (f.get("coin") or "").upper()
        expected = float(f.get("expected_amount") or 0)
        if not coin or not expected:
            continue
        try:
            since = datetime.fromisoformat(
                o["createdTime"].replace("Z", "+00:00")).timestamp()
        except Exception:
            since = 0
        others = [float(x["fields"].get("expected_amount") or 0) for x in awaiting
                  if x["id"] != o["id"] and (x["fields"].get("coin") or "").upper() == coin]
        try:
            res = crypto_verify.verify_payment(coin, _wallet_address(coin), expected,
                                               since, other_amounts=others)
        except Exception as e:
            print(f"[PayWatch] verify {f.get('order_ref')} failed: {e!r}")
            continue
        if not res:
            continue
        print(f"[PayWatch] MATCH {f.get('order_ref')}: {res['amount']} {coin} tx={res['tx_hash'][:20]}…")
        try:
            airtable.mark_order_paid(o["id"], res.get("tx_hash", ""),
                                     datetime.now(timezone.utc).isoformat())
        except Exception as e:
            print(f"[PayWatch] mark_paid failed: {e!r}")
            continue
        paid += 1
        phone = airtable.get_lead_phone_for_order(o)
        if phone:
            set_stage(phone, "awaiting_address")
            _pending_payments.pop(phone, None)
            _notify_paid(phone)
    return {"awaiting": len(awaiting), "paid": paid}


if __name__ == "__main__":
    print(json.dumps(check_awaiting_payments(), indent=2))
