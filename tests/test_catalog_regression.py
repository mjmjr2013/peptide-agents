"""
Catalog regression suite — 2026-08-30.

Locks in the four fixes made when the catalog audit found that five products were
spelled differently in core/pricing.py and core/price_image.py, and that an
unpriceable line silently shipped free.

The load-bearing test is test_no_price_moved: every price a customer can see must
be byte-identical to what it was before the fixes. BASELINE_LIST_PRICES below was
captured from the catalog BEFORE any change. If a test here fails, a price moved —
do not update the baseline to make it pass without confirming the new number is
intended.

Run:  python3 -m pytest tests/test_catalog_regression.py -q
"""
from __future__ import annotations
import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import pricing, price_image                      # noqa: E402
from core.aliases import canon                             # noqa: E402


# ── Baseline: every (SKU, product, spec) -> the exact list price on the customer
# price sheet, read from price_image.CATEGORIES. This is the source of truth the
# customer actually sees, so it is the right thing to pin.
def _sheet_rows():
    for _cat, items in price_image.CATEGORIES:
        for sku, product, spec, pstr in items:
            yield sku, product, spec, float(re.sub(r"[^0-9.]", "", pstr))


SHEET_ROWS = list(_sheet_rows())

# Products that were spelled two ways. Both spellings must now resolve, to the
# same row, at the same price.
DIVERGENT_NAMES = [
    ("BPC+GHK-Cu+TB Blend",     "BPC+TB+GHK Blend",       "70mg", "GLOW70"),
    ("BPC+TB+GHK-Cu+KPV Blend", "BPC+TB+GHK+KPV",         "80mg", "KLOW"),
    ("CJC-1295 (with DAC)",     "CJC-1295 (w/ DAC)",      "5mg",  "CD5"),
    ("CJC+Ipamorelin Blend",    "CJC+Ipa Blend",          "10mg", "CP10"),
    ("MIC (Lipo-C+B12)",        "MIC (Lipo+B12)",         "10ml", "MIC10"),
]


# ── 1. The load-bearing test ─────────────────────────────────────────────────
@pytest.mark.parametrize("sku,product,spec,sheet_price", SHEET_ROWS,
                         ids=[r[0] for r in SHEET_ROWS])
def test_no_price_moved(sku, product, spec, sheet_price):
    """Every sheet row still prices to exactly what the sheet shows."""
    got = pricing.get_list_price(product, spec)
    assert got is not None, f"{sku} {product} {spec} no longer prices at all"
    assert abs(got - sheet_price) < 0.005, (
        f"{sku} {product} {spec}: sheet says ${sheet_price:.2f}, "
        f"get_list_price now returns ${got:.2f}")


# ── 2. Nothing on the sheet can be unpriceable ───────────────────────────────
def test_every_sheet_row_prices():
    """No SKU may resolve to None — that is the bug that shipped kits free."""
    broken = [(sku, product, spec) for sku, product, spec, _ in SHEET_ROWS
              if pricing.get_list_price(product, spec) is None]
    assert not broken, f"{len(broken)} sheet SKUs do not price: {broken}"


def test_every_sheet_row_has_a_floor():
    """A line with no floor can be discounted below cost."""
    broken = [(sku, product, spec) for sku, product, spec, _ in SHEET_ROWS
              if pricing.get_floor_price(product, spec) is None]
    assert not broken, f"{len(broken)} sheet SKUs have no floor price: {broken}"


def test_no_sheet_price_is_below_its_own_floor():
    """The sheet must never sell under the 3x cost floor the guard enforces.
    This is what flagged the stale Sermorelin Acetate rows: pricing off them put
    SMO5 at 2.4x cost."""
    under = []
    for sku, product, spec, sheet_price in SHEET_ROWS:
        floor = pricing.get_floor_price(product, spec)
        if floor is not None and sheet_price < math.ceil(floor) - 0.005:
            under.append((sku, product, spec, sheet_price, floor))
    assert not under, f"sheet price below floor: {under}"


# ── 3. Spelling robustness — the class of bug, not the five instances ────────
def _spec_variants(spec: str) -> list[str]:
    return [spec, spec.replace("mg", " mg"), f"{spec} x10", f"{spec}x10",
            spec.upper(), spec.lower()]


def test_plausible_spellings_all_price():
    """Fuzz the spec formats Lily plausibly emits. Before the fix, 36 of 930
    spellings drawn from our own price sheet resolved to None (3.9%, 8 SKUs,
    two unrelated root causes)."""
    failures = []
    for sku, product, spec, _ in SHEET_ROWS:
        for variant in _spec_variants(spec):
            if pricing.get_list_price(product, variant) is None:
                failures.append((sku, product, variant))
    assert not failures, (
        f"{len(failures)} plausible spellings price at None: {failures[:15]}")


