"""What Jason is owed — the arithmetic, against the REAL catalog.

These tests use live SKUs on purpose. A fee test built on invented weights would
pass forever while the catalog moved underneath it, and the failure mode this
feature has is precisely 'the box count the payout used is not the box count on
the manifest'. Every assertion here is therefore also a check that
`warehouse_fees` and `shipping` still agree.
"""
import pytest

from core import catalog, shipping, warehouse_fees
from config import settings


BOX = 12.0
KG = 15.90
TARE_G = 350.0          # catalog.PACKAGE_TARE_G — asserted below, not assumed


@pytest.fixture(autouse=True)
def _rates(monkeypatch):
    """Pin the rates. Otherwise these tests read whatever is in the deployer's env."""
    monkeypatch.setattr(settings, "warehouse_fee_per_box_usd", BOX, raising=False)
    monkeypatch.setattr(settings, "warehouse_fee_per_kg_usd", KG, raising=False)


def expected(product_g, boxes):
    """$12 a box + $15.90 a kilo of GROSS weight, tare included, per component."""
    gross = product_g + TARE_G * boxes
    return round(round(boxes * BOX, 2) + round(gross / 1000.0 * KG, 2), 2)


def test_the_tare_is_the_shipping_constant_not_a_second_copy():
    """Jordan said 'assume box itself is 350g'. That number must be the one the
    packing split already uses — two copies would drift and pay him for a box
    weight the manifest disagrees with."""
    assert catalog.PACKAGE_TARE_G == TARE_G


# ── The rate itself ──────────────────────────────────────────────────────────

def test_one_small_order_is_a_box_fee_plus_its_weight():
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 1}])
    assert fee["billable"] is True
    assert fee["packages"] == 1
    assert fee["gross_g"] == 75.0 + TARE_G          # one kit, one empty box
    assert fee["box_usd"] == 12.0
    assert fee["weight_usd"] == 6.76
    assert fee["fee_usd"] == 18.76


def test_the_weight_paid_is_gross_including_the_box():
    """Product-only weight would shortchange him 350 g on every parcel."""
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 1}])
    assert fee["gross_g"] == fee["product_g"] + TARE_G


def test_a_split_order_carries_one_tare_per_box():
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert fee["packages"] == 2
    assert fee["gross_g"] == fee["product_g"] + TARE_G * 2


def test_the_two_components_add_up_to_the_total():
    """Jason re-adds this by hand off the statement; it has to reconcile."""
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert round(fee["box_usd"] + fee["weight_usd"], 2) == fee["fee_usd"]


def test_kg_rate_moves_with_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "warehouse_fee_per_kg_usd", 20.0, raising=False)
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 1}])
    assert fee["per_kg_usd"] == 20.0
    assert fee["weight_usd"] == round(0.425 * 20.0, 2)


def test_two_orders_of_the_same_weight_in_different_box_counts_differ_by_the_box_terms():
    """The whole point of having two components: packing and lifting are paid
    separately, and an extra box costs a $12 fee AND another 350 g of tare."""
    one = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 20}])
    two = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert one["packages"] == 1 and two["packages"] == 2
    assert two["fee_usd"] > two["product_g"] / 1000.0 * KG + BOX


def test_the_box_component_is_per_box_not_per_kit_or_per_order():
    """The bug this whole module is shaped to avoid: paying per kit, or per order."""
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert fee["packages"] == 2          # 40 * 75 g = 3 kg -> two balanced boxes
    assert fee["box_usd"] == 24.0        # not 40 * 12, and not 12
    assert fee["fee_usd"] == 82.83       # 24.00 + 3.70 kg * 15.90


def test_box_rate_moves_with_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "warehouse_fee_per_box_usd", 15.0, raising=False)
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert fee["per_box_usd"] == 15.0
    assert fee["box_usd"] == 30.0


def test_empty_order_owes_nothing_and_is_not_an_error():
    fee = warehouse_fees.fee_for_order([])
    assert fee["billable"] is True and fee["fee_usd"] == 0.0 and fee["packages"] == 0


# ── The box count is SHIPPING's, never a second opinion ──────────────────────

@pytest.mark.parametrize("items", [
    [{"sku": "RT100", "kits": 1}],
    [{"sku": "RT100", "kits": 40}],
    [{"sku": "BAC10", "kits": 84}],
    [{"sku": "BAC10", "kits": 84}, {"sku": "RT100", "kits": 2}],
    [{"sku": "MIC10", "kits": 12}, {"sku": "RT100", "kits": 7}],
    [{"sku": "MIC10", "kits": 40}],
])
def test_fee_box_count_equals_the_manifest_box_count(items):
    """If these ever diverge, Jason is told to pack N boxes and paid for M."""
    try:
        n = len(shipping.split_packages(items))
    except shipping.Unshippable:
        pytest.skip("unshippable mix")
    fee = warehouse_fees.fee_for_order(items)
    assert fee["packages"] == n
    assert fee["fee_usd"] == expected(fee["product_g"], n)


def test_package_detail_is_carried_through_for_the_audit_trail():
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 40}])
    assert len(fee["package_detail"]) == fee["packages"]
    assert all("gross_g" in p for p in fee["package_detail"])


# ── Fail closed ──────────────────────────────────────────────────────────────

def test_unknown_product_is_not_paid_at_a_guessed_box_count():
    """order_weight_g contributes NOTHING for a line it cannot weigh, so a naive
    implementation would price this order at one box. It must refuse instead."""
    fee = warehouse_fees.fee_for_order([{"product": "Blorbotide", "spec": "50mg", "kits": 30}])
    assert fee["billable"] is False
    assert fee["fee_usd"] == 0.0
    assert "no catalog weight" in fee["reason"]
    assert fee["unweighed"]


