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


def _flag_for_review(order: dict, near: dict) -> None:
    """A near-miss payment landed for an awaiting order but fell outside the auto-accept
    band. Email ops once (so it's never silently lost) and flag the order to avoid repeats."""
    f = order["fields"]
    ref = f.get("order_ref", "?"); coin = f.get("coin", ""); exp = f.get("expected_amount", "")
    amt = near.get("amount"); over = near.get("over"); tx = near.get("tx_hash", "")
    kind = "OVERPAID" if isinstance(over, (int, float)) and over > 0 else "UNDERPAID"
    body = (
        f"A payment landed that likely belongs to order {ref} but did NOT auto-verify "
        f"(it fell outside the auto-accept band):\n\n"
        f"  Status:      {kind}\n"
        f"  Order wants: {exp} {coin}\n"
        f"  Received:    {amt} {coin}   (delta {'+' if (isinstance(over,(int,float)) and over>=0) else ''}{over} {coin})\n"
        f"  Tx:          {tx}\n\n"
        f"If this is the customer's payment, open the order in Airtable and set "
        f"payment_status = paid and paste the tx hash. (Overpayment = they paid enough; you may "
        f"refund the excess. Underpayment = decide whether to ship or ask for the difference.)\n\n"
        f"This order is now flagged so you won't get repeat emails about it.\n"
    )
    try:
        from agents.weekly_report import _send_email
        _send_email(f"⚠️ Payment needs manual confirm — {ref} ({kind})", body, [])
    except Exception as e:
        print(f"[PayWatch] review email failed: {e!r}")
    try:
        airtable.update_order(order["id"], payment_flagged=True)
    except Exception as e:
        print(f"[PayWatch] flag write failed: {e!r}")
    print(f"[PayWatch] FLAGGED {ref} for review: got {amt} {coin} vs expected {exp}")


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
            # SAFETY NET: a real payment can land just outside the auto-accept band
            # (big over/underpay). Never silently drop it — do a wide "loose" scan and,
            # if a plausible payment exists, email ops once for manual confirmation.
            if not f.get("payment_flagged"):
                try:
                    near = crypto_verify.verify_payment(coin, _wallet_address(coin), expected,
                                                        since, other_amounts=others, loose=True)
                except Exception:
                    near = None
                if near:
                    _flag_for_review(o, near)
            continue
        print(f"[PayWatch] MATCH {f.get('order_ref')}: {res['amount']} {coin} tx={res['tx_hash'][:20]}…")
        try:
            airtable.mark_order_paid(o["id"], res.get("tx_hash", ""),
                                     datetime.now(timezone.utc).isoformat())
        except Exception as e:
            print(f"[PayWatch] mark_paid failed: {e!r}")
            continue
        # White-label deals: send the artwork + print spec to the label factory now
        # that payment is in. No-ops for ordinary orders and for repeat calls.
        try:
            from agents.messaging_agent import notify_factory_for_order
            notify_factory_for_order(o["id"])
        except Exception as e:
            print(f"[PayWatch] factory notify failed: {e!r}")
        paid += 1
        phone = airtable.get_lead_phone_for_order(o)
        if phone:
            set_stage(phone, "awaiting_address")
            # Keep (don't pop!) the order pointer — the awaiting_address handler
            # writes the customer's shipping details to pend["order_id"]. Popping
            # it here made Lily parse Daniel's address, say "All set!", and write
            # NOTHING (2026-08-02 incident).
            _pending_payments[phone] = {
                "order_id": o["id"], "coin": coin, "expected": expected,
                "since": 0.0, "charge_usd": float(f.get("total_price") or 0),
                "ref": f.get("order_ref", ""),
            }
            _notify_paid(phone)
    return {"awaiting": len(awaiting), "paid": paid}


if __name__ == "__main__":
    print(json.dumps(check_awaiting_payments(), indent=2))
