"""Pin every live code in core/deals.py — the basket deals AND the at-cost codes.

A deal is a human-set basket at a human-set total, so nothing derives it and
nothing else would notice a stray edit. An at-cost code is smaller but sharper:
it puts our cost prices into a chat, so the tests here are about it doing that
ONLY for the phone that presented it, ONLY until the order is paid, and never
changing what a warehouse sells. If one of these fails, confirm with Jordan
before touching the expected value.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import deals, pricing


# ── DIEGO26 (HANDOFF §24) ─────────────────────────────────────────────────────
def test_diego26_is_pinned():
    d = deals.get_deal("DIEGO26")
    assert deals.total_kits(d) == 30
    assert deals.branded_kits(d) == 25
    assert deals.variation_count(d) == 21
    assert deals.grand_total(d) == 3393.64
    assert d["one_time"] and d["requires_artwork"]


# ── USSTOCK26 — Daniel's at-cost stock-order code (HANDOFF §33c/§33d) ────────
DANIEL = "+14806366814"      # HANDOFF §28; the number the code is locked to
STRANGER = "+15550009999"


def test_usstock26_is_an_at_cost_code_not_a_basket():
    """It was a fixed basket for a few hours on 2026-09-13; Jordan reworked it
    so Daniel enters the order himself. It must never come back as a deal."""
    assert deals.get_deal("USSTOCK26") is None
    spec = deals.get_at_cost_code("usstock26")
    assert spec and spec["code"] == "USSTOCK26"


def test_usstock26_is_reusable_and_locked_to_daniels_phone():
    """Jordan, 2026-09-13: not single use — Daniel orders the company's own stock
    with it repeatedly — so the PHONE LOCK is the only guard. It must be there."""
    spec = deals.get_at_cost_code("USSTOCK26")
    assert spec["one_time"] is False
    assert spec["phones"] == (DANIEL,)
    assert deals.phone_allowed(spec, DANIEL)
    assert deals.phone_allowed(spec, "whatsapp:" + DANIEL)
    assert deals.phone_allowed(spec, "(480) 636-6814")
    assert not deals.phone_allowed(spec, STRANGER)
    assert not deals.phone_allowed(spec, "")
    assert not deals.phone_allowed(spec, "6814")        # a suffix is not a match


def test_every_at_cost_code_is_locked_to_at_least_one_phone():
    """An open at-cost code would hand our cost sheet to whoever guessed it."""
    for code, spec in deals.AT_COST_CODES.items():
        assert spec.get("phones"), f"{code} is not locked to a phone"


def test_a_code_is_exactly_one_kind():
    assert not (set(deals.DEALS) & set(deals.AT_COST_CODES))
    assert deals.find_code_in("USSTOCK26") is None          # not a basket deal
    assert deals.find_at_cost_code_in("DIEGO26") is None    # not an at-cost code


def test_at_cost_code_is_found_on_word_boundaries_only():
    assert deals.find_at_cost_code_in("hi, my code is usstock26!") == "USSTOCK26"
    assert deals.find_at_cost_code_in("USSTOCK26 6 kits reta 20mg please") == "USSTOCK26"
    assert deals.find_at_cost_code_in("xUSSTOCK26") is None
    assert deals.find_at_cost_code_in("USSTOCK261") is None
    assert deals.find_at_cost_code_in("") is None


# ── The at-cost catalog Lily quotes from ─────────────────────────────────────
def test_at_cost_catalog_text_is_cost_with_cents_and_no_tiers():
    china = pricing.get_catalog_text("china", at_cost=True)
    assert "1-24 kits" not in china and "25-99" not in china
    assert "Retatrutide | 20mg x10 | $28.26" in china
    # The ordinary prompt table is untouched.
    normal = pricing.get_catalog_text("china")
    assert "1-24 kits" in normal and "$28.26" not in normal


def test_at_cost_us_table_lists_only_us_skus():
    """The code changes the price, never what a warehouse stocks."""
    us = pricing.get_catalog_text("us", at_cost=True)
    rows = [l for l in us.splitlines()[3:] if l.strip()]
    assert len(rows) == len(pricing.US_CATALOG) == 30
    assert "DSIP" not in us                         # China-only, must stay absent
    assert pricing.cost_for_sku("RT10") == 14.88
    assert "Retatrutide 10mg | $1.49 | $14.88" in us


# ── Pricing through the agent's validator ────────────────────────────────────
def test_validator_prices_at_cost_only_when_told():
    import agents.messaging_agent as ma
    li = [{"product": "Retatrutide", "spec": "20mg", "quantity_kits": 6, "unit_price": 180},
          {"product": "Bacteriostatic Water", "spec": "10ml", "quantity_kits": 10}]
    sheet, _, _ = ma._validate_line_items(li, "china")
    cost, _, unpriced = ma._validate_line_items(li, "china", at_cost=True)
    assert [i["unit_price"] for i in sheet] == [180.0, 20.0]
    assert [i["unit_price"] for i in cost] == [28.26, 2.98]
    assert not unpriced
    assert cost[0]["line_total"] == round(28.26 * 6, 2)


def test_validator_at_cost_still_refuses_what_the_warehouse_does_not_sell():
    import agents.messaging_agent as ma
    items, _, unpriced = ma._validate_line_items(
        [{"product": "DSIP", "spec": "5mg", "quantity_kits": 1}], "us", at_cost=True)
    assert items == [] and unpriced and unpriced[0]["product"] == "DSIP"


def test_at_cost_prompt_overrides_tiers_and_keeps_shipping_charged():
    import agents.messaging_agent as ma
    p = ma._build_order_prompt("china", at_cost=True)
    assert p.endswith(ma._AT_COST_PROMPT)
    assert "INTERNAL AT-COST PRICES" in p
    assert "NO free-shipping threshold" in p and "Never waive the $95" in p
    assert "1-24 kits" not in p
    assert "INTERNAL AT-COST" not in ma._build_order_prompt("china")


def test_at_cost_orders_pay_flat_shipping_with_no_free_threshold():
    """Jordan, 2026-09-13: "don't do free shipping. Charge the flat $95." A
    $1,500 at-cost order still pays $95; a $1,500 customer order does not."""
    import agents.messaging_agent as ma
    assert ma._shipping_fee("standard", 1500) == 0
    assert ma._shipping_fee("standard", 1500, free_threshold=False) == 95
    assert ma._shipping_fee("standard", 200, free_threshold=False) == 95
    assert ma._shipping_fee("expedited", 1500, free_threshold=False) == 235
    assert ma._shipping_fee("standard", 1500, warehouse="us", free_threshold=False) == 30


# ── Arming, remembering, spending ────────────────────────────────────────────
def _fake_airtable(redeemed: bool = False, lead_update_error: str = ""):
    at = mock.MagicMock()
    at.is_promo_redeemed.return_value = redeemed
    if lead_update_error:
        at.leads.update.side_effect = Exception(lead_update_error)
    return at


def test_arming_from_daniels_phone_remembers_the_code_in_memory_and_on_the_lead():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._pricing_field_ok = True
    with mock.patch.object(ma, "airtable", _fake_airtable()) as at, \
         mock.patch.object(ma, "_notify_operators"):
        reply = ma._arm_at_cost("whatsapp:" + DANIEL, "usstock26", {"id": "recL", "fields": {}})
        assert "special pricing" in reply
        assert ma._at_cost["whatsapp:" + DANIEL] == "USSTOCK26"
        at.leads.update.assert_called_once_with("recL", {"pricing_code": "USSTOCK26"})
        assert ma.get_at_cost_code("whatsapp:" + DANIEL) == "USSTOCK26"


def test_another_phone_presenting_the_code_is_refused_and_reported():
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.object(ma, "airtable", _fake_airtable()) as at, \
         mock.patch.object(ma, "_notify_operators") as ops:
        reply = ma._arm_at_cost(STRANGER, "USSTOCK26", {"id": "recX", "fields": {}})
        assert "check that code" in reply and "USSTOCK26" not in reply
        assert STRANGER not in ma._at_cost
        assert not at.leads.update.called
        assert "WRONG PHONE" in ops.call_args[0][0]


def test_the_lock_holds_even_if_the_code_is_on_the_wrong_lead():
    """pricing_code pasted onto a stranger's lead by hand must do nothing."""
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.object(ma, "airtable", _fake_airtable()):
        assert ma.get_at_cost_code(STRANGER, {"id": "recX", "fields": {"pricing_code": "USSTOCK26"}}) == ""
        assert STRANGER not in ma._at_cost