def test_one_bad_line_poisons_the_whole_order_not_just_that_line():
    fee = warehouse_fees.fee_for_order([
        {"sku": "RT100", "kits": 40},
        {"product": "Blorbotide", "spec": "50mg", "kits": 1},
    ])
    assert fee["billable"] is False and fee["fee_usd"] == 0.0


def test_strict_mode_raises_for_callers_that_want_it():
    with pytest.raises(warehouse_fees.UnpriceableOrder):
        warehouse_fees.fee_for_order(
            [{"product": "Blorbotide", "spec": "50mg", "kits": 1}], strict=True)


def test_zero_and_negative_kit_lines_are_ignored_not_charged():
    fee = warehouse_fees.fee_for_order([{"sku": "RT100", "kits": 1},
                                        {"sku": "RT100", "kits": 0}])
    assert fee["billable"] and fee["packages"] == 1
    assert fee["fee_usd"] == expected(75.0, 1)


# ── The bac-water hole, now closed by the weight term ────────────────────────

def test_the_heavy_water_box_is_paid_for_its_weight_not_just_as_one_box():
    """84 kits of bac water ship as ONE 23 kg box (HANDOFF §30). Under a flat
    per-box rate that was $12 for 23 kg of lifting. The per-kg term is what fixes
    it, without needing a special case for water anywhere."""
    fee = warehouse_fees.fee_for_order([{"sku": "BAC10", "kits": 84}])
    assert fee["packages"] == 1
    assert fee["box_usd"] == 12.0
    assert fee["weight_usd"] == 366.18
    assert fee["fee_usd"] == 378.18


def test_equal_gross_weight_pays_equal_weight_money_capped_or_not():
    """A kilo is a kilo. The cap changes how many BOXES, and so the box fees —
    it must not change the price of the weight itself."""
    water = warehouse_fees.fee_for_order([{"sku": "BAC10", "kits": 10}])   # uncapped
    other = warehouse_fees.fee_for_order([{"sku": "MIC10", "kits": 10}])   # capped, splits
    assert water["packages"] == 1 and other["packages"] == 2
    for fee in (water, other):
        assert fee["weight_usd"] == round(fee["gross_g"] / 1000.0 * KG, 2)


# ── Batches ──────────────────────────────────────────────────────────────────

def _batch():
    return warehouse_fees.fee_for_batch([
        {"id": "r1", "ref": "NL-A", "items": [{"sku": "RT100", "kits": 1}]},
        {"id": "r2", "ref": "NL-B", "items": [{"sku": "RT100", "kits": 40}]},
        {"id": "r3", "ref": "NL-C",
         "items": [{"product": "Blorbotide", "spec": "50mg", "kits": 1}]},
    ])


def test_batch_totals_only_the_payable_rows():
    b = _batch()
    assert b["orders"] == 2
    assert b["boxes"] == 3
    assert b["box_usd"] == 36.0
    assert b["total_usd"] == round(18.76 + 82.83, 2)
    assert [r["ref"] for r in b["skipped"]] == ["NL-C"]


def test_batch_components_add_up_to_the_batch_total():
    b = _batch()
    assert round(b["box_usd"] + b["weight_usd"], 2) == b["total_usd"]


def test_batch_gross_weight_is_the_sum_of_the_payable_rows():
    b = _batch()
    assert b["gross_g"] == round(sum(r["gross_g"] for r in b["rows"]), 1)


def test_a_bad_order_does_not_block_the_good_ones():
    """One catalog gap must not stop Jason being paid for the rest of the night."""
    b = _batch()
    assert b["total_usd"] > 0
    assert all(r["billable"] for r in b["rows"])


def test_skipped_orders_are_never_folded_in_at_zero_and_forgotten():
    b = _batch()
    assert b["skipped"] and all(r["reason"] for r in b["skipped"])


def test_empty_batch_is_zero_not_an_error():
    b = warehouse_fees.fee_for_batch([])
    assert b["orders"] == 0 and b["total_usd"] == 0.0 and b["rows"] == []


# ── The statement Jason reads ────────────────────────────────────────────────

def test_statement_shows_every_order_and_the_total():
    b = _batch()
    text = warehouse_fees.statement_text(b, "2026-09-05")
    assert "NL-A" in text and "NL-B" in text
    assert "101.59" in text
    assert "$12.00 per box plus $15.90 per kg." in text


def test_statement_shows_both_components_so_he_can_re_add_it():
    text = warehouse_fees.statement_text(_batch(), "2026-09-05")
    assert "BOXES $" in text and "WEIGHT $" in text and "GROSS KG" in text


def test_statement_says_the_box_weight_is_included():
    """He is being paid for 350 g he did not put in the box; say so, or the
    kilos on his own scale will not match the kilos on the statement."""
    text = warehouse_fees.statement_text(_batch(), "2026-09-05")
    assert "350 g" in text


def test_statement_tells_him_what_was_left_out():
    """An order he packed and was not paid for must be visible to him, or the
    first he hears of it is a number that looks wrong."""
    text = warehouse_fees.statement_text(_batch(), "2026-09-05")
    assert "NOT INCLUDED" in text and "NL-C" in text


def test_statement_has_no_customer_data():
    """Same discipline as the warehouse manifest: the rep sees what he needs."""
    text = warehouse_fees.statement_text(_batch(), "2026-09-05")
    for leak in ("address", "@", "phone"):
        assert leak not in text.lower()
