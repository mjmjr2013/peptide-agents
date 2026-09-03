"""
Consolidated catalog + weight-aware shipping — 2026-08-31.

Companion to test_catalog_regression.py, which pins PRICES. This file pins the
things that were added when the catalog was consolidated into core/catalog.py:

  * the join across the three catalog copies is TOTAL — every sheet SKU has a
    cost row and vice versa. This is the test that turns catalog drift (the root
    cause in HANDOFF §29) into a CI failure instead of an unpriceable order line.
  * anything that PRICES also WEIGHS. The price guard refuses unpriceable lines;
    if a priceable line could still be unweighable, an order would pass the guard
    and then ship at an unknown weight.
  * package splitting stays under the 2 kg GROSS cap and stays BALANCED.

Run:  python3 -m pytest tests/test_catalog_weights.py -q
"""
from __future__ import annotations
import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog, price_image, pricing, shipping          # noqa: E402
from core.aliases import canon                                    # noqa: E402


def _sheet_rows():
    for _cat, items in price_image.CATEGORIES:
        for sku, product, spec, pstr in items:
            yield sku, product, spec, float(re.sub(r"[^0-9.]", "", pstr))


SHEET_ROWS = list(_sheet_rows())


# ── The join is total ────────────────────────────────────────────────────────

def test_audit_is_clean():
    """Every way the catalog sources can disagree, all at once.

    If this fails, read the dict: it names the SKUs and which source is missing
    them. Do NOT silence it by deleting a row — that is how §29 happened.
    """
    problems = {k: v for k, v in catalog.audit().items() if v}
    assert problems == {}, f"catalog drift: {problems}"


def test_every_sheet_sku_is_in_the_catalog():
    assert len(catalog.ITEMS) == len(SHEET_ROWS)
    for sku, _product, _spec, _price in SHEET_ROWS:
        assert catalog.get(sku) is not None, f"{sku} missing from consolidated catalog"


def test_catalog_list_price_matches_the_sheet_exactly():
    """The consolidated catalog must not become a second opinion on price."""
    for sku, product, spec, price in SHEET_ROWS:
        item = catalog.get(sku)
        assert item.list_price == price, f"{sku}: catalog ${item.list_price} vs sheet ${price}"
        assert item.list_price == pricing.get_list_price(product, spec), \
            f"{sku}: catalog disagrees with pricing.get_list_price"


def test_catalog_cost_matches_the_cost_file():
    by_key = {(canon(r["product"]), price_image._norm_dose(r["spec"])): r
              for r in pricing.CATALOG}
    for item in catalog.ITEMS:
        row = by_key[(canon(item.product), price_image._norm_dose(item.spec))]
        assert item.cost == row["cost"], f"{item.sku}: cost drifted from pricing.CATALOG"


def test_no_sku_code_is_reused():
    assert len(catalog.BY_SKU) == len(SHEET_ROWS)


# ── Anything priceable is weighable ──────────────────────────────────────────

def test_every_catalog_item_has_a_positive_weight():
    for item in catalog.ITEMS:
        assert item.unit_weight_g and item.unit_weight_g > 0, f"{item.sku} has no weight"


def test_anything_that_prices_also_weighs():
    """The invariant that lets the order flow trust weight.

    _validate_line_items refuses a line it cannot price. If a line could price
    but not weigh, it would sail through the guard and reach shipping with an
    unknown weight — the same shape of hole as §29, one layer down.
    """
    spellings = []
    for sku, product, spec, _p in SHEET_ROWS:
        dose = spec.split()[0] if spec else ""
        spellings += [(product, spec), (product, dose), (product.lower(), dose),
                      (product.upper(), dose)]
    for row in pricing.CATALOG:
        spellings += [(row["product"], row["spec"]),
                      (row["product"], row["spec"].split()[0])]

    misses = [(p, s) for p, s in spellings
              if pricing.get_list_price(p, s) is not None and catalog.find(p, s) is None]
    assert misses == [], f"{len(misses)} spellings price but do not weigh: {misses[:5]}"


def test_liquid_is_a_class_not_a_bac10_special_case():
    """HANDOFF §29 asked for this explicitly. All four ml-spec SKUs are liquid,
    nothing measured in mg or IU is, and no SKU is named anywhere in the rule."""
    liquid = {i.sku for i in catalog.ITEMS if i.is_liquid}
    assert liquid == {"BAC10", "LC216", "MIC10"}
    for item in catalog.ITEMS:
        assert (item.unit == "ml") == item.is_liquid, f"{item.sku} misclassified"