def test_usstock26_survives_a_paid_order_because_it_is_reusable():
    """Redemption is not even consulted for a code that is not one_time."""
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._at_cost[DANIEL] = "USSTOCK26"
    at = _fake_airtable(redeemed=True)
    with mock.patch.object(ma, "airtable", at):
        assert ma.get_at_cost_code(DANIEL) == "USSTOCK26"
        assert not at.is_promo_redeemed.called


_ONESHOT = {"ONESHOT1": {"code": "ONESHOT1", "label": "test", "one_time": True,
                         "phones": (DANIEL,), "notes": ""}}


def test_a_spent_one_time_code_does_not_arm():
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.dict(deals.AT_COST_CODES, _ONESHOT), \
         mock.patch.object(ma, "airtable", _fake_airtable(redeemed=True)), \
         mock.patch.object(ma, "_notify_operators") as ops:
        reply = ma._arm_at_cost(DANIEL, "ONESHOT1", {"id": "recL", "fields": {}})
        assert "already been used" in reply
        assert DANIEL not in ma._at_cost
        assert ops.called


def test_unlock_is_read_back_from_the_lead_after_a_deploy():
    """Memory is wiped by every deploy; the lead's pricing_code is not."""
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    lead = {"id": "recL", "fields": {"pricing_code": "usstock26"}}
    with mock.patch.object(ma, "airtable", _fake_airtable()):
        assert ma.get_at_cost_code(DANIEL, lead) == "USSTOCK26"
        assert ma._at_cost[DANIEL] == "USSTOCK26"      # re-cached


