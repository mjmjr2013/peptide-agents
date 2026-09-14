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


# ── USSTOCK26 — Daniel's at-cost rehearsal code (HANDOFF §33c) ───────────────
def test_usstock26_is_an_at_cost_code_not_a_basket():
    """It was a fixed basket for a few hours on 2026-09-13; Jordan reworked it
    so Daniel enters the order himself. It must never come back as a deal."""
    assert deals.get_deal("USSTOCK26") is None
    spec = deals.get_at_cost_code("usstock26")
    assert spec and spec["code"] == "USSTOCK26" and spec["one_time"] is True


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


def test_at_cost_prompt_overrides_tiers_and_shipping():
    import agents.messaging_agent as ma
    p = ma._build_order_prompt("china", at_cost=True)
    assert p.endswith(ma._AT_COST_PROMPT)
    assert "INTERNAL AT-COST PRICES" in p
    assert "SHIPPING IS FREE for this buyer" in p
    assert "1-24 kits" not in p
    assert "INTERNAL AT-COST" not in ma._build_order_prompt("china")


# ── Arming, remembering, spending ────────────────────────────────────────────
def _fake_airtable(redeemed: bool = False, lead_update_error: str = ""):
    at = mock.MagicMock()
    at.is_promo_redeemed.return_value = redeemed
    if lead_update_error:
        at.leads.update.side_effect = Exception(lead_update_error)
    return at


def test_arming_remembers_the_code_in_memory_and_on_the_lead():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._pricing_field_ok = True
    with mock.patch.object(ma, "airtable", _fake_airtable()) as at, \
         mock.patch.object(ma, "_notify_operators"):
        reply = ma._arm_at_cost("+15550001", "usstock26", {"id": "recL", "fields": {}})
        assert "special pricing" in reply
        assert ma._at_cost["+15550001"] == "USSTOCK26"
        at.leads.update.assert_called_once_with("recL", {"pricing_code": "USSTOCK26"})
        assert ma.get_at_cost_code("+15550001") == "USSTOCK26"


def test_a_spent_code_does_not_arm():
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.object(ma, "airtable", _fake_airtable(redeemed=True)), \
         mock.patch.object(ma, "_notify_operators") as ops:
        reply = ma._arm_at_cost("+15550002", "USSTOCK26", {"id": "recL", "fields": {}})
        assert "already been used" in reply
        assert "+15550002" not in ma._at_cost
        assert ops.called


def test_unlock_is_read_back_from_the_lead_after_a_deploy():
    """Memory is wiped by every deploy; the lead's pricing_code is not."""
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    lead = {"id": "recL", "fields": {"pricing_code": "usstock26"}}
    with mock.patch.object(ma, "airtable", _fake_airtable()):
        assert ma.get_at_cost_code("+15550003", lead) == "USSTOCK26"
        assert ma._at_cost["+15550003"] == "USSTOCK26"      # re-cached


def test_unlock_dies_once_an_order_carrying_it_is_paid():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._at_cost["+15550004"] = "USSTOCK26"
    lead = {"id": "recL", "fields": {"pricing_code": "USSTOCK26"}}
    with mock.patch.object(ma, "airtable", _fake_airtable(redeemed=True)):
        assert ma.get_at_cost_code("+15550004", lead) == ""
        assert "+15550004" not in ma._at_cost


def test_unlock_fails_toward_the_sheet_price_when_airtable_is_down():
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._at_cost["+15550005"] = "USSTOCK26"
    at = _fake_airtable(); at.is_promo_redeemed.side_effect = Exception("airtable 503")
    with mock.patch.object(ma, "airtable", at):
        assert ma.get_at_cost_code("+15550005") == ""


def test_an_unknown_code_on_the_lead_is_ignored():
    import agents.messaging_agent as ma
    ma._at_cost.clear()
    with mock.patch.object(ma, "airtable", _fake_airtable()):
        assert ma.get_at_cost_code("+15550006", {"id": "x", "fields": {"pricing_code": "NOPE"}}) == ""


def test_missing_lead_field_degrades_to_memory_only_and_says_so_once(capsys):
    import agents.messaging_agent as ma
    ma._at_cost.clear(); ma._pricing_field_ok = True
    at = _fake_airtable(lead_update_error='422 UNKNOWN_FIELD_NAME "pricing_code"')
    with mock.patch.object(ma, "airtable", at), mock.patch.object(ma, "_notify_operators"):
        ma._arm_at_cost("+15550007", "USSTOCK26", {"id": "recL", "fields": {}})
        ma._arm_at_cost("+15550008", "USSTOCK26", {"id": "recM", "fields": {}})
    assert ma._at_cost["+15550007"] == ma._at_cost["+15550008"] == "USSTOCK26"
    assert at.leads.update.call_count == 1            # gave up after the first refusal
    assert "add it" in capsys.readouterr().out
    ma._pricing_field_ok = True