def test_liquid_kits_are_heavier_than_lyophilized():
    lyo = {i.unit_weight_g for i in catalog.ITEMS if not i.is_liquid and i.weight_source == "form"}
    liq = {i.unit_weight_g for i in catalog.ITEMS if i.is_liquid}
    assert lyo == {75.0} and liq == {270.0}


@pytest.mark.parametrize("product,grams", [("NAD", 170.0), ("Glutathione", 170.0)])
def test_product_weight_overrides_apply_to_every_dose(product, grams):
    doses = [i for i in catalog.ITEMS if canon(i.product) == canon(product)]
    assert doses, f"no {product} rows found"
    for item in doses:
        assert item.unit_weight_g == grams and item.weight_source == "product"


# ── Package splitting ────────────────────────────────────────────────────────

def test_tare_comes_out_of_the_cap():
    assert shipping.PAYLOAD_CAP_G == shipping.PACKAGE_CAP_G - catalog.PACKAGE_TARE_G == 1650.0


@pytest.mark.parametrize("sku,kits", [
    ("LC216", 84), ("LC216", 1), ("RT100", 40), ("RT100", 1), ("RT100", 22),
    ("RT100", 23), ("NJ1000", 30), ("MIC10", 11), ("GTT15", 15), ("RT100", 250),
])
def test_no_capped_package_ever_exceeds_the_gross_cap(sku, kits):
    for pkg in shipping.split_packages([{"sku": sku, "kits": kits}]):
        assert pkg["capped"] is True
        assert pkg["gross_g"] <= shipping.PACKAGE_CAP_G, \
            f"{sku} x{kits}: box {pkg['index']} is {pkg['gross_g']}g"


@pytest.mark.parametrize("sku,kits", [("LC216", 84), ("RT100", 40), ("NJ100", 19)])
def test_split_is_balanced_not_greedy(sku, kits):
    """A greedy fill tops out each box in turn and leaves a stub at the end.
    Balanced means every box is within one kit's weight of every other."""
    pkgs = shipping.split_packages([{"sku": sku, "kits": kits}])
    loads = [p["payload_g"] for p in pkgs]
    assert max(loads) - min(loads) <= catalog.get(sku).unit_weight_g


def test_handoff_three_kilo_example():
    """HANDOFF §29: 'a 3 kg order becomes two ~1.5 kg shipments, not 2.0 + 1.0'."""
    pkgs = shipping.split_packages([{"sku": "RT100", "kits": 40}])   # 40 x 75g = 3.0kg
    assert [p["payload_g"] for p in pkgs] == [1500.0, 1500.0]


def test_package_count_falls_out_of_weight_not_kit_count():
    """Same kit count, different mix, different box count — nothing hardcoded."""
    lyo = shipping.split_packages([{"sku": "RT100", "kits": 12}])
    liq = shipping.split_packages([{"sku": "LC216", "kits": 12}])
    assert len(lyo) == 1 and len(liq) == 2


def test_every_kit_survives_the_split():
    items = [{"sku": "RT100", "kits": 12}, {"sku": "BAC10", "kits": 12},
             {"sku": "NJ1000", "kits": 6}]
    pkgs = shipping.split_packages(items)
    assert sum(p["kits"] for p in pkgs) == 30
    counted = {}
    for p in pkgs:
        for c in p["contents"]:
            counted[c["sku"]] = counted.get(c["sku"], 0) + c["kits"]
    assert counted == {"RT100": 12, "BAC10": 12, "NJ1000": 6}
    total, _ = shipping.order_weight_g(items)
    assert round(sum(p["payload_g"] for p in pkgs), 1) == total


def test_unresolvable_line_is_reported_not_silently_weighed_as_zero():
    grams, unweighed = shipping.order_weight_g(
        [{"product": "Definitely Not A Peptide", "spec": "10mg", "kits": 5}])
    assert grams == 0.0 and len(unweighed) == 1


# ── Customer-facing quote is unchanged until someone turns the guard on ──────

def _old_shipping_fee(shipping_kind, subtotal):
    """The rule exactly as it read before core/shipping.py existed."""
    if shipping_kind == "expedited":
        return 235
    if subtotal > 1000:
        return 0
    return 95