def test_a_one_time_unlock_dies_once_an_order_carrying_it_is_paid():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._at_cost[DANIEL] = "ONESHOT1"
    lead = {"id": "recL", "fields": {"pricing_code": "ONESHOT1"}}
    with mock.patch.dict(deals.AT_COST_CODES, _ONESHOT), \
         mock.patch.object(ma, "airtable", _fake_airtable(redeemed=True)):
        assert ma.get_at_cost_code(DANIEL, lead) == ""
        assert DANIEL not in ma._at_cost


def test_a_one_time_unlock_fails_toward_the_sheet_price_when_airtable_is_down():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._at_cost[DANIEL] = "ONESHOT1"
    at = _fake_airtable(); at.is_promo_redeemed.side_effect = Exception("airtable 503")
    with mock.patch.dict(deals.AT_COST_CODES, _ONESHOT), mock.patch.object(ma, "airtable", at):
        assert ma.get_at_cost_code(DANIEL) == ""


def test_an_unknown_code_on_the_lead_is_ignored():
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.object(ma, "airtable", _fake_airtable()):
        assert ma.get_at_cost_code(DANIEL, {"id": "x", "fields": {"pricing_code": "NOPE"}}) == ""


def test_missing_lead_field_degrades_to_memory_only_and_says_so_once(capsys):
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._pricing_field_ok = True
    at = _fake_airtable(lead_update_error='422 UNKNOWN_FIELD_NAME "pricing_code"')
    with mock.patch.object(ma, "airtable", at), mock.patch.object(ma, "_notify_operators"):
        ma._arm_at_cost(DANIEL, "USSTOCK26", {"id": "recL", "fields": {}})
        ma._at_cost.clear()
        ma._arm_at_cost("whatsapp:" + DANIEL, "USSTOCK26", {"id": "recL", "fields": {}})
    assert ma._at_cost["whatsapp:" + DANIEL] == "USSTOCK26"
    assert at.leads.update.call_count == 1            # gave up after the first refusal
    assert "add it" in capsys.readouterr().out
    ma._pricing_field_ok = True
