"""A new order must never fail an already-PAID order — HANDOFF §33e, bug 1.

Daniel placed three at-cost orders to three addresses in one conversation and
paid each. Because the flow marks the prior awaiting order 'failed' when a new
one is placed, and the watcher only scans 'awaiting', two paid orders were
buried and never confirmed. _supersede_unless_paid closes that: it fails a prior
order ONLY when the chain says it is unpaid.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _order(ref="NL-1", coin="USDT", expected=231.01, oid="rec1"):
    return {"id": oid, "fields": {"order_ref": ref, "coin": coin, "expected_amount": expected}}


def _fake_at(awaiting=None):
    at = mock.MagicMock()
    at.get_awaiting_orders.return_value = awaiting or []
    return at


def test_a_paid_prior_order_is_not_superseded():
    import agents.messaging_agent as ma
    at = _fake_at()
    with mock.patch.object(ma, "airtable", at), \
         mock.patch.object(ma.crypto_verify, "verify_payment",
                           return_value={"tx_hash": "0xabc", "amount": 231.12}), \
         mock.patch.object(ma, "_notify_operators") as ops:
        superseded = ma._supersede_unless_paid(_order())
    assert superseded is False
    at.orders.update.assert_not_called()               # never marked failed
    assert "MULTI-ORDER" in ops.call_args[0][0]


def test_an_unpaid_prior_order_is_still_superseded():
    import agents.messaging_agent as ma
    at = _fake_at()
    with mock.patch.object(ma, "airtable", at), \
         mock.patch.object(ma.crypto_verify, "verify_payment", return_value=None):
        superseded = ma._supersede_unless_paid(_order())
    assert superseded is True
    at.orders.update.assert_called_once_with("rec1", {"payment_status": "failed"})


def test_an_unverifiable_prior_order_is_kept_not_failed():
    """If the chain cannot be checked, keep the order — failing a possibly-paid
    order is the worse outcome. Fails toward keeping money."""
    import agents.messaging_agent as ma
    at = _fake_at()
    with mock.patch.object(ma, "airtable", at), \
         mock.patch.object(ma.crypto_verify, "verify_payment",
                           side_effect=Exception("etherscan 503")), \
         mock.patch.object(ma, "_notify_operators") as ops:
        superseded = ma._supersede_unless_paid(_order())
    assert superseded is False
    at.orders.update.assert_not_called()
    assert "unverifiable" in ops.call_args[0][0].lower()


def test_no_prior_order_is_a_noop():
    import agents.messaging_agent as ma
    at = _fake_at()
    with mock.patch.object(ma, "airtable", at):
        assert ma._supersede_unless_paid(None) is False
        at.orders.update.assert_not_called()


def test_other_awaiting_amounts_are_passed_so_a_shared_address_isnt_misattributed():
    """The paid-check must exclude the order under test and pass the OTHER
    awaiting amounts, or a neighbour's payment could be read as this order's."""
    import agents.messaging_agent as ma
    prev = _order(expected=231.01, oid="rec1")
    others = [prev, _order(ref="NL-2", expected=429.01, oid="rec2")]
    at = _fake_at(awaiting=others)
    seen = {}
    def fake_verify(coin, addr, expected, since, other_amounts=None, loose=False):
        seen["expected"] = expected; seen["others"] = other_amounts
        return None
    with mock.patch.object(ma, "airtable", at), \
         mock.patch.object(ma.crypto_verify, "verify_payment", side_effect=fake_verify):
        ma._order_paid_onchain(prev)
    assert seen["expected"] == 231.01
    assert 429.01 in seen["others"] and 231.01 not in seen["others"]