@pytest.mark.parametrize("kind", ["standard", "expedited"])
@pytest.mark.parametrize("subtotal", [0, 50, 94.99, 999, 1000, 1000.01, 5000])
def test_customer_shipping_quote_is_unchanged(kind, subtotal):
    """Freight is priced into bac water instead of charged at checkout, so this
    number must not have moved for anybody. Checked with and without items."""
    expected = _old_shipping_fee(kind, subtotal)
    assert shipping.shipping_quote(kind, subtotal) == expected
    assert shipping.shipping_quote(kind, subtotal, [{"sku": "BAC10", "kits": 84}]) == expected


def test_no_weight_term_leaked_into_the_customer_quote():
    """Guard against a future session reintroducing a weight-based charge without
    Jordan — the decision was to price freight in, not to bill it."""
    assert not hasattr(shipping, "FREE_SHIPPING_MAX_KG")


def test_value_per_kg_ranks_the_money_losers_last():
    water = shipping.order_shipping_profile([{"sku": "BAC10", "kits": 84}], "standard", 1428)
    peptide = shipping.order_shipping_profile([{"sku": "RT100", "kits": 1}], "standard", 894)
    assert water["value_per_kg"] < peptide["value_per_kg"] / 100
    assert water["packages"] == 1 and peptide["packages"] == 1


# ── The bac water exemption ──────────────────────────────────────────────────

@pytest.mark.parametrize("sku", ["BAC10"])
def test_water_ships_whole_regardless_of_weight(sku):
    """Jordan, 2026-08-31: the 2 kg cap is about seizure risk, and water carries
    none. 84 kits is ONE box (22.7 kg), not the fourteen the cap would force."""
    pkgs = shipping.split_packages([{"sku": sku, "kits": 84}])
    assert len(pkgs) == 1
    assert pkgs[0]["kits"] == 84
    assert pkgs[0]["capped"] is False
    assert pkgs[0]["gross_g"] > shipping.PACKAGE_CAP_G


def test_the_exemption_is_water_only_not_liquids():
    """Lipo-C and MIC are liquid, 270 g, and NOT exempt. The rule is about what
    is in the vial at a border, not about the form or the price — so it must
    never be widened by inference."""
    assert catalog.UNRESTRICTED_SKUS == frozenset({"BAC10"})
    liquid = {i.sku for i in catalog.ITEMS if i.is_liquid}
    assert liquid - catalog.UNRESTRICTED_SKUS == {"LC216", "MIC10"}
    for sku in ("LC216", "MIC10"):
        pkgs = shipping.split_packages([{"sku": sku, "kits": 84}])
        assert len(pkgs) == 14
        for pkg in pkgs:
            assert pkg["gross_g"] <= shipping.PACKAGE_CAP_G


def test_peptides_never_ride_in_an_uncapped_box():
    """The exemption must never leak: any package holding a restricted SKU stays
    under the cap, whatever else is in the order."""
    pkgs = shipping.split_packages([{"sku": "BAC10", "kits": 84},
                                    {"sku": "RT100", "kits": 30}])
    for pkg in pkgs:
        skus = {c["sku"] for c in pkg["contents"]}
        if not skus <= catalog.UNRESTRICTED_SKUS:
            assert pkg["gross_g"] <= shipping.PACKAGE_CAP_G, \
                f"box {pkg['index']} holds {skus} at {pkg['gross_g']}g"


def test_small_water_add_on_rides_along_instead_of_costing_a_second_box():
    """A separate water box always costs exactly one parcel, so it is only worth
    it when the water does not fit alongside. One kit does fit."""
    pkgs = shipping.split_packages([{"sku": "RT100", "kits": 4},
                                    {"sku": "BAC10", "kits": 1}])
    assert len(pkgs) == 1 and pkgs[0]["kits"] == 5


def test_bulk_water_plus_peptides_separates():
    pkgs = shipping.split_packages([{"sku": "RT100", "kits": 4},
                                    {"sku": "BAC10", "kits": 60}])
    assert len(pkgs) == 2
    water = [p for p in pkgs if not p["capped"]]
    assert len(water) == 1 and water[0]["kits"] == 60


def test_water_shares_one_uncapped_box_with_itself():
    """The exemption is a property of the package's CONTENTS, not of one SKU.
    This used to be asserted with a mixed bac+sterile order; sterile water left
    the catalog on 2026-09-03, so the same property is asserted on two bac-water
    lines. Restore the mixed version if sterile water comes back."""
    pkgs = shipping.split_packages([{"sku": "BAC10", "kits": 40},
                                    {"sku": "BAC10", "kits": 40}])
    assert len(pkgs) == 1 and pkgs[0]["kits"] == 80 and pkgs[0]["capped"] is False