@pytest.mark.parametrize("cost_name,sheet_name,spec,sku", DIVERGENT_NAMES,
                         ids=[d[3] for d in DIVERGENT_NAMES])
def test_both_spellings_resolve_identically(cost_name, sheet_name, spec, sku):
    """Either name must give BOTH a price and a SKU. Before the alias layer, a
    line was either priced with a null SKU or SKU'd at no price — never both."""
    for name in (cost_name, sheet_name):
        assert pricing.get_list_price(name, spec) is not None, \
            f"{name!r} {spec} does not price"
        assert pricing.get_floor_price(name, spec) is not None, \
            f"{name!r} {spec} has no floor"
        assert price_image.get_sku(name, spec) == sku, \
            f"{name!r} {spec} -> SKU {price_image.get_sku(name, spec)!r}, expected {sku!r}"
    assert pricing.get_list_price(cost_name, spec) == pricing.get_list_price(sheet_name, spec)


def test_dsip_specs_use_the_standard_x10_form():
    """DSIP rows lacked the 'x10' suffix every other row uses, so a query of
    '10mg x10' tied three candidates and returned None."""
    dsip = [i for i in pricing.CATALOG if i["product"] == "DSIP"]
    assert dsip, "DSIP rows vanished from the catalog"
    for row in dsip:
        assert row["spec"].endswith(" x10"), f"DSIP spec {row['spec']!r} is non-standard"
    for spec in ("2mg", "5mg", "10mg"):
        for variant in (spec, f"{spec} x10", f"{spec}x10"):
            assert pricing.get_list_price("DSIP", variant) is not None, \
                f"DSIP {variant!r} does not price"


# ── 4. Sermorelin collapse ───────────────────────────────────────────────────
def test_sermorelin_has_one_cost_basis():
    """Two live cost bases 2.5x apart is a trap regardless of which is right."""
    names = {i["product"] for i in pricing.CATALOG if "sermorelin" in i["product"].lower()}
    assert names == {"Sermorelin"}, f"expected only 'Sermorelin', found {names}"


@pytest.mark.parametrize("spec,expected", [("5mg", 90.0), ("10mg", 119.0)])
def test_sermorelin_acetate_resolves_to_the_sheet_price(spec, expected):
    """The removed name still resolves — via the alias — and now to the price the
    customer sheet has always shown, not the stale $224/$398."""
    for name in ("Sermorelin", "Sermorelin Acetate"):
        assert pricing.get_list_price(name, spec) == expected, \
            f"{name} {spec} -> {pricing.get_list_price(name, spec)}, expected {expected}"


# ── 5. The alias layer itself ────────────────────────────────────────────────
def test_alias_canon_folds_divergent_names():
    for cost_name, sheet_name, _spec, _sku in DIVERGENT_NAMES:
        assert canon(cost_name) == canon(sheet_name), \
            f"{cost_name!r} and {sheet_name!r} do not canonicalize together"


def test_canon_passes_unknown_products_through_unchanged():
    """An unknown product must normalize plainly — the alias table can never
    break a product it does not know about."""
    assert canon("Retatrutide") == "retatrutide"
    assert canon("Some Product We Have Never Sold") == "someproductwehaveneversold"


def test_aliases_do_not_collide_with_real_products():
    """No alias may fold two DIFFERENT catalog products into one key."""
    seen: dict[str, str] = {}
    for item in pricing.CATALOG:
        key = canon(item["product"])
        if key in seen and seen[key] != item["product"]:
            pytest.fail(f"alias collision: {seen[key]!r} and {item['product']!r} "
                        f"both canonicalize to {key!r}")
        seen[key] = item["product"]


# ── 6. deals.py must still price end to end ──────────────────────────────────
def test_every_deal_line_prices():
    """deals.py uses the price-sheet spellings. Three DIEGO26 lines returned None
    before the alias layer."""
    from core import deals
    broken = []
    for code, deal in deals.DEALS.items():
        for sku, product, spec, _kits, _wl in deal["items"]:
            if pricing.get_list_price(product, spec) is None:
                broken.append((code, sku, product, spec))
    assert not broken, f"deal lines that do not price: {broken}"


def test_every_deal_line_has_a_sku_that_exists():
    from core import deals
    broken = []
    for code, deal in deals.DEALS.items():
        for sku, product, spec, _kits, _wl in deal["items"]:
            if price_image.get_sku(product, spec) != sku:
                broken.append((code, sku, product, spec, price_image.get_sku(product, spec)))
    assert not broken, f"deal lines whose SKU does not round-trip: {broken}"
