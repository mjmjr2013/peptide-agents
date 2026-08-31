"""
Frozen price baseline — the snapshot test_catalog_regression.py's docstring
always promised but never actually had.

WHY THIS EXISTS (2026-08-31). test_no_price_moved compares get_list_price()
against price_image.CATEGORIES — the price sheet itself. That correctly catches
the two sources DRIFTING APART (the §29 bug), but it cannot catch a price being
edited, because editing the sheet moves the thing it compares against. The
suite written to stop a revenue leak would have sat green through a fat-fingered
zero on the customer price list.

This dict is a hardcoded snapshot of every list price at commit 1aa2007f, with
ONE deliberate edit applied and recorded below. It is the real ratchet: any
price change now has to be made here too, in a diff a human reads.

CHANGING A PRICE ON PURPOSE: edit the sheet, edit this dict, and add a line to
INTENTIONAL_CHANGES saying who decided and why. Never edit this file alone, and
never regenerate it wholesale to make a red test go green — that throws away
every price it was protecting.

Run:  python3 -m pytest tests/test_price_baseline.py -q
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog, price_image, pricing                    # noqa: E402


# Every deliberate price move since the baseline was frozen, newest last.
INTENTIONAL_CHANGES = [
    # (SKU, from, to, date, who decided, why)
    ("BAC10", 12.00, 17.00, "2026-08-31", "Jordan",
     "price the freight into the product: a bac water kit is 270 g against 75 g "
     "for a lyophilized one, so bulk water was buying free shipping at ~$44/kg "
     "of value. Chosen over denying free shipping by weight."),
    ("STW10", 12.00, 17.00, "2026-08-31", "Jordan",
     "matched to BAC10 the same day. Same 270 g of water at the same $2 cost, on "
     "the adjacent line of the price sheet — a gap between them would just move "
     "bulk water buyers one row down."),
]


# Products removed from the catalog since the baseline was frozen. A SKU leaves
# BASELINE only by being listed here — otherwise test_baseline_covers_every_sku
# would let a product quietly disappear from the price list, which is the same
# class of silent change the baseline exists to catch.
REMOVED_SKUS = [
    # (SKU, date, who decided, why)
    ("DR2",  "2026-08-31", "Jordan", "Dermorphin discontinued — not sold any more"),
    ("DR5",  "2026-08-31", "Jordan", "Dermorphin discontinued — not sold any more"),
    ("DR10", "2026-08-31", "Jordan", "Dermorphin discontinued — not sold any more"),
    ("DR20", "2026-08-31", "Jordan", "Dermorphin discontinued — not sold any more"),
]


BASELINE: dict[str, float] = {
    '10AD'      :   332.00,   # AOD-9604 10mg
    '2AD'       :   100.00,   # AOD-9604 2mg
    '2S10'      :   100.00,   # SS-31 10mg
    '2S50'      :   414.00,   # SS-31 50mg
    '50AM'      :   812.00,   # 5-Amino/MQ 50mg
    '5AD'       :   191.00,   # AOD-9604 5mg
    '5AM'       :   183.00,   # 5-Amino/MQ 5mg
    '5AM10'     :   261.00,   # 5-Amino/MQ 10mg
    'ACTH5'     :   183.00,   # ACTH 5mg
    'ADA10'     :   265.00,   # Admax 10mg
    'ADA5'      :   158.00,   # Admax 5mg
    'AE1'       :   243.00,   # ACE-031 1mg
    'AP2'       :    86.00,   # Adipotide 2mg
    'AP5'       :   166.00,   # Adipotide 5mg
    'AR50'      :    80.00,   # AICAR 50mg
    'BAC10'     :    17.00,   # Bacteriostatic Water 10ml
    'BB10'      :   108.00,   # BPC+TB Blend 10mg
    'BB20'      :   166.00,   # BPC+TB Blend 20mg
    'BC10'      :    72.00,   # BPC-157 10mg
    'BC5'       :    58.00,   # BPC-157 5mg
    'BT10'      :   140.00,   # TB-500 10mg
    'BT5'       :    92.00,   # TB-500 5mg
    'CAR10'     :   174.00,   # Cardiogen 10mg
    'CAR20'     :   298.00,   # Cardiogen 20mg
    'CART10'    :   191.00,   # Cartalax 10mg
    'CART20'    :   323.00,   # Cartalax 20mg
    'CD5'       :   166.00,   # CJC-1295 (w/ DAC) 5mg
    'CGL10'     :   181.00,   # Cagrilintide 10mg
    'CGL5'      :   115.00,   # Cagrilintide 5mg
    'CND10'     :   158.00,   # CJC-1295 (no DAC) 10mg
    'CND2'      :    42.00,   # CJC-1295 (no DAC) 2mg
    'CND5'      :    98.00,   # CJC-1295 (no DAC) 5mg
    'CP10'      :   114.00,   # CJC+Ipa Blend 10mg
    'CRY10'     :   158.00,   # Crystagen 10mg
    'CRY20'     :   290.00,   # Crystagen 20mg
    'CU100'     :   116.00,   # GHK-Cu 100mg
    'CU50'      :    71.00,   # GHK-Cu 50mg
    'DS10'      :   104.00,   # DSIP 10mg
    'DS2'       :    38.00,   # DSIP 2mg
    'DS5'       :    58.00,   # DSIP 5mg
    'DUL10'     :   514.00,   # Dulaglutide 10mg
    'DUL5'      :   315.00,   # Dulaglutide 5mg
    'EP0'       :   149.00,   # EPO 3000IU
    'ET10'      :    64.00,   # Epithalon 10mg
    'ET50'      :   240.00,   # Epithalon 50mg
    'F410'      :   629.00,   # FOXO4-DRI 10mg
    'F42'       :   232.00,   # FOXO4-DRI 2mg
    'F45'       :   373.00,   # FOXO4-DRI 5mg
    'FM2'       :    58.00,   # MGF 2mg
    'FMP2'      :   101.00,   # PEG MGF 2mg
    'FN1'       :   290.00,   # Follistatin 1mg
    'G10K'      :   164.00,   # HCG 10000IU
    'G210'      :    58.00,   # GHRP-2 10mg
    'G25'       :    34.00,   # GHRP-2 5mg
    'G5K'       :   104.00,   # HCG 5000IU
    'G610'      :    42.00,   # GHRP-6 10mg
    'G65'       :    38.00,   # GHRP-6 5mg
    'GLOW70'    :   154.00,   # BPC+TB+GHK Blend 70mg
    'GND2'      :    56.00,   # Gonadorelin 2mg
    'GTT'       :    87.00,   # Glutathione 600mg
    'GTT15'     :   166.00,   # Glutathione 1500mg
    'GTT4'      :    67.00,   # Glutathione 400mg
    'H10'       :    80.00,   # HGH 191AA 10iu
    'H15'       :   106.00,   # HGH 191AA 15iu
    'H8'        :    65.00,   # HGH 191AA 8iu
    'HUM10'     :   737.00,   # Humanin 10mg
    'HX2'       :    56.00,   # Hexarelin 2mg
    'HX5'       :   104.00,   # Hexarelin 5mg
    'IG1'       :   204.00,   # IGF-1 LR3 1mg
    'IGD'       :    77.00,   # IGF-DES 2mg
    'IP10'      :   100.00,   # Ipamorelin 10mg
    'IP2'       :    47.00,   # Ipamorelin 2mg
    'IP5'       :    58.00,   # Ipamorelin 5mg
    'KLOW'      :   220.00,   # BPC+TB+GHK+KPV 80mg
    'KPV10'     :   100.00,   # KPV 10mg
    'KPV5'      :    63.00,   # KPV 5mg
    'KS10'      :   116.00,   # KissPeptin-10 10mg
    'KS5'       :    72.00,   # KissPeptin-10 5mg
    'LC216'     :    92.00,   # Lipo-C 10ml
    'LGT10'     :   398.00,   # Liraglutide 10mg
    'LGT20'     :   737.00,   # Liraglutide 20mg
    'LGT5'      :   224.00,   # Liraglutide 5mg
    'MAT10'     :    82.00,   # Matrixyl 10mg
    'MDT10'     :   203.00,   # Mazdutide 10mg
    'MDT5'      :   192.00,   # Mazdutide 5mg
    'MEL10'     :   133.00,   # Melatonin 10mg
    'MIC10'     :   298.00,   # MIC (Lipo+B12) 10ml
    'ML10'      :   149.00,   # Melanotan II 10mg
    'MS10'      :    82.00,   # MOTS-c 10mg
    'MS20'      :   112.00,   # MOTS-c 20mg
    'MS40'      :   197.00,   # MOTS-c 40mg
    'NJ100'     :    55.00,   # NAD 100mg
    'NJ1000'    :   195.00,   # NAD 1000mg
    'NJ500'     :   135.00,   # NAD 500mg
    'NP810'     :   133.00,   # Snap-8 10mg
    'NP8100'    :   663.00,   # Snap-8 100mg
    'OT10'      :   232.00,   # Oxytocin 10mg
    'OT2'       :    72.00,   # Oxytocin 2mg
    'OT5'       :   125.00,   # Oxytocin 5mg
    'P41'       :    72.00,   # PT-141 10mg
    'PI10'      :   125.00,   # Pinealon 10mg
    'PI5'       :    75.00,   # Pinealon 5mg
    'PN5'       :   290.00,   # PNC-27 5mg
    'RA10'      :   149.00,   # Ara-290 10mg
    'RA16'      :   238.00,   # Ara-290 16mg
    'RT10'      :    95.00,   # Retatrutide 10mg
    'RT100'     :   894.00,   # Retatrutide 100mg
    'RT15'      :   142.00,   # Retatrutide 15mg
    'RT20'      :   189.00,   # Retatrutide 20mg
    'RT30'      :   274.00,   # Retatrutide 30mg
    'RT40'      :   365.00,   # Retatrutide 40mg
    'RT5'       :    67.00,   # Retatrutide 5mg
    'RT50'      :   456.00,   # Retatrutide 50mg
    'RT60'      :   547.00,   # Retatrutide 60mg
    'RT80'      :   729.00,   # Retatrutide 80mg
    'SK10'      :    92.00,   # Selank 10mg
    'SK5'       :    55.00,   # Selank 5mg
    'SLU5'      :   216.00,   # SLU-PP-322 5mg
    'SM10'      :    92.00,   # Semaglutide 10mg
    'SM15'      :   133.00,   # Semaglutide 15mg
    'SM20'      :   166.00,   # Semaglutide 20mg
    'SM30'      :   216.00,   # Semaglutide 30mg
    'SM40'      :   288.00,   # Semaglutide 40mg
    'SM5'       :    58.00,   # Semaglutide 5mg
    'SM50'      :   360.00,   # Semaglutide 50mg
    'SMO10'     :   119.00,   # Sermorelin 10mg
    'SMO5'      :    90.00,   # Sermorelin 5mg
    'STW10'     :    17.00,   # Sterile Water 10ml
    'SUR10'     :   820.00,   # Survodutide 10mg
    'SUR2'      :   265.00,   # Survodutide 2mg
    'SUR5'      :   480.00,   # Survodutide 5mg
    'TA10'      :   176.00,   # Thymosin Alpha-1 10mg
    'TA2'       :    73.00,   # Thymosin Alpha-1 2mg
    'TA5'       :   105.00,   # Thymosin Alpha-1 5mg
    'TR10'      :   108.00,   # Tirzepatide 10mg
    'TR100'     :   795.00,   # Tirzepatide 100mg
    'TR15'      :   162.00,   # Tirzepatide 15mg
    'TR20'      :   216.00,   # Tirzepatide 20mg
    'TR30'      :   274.00,   # Tirzepatide 30mg
    'TR40'      :   365.00,   # Tirzepatide 40mg
    'TR5'       :    71.00,   # Tirzepatide 5mg
    'TR50'      :   456.00,   # Tirzepatide 50mg
    'TR60'      :   547.00,   # Tirzepatide 60mg
    'TR80'      :   729.00,   # Tirzepatide 80mg
    'TSM10'     :   195.00,   # Tesamorelin 10mg
    'TSM2'      :    72.00,   # Tesamorelin 2mg
    'TSM20'     :   290.00,   # Tesamorelin 20mg
    'TSM5'      :   115.00,   # Tesamorelin 5mg
    'TY10'      :    77.00,   # Thymalin 10mg
    'XA10'      :    92.00,   # Semax 10mg
    'XA5'       :    53.00,   # Semax 5mg
}


def _sheet():
    for _cat, items in price_image.CATEGORIES:
        for sku, product, spec, pstr in items:
            yield sku, product, spec, float(re.sub(r"[^0-9.]", "", pstr))


SHEET = list(_sheet())


def test_baseline_covers_every_sku():
    """A new SKU must be added to the baseline, or it is unprotected."""
    sheet_skus = {sku for sku, _p, _s, _pr in SHEET}
    assert sheet_skus == set(BASELINE), (
        f"not in baseline: {sorted(sheet_skus - set(BASELINE))}; "
        f"in baseline but gone from the sheet: {sorted(set(BASELINE) - sheet_skus)}")


@pytest.mark.parametrize("sku,product,spec,sheet_price", SHEET)
def test_price_matches_the_frozen_baseline(sku, product, spec, sheet_price):
    """Every price a customer can see, pinned to a number a human approved."""
    expected = BASELINE[sku]
    assert abs(sheet_price - expected) < 0.005, (
        f"{sku} ({product} {spec}) is ${sheet_price:.2f} on the sheet but the "
        f"frozen baseline says ${expected:.2f}. If this change is intended, add "
        f"it to INTENTIONAL_CHANGES and update BASELINE in the same commit.")
    got = pricing.get_list_price(product, spec)
    assert got is not None and abs(got - expected) < 0.005, (
        f"{sku}: get_list_price returned {got}, baseline ${expected:.2f}")
    assert abs(catalog.get(sku).list_price - expected) < 0.005


def test_intentional_changes_are_reflected_in_the_baseline():
    """The log and the numbers cannot disagree."""
    for sku, _old, new, _date, _who, _why in INTENTIONAL_CHANGES:
        assert abs(BASELINE[sku] - new) < 0.005, (
            f"{sku} is logged as changed to ${new:.2f} but the baseline says "
            f"${BASELINE[sku]:.2f}")


def test_every_baseline_price_still_clears_its_floor():
    """A price edit must never dip under 3x cost — the guard §29 exists to keep."""
    under = [(sku, BASELINE[sku], catalog.get(sku).floor_price)
             for sku in BASELINE
             if catalog.get(sku).floor_price and BASELINE[sku] < catalog.get(sku).floor_price]
    assert under == [], f"priced below the 3x floor: {under}"


def test_removed_skus_are_really_gone():
    """A discontinued product must leave every catalog copy, not just the sheet —
    otherwise Lily still quotes it from her prompt and the order cannot be filled."""
    from core import pricing
    for sku, _date, _who, _why in REMOVED_SKUS:
        assert sku not in BASELINE, f"{sku} is logged as removed but is still in BASELINE"
        assert catalog.get(sku) is None, f"{sku} is logged as removed but is still in the catalog"
    gone = {r[0] for r in REMOVED_SKUS}
    assert not gone & {s for s, _p, _sp, _pr in SHEET}, "a removed SKU is still on the price sheet"


def test_a_removed_product_no_longer_prices():
    """It must fail CLOSED: an order line for it is refused and escalated to a
    human (HANDOFF §29), never quietly sold from a stale row."""
    from core import pricing
    assert pricing.get_list_price("Dermorphin", "10mg") is None
    assert catalog.find("Dermorphin", "10mg") is None