def test_oversized_package_is_flagged_not_silently_shipped():
    """Uncapped does not mean unlimited — a courier still refuses a huge parcel.
    We flag rather than split, because splitting would contradict the rule."""
    small = shipping.split_packages([{"sku": "BAC10", "kits": 84}])[0]
    huge = shipping.split_packages([{"sku": "BAC10", "kits": 400}])[0]
    assert small["over_courier_limit"] is False
    assert huge["over_courier_limit"] is True


# ── The price change ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sku,name", [("BAC10", "Bacteriostatic Water")])
def test_water_price_is_the_same_everywhere(sku, name):
    """$12 -> $17 on 2026-08-31 to price freight into the product; -> $20 on
    Daniel's 2026-09-03 sheet. The NUMBER is not what this test defends — the
    baseline test does that. What matters here is that every place a customer
    can see it agrees, or Lily quotes one number and the sheet shows another.

    The parametrize list held sterile water too until 2026-09-03; it left the
    catalog, so the pair that had to stay level is now a single row."""
    expected = catalog.get(sku).list_price
    assert pricing.get_list_price(name, "10ml") == expected
    whatsapp = "\n".join(pricing.PRICE_LIST_MESSAGES)
    assert f"*{name}*" in whatsapp
    line = [ln for ln in whatsapp.splitlines() if ln.startswith(f"*{name}*")][0]
    assert f"${int(expected)}" in line, f"WhatsApp price list still says: {line}"


@pytest.mark.parametrize("sku", ["BAC10"])
def test_water_still_sells_above_cost(sku):
    """There is no 3x floor any more — nothing negotiates a price down. The
    question that is left is whether the sheet itself sells water at a loss, and
    it is asked at the deepest tier because that is the one that could."""
    item = catalog.get(sku)
    assert item.list_price >= item.reseller_price >= item.trading_price
    assert item.trading_price > item.cost


def test_epo_is_a_ten_vial_kit_like_everything_else():
    """The last spec without an 'x10' suffix — the DSIP bug's shape (§29)."""
    epo = catalog.find("EPO", "3000IU")
    assert epo is not None and epo.vials == 10
    assert epo.list_price == 170.0, "the 2026-09-03 sheet price for EP0"
    assert all(i.vials == 10 for i in catalog.ITEMS), \
        "every kit is ten vials; a new exception needs its own weight"


# ── Colloquial names ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed,spec,sku", [
    ("MT2", "10mg", "ML10"), ("Melanotan 2", "10mg", "ML10"),
    ("Melanotan II (MT2)", "10mg", "ML10"),
    ("Wolverine", "20mg", "BB20"),
    ("BPC-157 + TB-500 (Wolverine)", "20mg", "BB20"),
    ("Glow", "70mg", "GLOW70"), ("GLOW 70", "70mg", "GLOW70"),
    ("Klow", "80mg", "KLOW"),
    ("Bac Water", "10ml", "BAC10"), ("Bacteriostatic", "10ml", "BAC10"),
    ("5-Amino 1MQ", "10mg", "5AM10"),
])
def test_names_customers_actually_type_resolve(typed, spec, sku):
    item = catalog.find(typed, spec)
    assert item is not None and item.sku == sku, f"{typed!r} {spec} did not resolve to {sku}"
    assert pricing.get_list_price(typed, spec) == catalog.get(sku).list_price


def test_coa_page_names_all_resolve():
    """website/coa.html is the third hardcoded catalog copy (HANDOFF §29).
    Every product name it shows must resolve, or a customer quoting the COA page
    back at Lily produces a line we refuse."""
    html = (Path(__file__).resolve().parent.parent / "website" / "coa.html").read_text()
    rows = re.findall(
        r'sku:\s*\\?"([^"\\]+)\\?",\s*name:\s*\\?"([^"\\]+)\\?".*?spec:\s*\\?"([^"\\]+)\\?"',
        html)
    assert rows, "could not parse coa.html — did its format change?"
    bad = []
    for sku, name, spec in rows:
        item = catalog.find(name, spec)
        if item is None or item.sku != sku:
            bad.append((sku, name, spec, item.sku if item else None))
    assert bad == [], f"coa.html names that do not resolve to their own SKU: {bad}"
