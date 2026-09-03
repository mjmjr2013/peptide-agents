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
    # 2026-09-03: the whole sheet was replaced, so the per-SKU log would be 147
    # near-identical lines. The moves are enumerated in PRICE_MOVES_2026_09_03
    # instead and checked against the previous baseline; this entry records the
    # DECISION, and BAC10 is called out because it undoes one of the two above.
    ("BAC10", 17.00, 20.00, "2026-09-03", "Jordan",
     "Daniel's new China warehouse sheet. Note this partly undoes the 2026-08-31 "
     "freight-pricing move and sterile water is no longer alongside it to stay "
     "level with — see REMOVED_SKUS."),
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
    # Daniel's 2026-09-03 sheets re-listed all four Dermorphin doses. Jordan kept
    # them out, so they stay removed here and the sheets were edited to match.
    ("RT80",  "2026-09-03", "Jordan",
     "not on Daniel's new sheets; pulled pending confirmation the lab can supply it"),
    ("TR80",  "2026-09-03", "Jordan",
     "not on Daniel's new sheets; pulled pending confirmation the lab can supply it"),
    ("STW10", "2026-09-03", "Jordan",
     "sterile water is not on Daniel's new sheets; pulled until the lab confirms "
     "they can sell it. Its bac-water price parity and its cap exemption went with it."),
]


# ── The baseline that ran until 2026-09-03 ──────────────────────────────────
# KEPT, NOT DELETED. Daniel's new sheets moved almost every price at once, and
# "regenerate the baseline" is exactly the move CLAUDE.md forbids — it throws
# away the thing the ratchet was protecting. So the old snapshot stays here,
# frozen, and `test_every_price_move_is_accounted_for` asserts that every single
# difference between it and today's BASELINE appears in PRICE_MOVES_2026_09_03
# below. The change is wholesale but it is still enumerated, and a 152nd price
# that moved without being listed still fails.
BASELINE_2026_08_31: dict[str, float] = {
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


# Every price that moved on 2026-09-03, from Daniel's new warehouse sheets
# (Harrison & Daniel thread), after the duplicate and discontinued rows were
# removed on Jordan's instruction. GENERATED FROM THE DIFF, not hand-typed —
# but it is checked against both baselines, so a wrong entry fails.
PRICE_MOVES_2026_09_03: list[tuple] = [
    # (SKU, from, to)
    ('10AD'      ,   332.00,   305.00),   # AOD-9604 10mg
    ('2AD'       ,   100.00,    95.00),   # AOD-9604 2mg
    ('2S10'      ,   100.00,   115.00),   # SS-31 10mg
    ('2S50'      ,   414.00,   330.00),   # SS-31 50mg
    ('50AM'      ,   812.00,   135.00),   # 5-Amino/MQ 50mg
    ('5AD'       ,   191.00,   180.00),   # AOD-9604 5mg
    ('5AM'       ,   183.00,    50.00),   # 5-Amino/MQ 5mg
    ('5AM10'     ,   261.00,    85.00),   # 5-Amino/MQ 10mg
    ('ACTH5'     ,   183.00,   210.00),   # ACTH 5mg
    ('ADA10'     ,   265.00,   180.00),   # Admax 10mg
    ('ADA5'      ,   158.00,   140.00),   # Admax 5mg
    ('AE1'       ,   243.00,   280.00),   # ACE-031 1mg
    ('AP2'       ,    86.00,   100.00),   # Adipotide 2mg
    ('AP5'       ,   166.00,   190.00),   # Adipotide 5mg
    ('AR50'      ,    80.00,    85.00),   # AICAR 50mg
    ('BAC10'     ,    17.00,    20.00),   # Bacteriostatic Water 10ml
    ('BB10'      ,   108.00,   120.00),   # BPC+TB Blend 10mg
    ('BB20'      ,   166.00,   190.00),   # BPC+TB Blend 20mg
    ('BC10'      ,    72.00,    80.00),   # BPC-157 10mg
    ('BC5'       ,    58.00,    65.00),   # BPC-157 5mg
    ('BT10'      ,   140.00,   160.00),   # TB-500 10mg
    ('BT5'       ,    92.00,   105.00),   # TB-500 5mg
    ('CAR10'     ,   174.00,   200.00),   # Cardiogen 10mg
    ('CAR20'     ,   298.00,   340.00),   # Cardiogen 20mg
    ('CART10'    ,   191.00,   150.00),   # Cartalax 10mg
    ('CART20'    ,   323.00,   220.00),   # Cartalax 20mg
    ('CD5'       ,   166.00,   175.00),   # CJC-1295 (with DAC) 5mg
    ('CGL10'     ,   181.00,   205.00),   # Cagrilintide 10mg
    ('CGL5'      ,   115.00,   130.00),   # Cagrilintide 5mg
    ('CND10'     ,   158.00,   180.00),   # CJC-1295 (no DAC) 10mg
    ('CND2'      ,    42.00,    50.00),   # CJC-1295 (no DAC) 2mg
    ('CND5'      ,    98.00,   110.00),   # CJC-1295 (no DAC) 5mg
    ('CP10'      ,   114.00,   130.00),   # CJC+Ipamorelin Blend 10mg
    ('CRY10'     ,   158.00,   180.00),   # Crystagen 10mg
    ('CRY20'     ,   290.00,   330.00),   # Crystagen 20mg
    ('CU100'     ,   116.00,    95.00),   # GHK-Cu 100mg
    ('CU50'      ,    71.00,    65.00),   # GHK-Cu 50mg
    ('DS10'      ,   104.00,   120.00),   # DSIP 10mg
    ('DS2'       ,    38.00,    45.00),   # DSIP 2mg
    ('DS5'       ,    58.00,    65.00),   # DSIP 5mg
    ('DUL10'     ,   514.00,   590.00),   # Dulaglutide 10mg
    ('DUL5'      ,   315.00,   360.00),   # Dulaglutide 5mg
    ('EP0'       ,   149.00,   170.00),   # EPO 3000IU
    ('ET10'      ,    64.00,    75.00),   # Epithalon 10mg
    ('ET50'      ,   240.00,   275.00),   # Epithalon 50mg
    ('F410'      ,   629.00,   265.00),   # FOXO4-DRI 10mg
    ('F42'       ,   232.00,    85.00),   # FOXO4-DRI 2mg
    ('F45'       ,   373.00,   210.00),   # FOXO4-DRI 5mg
    ('FM2'       ,    58.00,    65.00),   # MGF 2mg
    ('FMP2'      ,   101.00,   115.00),   # PEG MGF 2mg
    ('FN1'       ,   290.00,    85.00),   # Follistatin 1mg
    ('G10K'      ,   164.00,   125.00),   # HCG 10000IU
    ('G210'      ,    58.00,    65.00),   # GHRP-2 10mg
    ('G25'       ,    34.00,    40.00),   # GHRP-2 5mg
    ('G5K'       ,   104.00,   120.00),   # HCG 5000IU
    ('G610'      ,    42.00,    50.00),   # GHRP-6 10mg
    ('G65'       ,    38.00,    45.00),   # GHRP-6 5mg
    ('GLOW70'    ,   154.00,   175.00),   # BPC+GHK-Cu+TB Blend 70mg
    ('GND2'      ,    56.00,    55.00),   # Gonadorelin 2mg
    ('GTT'       ,    87.00,    75.00),   # Glutathione 600mg
    ('GTT15'     ,   166.00,   125.00),   # Glutathione 1500mg
    ('GTT4'      ,    67.00,    55.00),   # Glutathione 400mg
    ('H10'       ,    80.00,    90.00),   # HGH 191AA 10iu
    ('H15'       ,   106.00,   120.00),   # HGH 191AA 15iu
    ('H8'        ,    65.00,    75.00),   # HGH 191AA 8iu
    ('HUM10'     ,   737.00,   245.00),   # Humanin 10mg
    ('HX2'       ,    56.00,    65.00),   # Hexarelin 2mg
    ('HX5'       ,   104.00,   120.00),   # Hexarelin 5mg
    ('IG1'       ,   204.00,   235.00),   # IGF-1 LR3 1mg
    ('IGD'       ,    77.00,    85.00),   # IGF-DES 2mg
    ('IP10'      ,   100.00,    95.00),   # Ipamorelin 10mg
    ('IP2'       ,    47.00,    50.00),   # Ipamorelin 2mg
    ('IP5'       ,    58.00,    65.00),   # Ipamorelin 5mg
    ('KLOW'      ,   220.00,   215.00),   # BPC+TB+GHK-Cu+KPV Blend 80mg
    ('KPV10'     ,   100.00,   115.00),   # KPV 10mg
    ('KPV5'      ,    63.00,    70.00),   # KPV 5mg
    ('KS10'      ,   116.00,   130.00),   # KissPeptin-10 10mg
    ('KS5'       ,    72.00,    85.00),   # KissPeptin-10 5mg
    ('LC216'     ,    92.00,   105.00),   # Lipo-C 10ml
    ('LGT10'     ,   398.00,   455.00),   # Liraglutide 10mg
    ('LGT20'     ,   737.00,   845.00),   # Liraglutide 20mg
    ('LGT5'      ,   224.00,   255.00),   # Liraglutide 5mg
    ('MAT10'     ,    82.00,    95.00),   # Matrixyl 10mg
    ('MDT10'     ,   203.00,   235.00),   # Mazdutide 10mg
    ('MDT5'      ,   192.00,   220.00),   # Mazdutide 5mg
    ('MEL10'     ,   133.00,   150.00),   # Melatonin 10mg
    ('MIC10'     ,   298.00,   340.00),   # MIC (Lipo-C+B12) 10ml
    ('ML10'      ,   149.00,   105.00),   # Melanotan II 10mg
    ('MS10'      ,    82.00,    95.00),   # MOTS-c 10mg
    ('MS20'      ,   112.00,   130.00),   # MOTS-c 20mg
    ('MS40'      ,   197.00,   225.00),   # MOTS-c 40mg
    ('NJ100'     ,    55.00,    85.00),   # NAD 100mg
    ('NJ1000'    ,   195.00,   210.00),   # NAD 1000mg
    ('NJ500'     ,   135.00,   150.00),   # NAD 500mg
    ('NP810'     ,   133.00,    65.00),   # Snap-8 10mg
    ('NP8100'    ,   663.00,   430.00),   # Snap-8 100mg
    ('OT10'      ,   232.00,   170.00),   # Oxytocin 10mg
    ('OT2'       ,    72.00,    80.00),   # Oxytocin 2mg
    ('OT5'       ,   125.00,    95.00),   # Oxytocin 5mg
    ('P41'       ,    72.00,    80.00),   # PT-141 10mg
    ('PI10'      ,   125.00,   115.00),   # Pinealon 10mg
    ('PI5'       ,    75.00,    85.00),   # Pinealon 5mg
    ('PN5'       ,   290.00,   330.00),   # PNC-27 5mg
    ('RA10'      ,   149.00,   170.00),   # Ara-290 10mg
    ('RA16'      ,   238.00,   285.00),   # Ara-290 16mg
    ('RT100'     ,   894.00,   430.00),   # Retatrutide 100mg
    ('RT15'      ,   142.00,   160.00),   # Retatrutide 15mg
    ('RT20'      ,   189.00,   180.00),   # Retatrutide 20mg
    ('RT30'      ,   274.00,   240.00),   # Retatrutide 30mg
    ('RT40'      ,   365.00,   305.00),   # Retatrutide 40mg
    ('RT5'       ,    67.00,    65.00),   # Retatrutide 5mg
    ('RT50'      ,   456.00,   340.00),   # Retatrutide 50mg
    ('RT60'      ,   547.00,   380.00),   # Retatrutide 60mg
    ('SK10'      ,    92.00,   105.00),   # Selank 10mg
    ('SK5'       ,    55.00,    65.00),   # Selank 5mg
    ('SLU5'      ,   216.00,   135.00),   # SLU-PP-322 5mg
    ('SM10'      ,    92.00,   115.00),   # Semaglutide 10mg
    ('SM15'      ,   133.00,   125.00),   # Semaglutide 15mg
    ('SM20'      ,   166.00,   160.00),   # Semaglutide 20mg
    ('SM30'      ,   216.00,   210.00),   # Semaglutide 30mg
    ('SM40'      ,   288.00,   285.00),   # Semaglutide 40mg
    ('SM5'       ,    58.00,    65.00),   # Semaglutide 5mg
    ('SM50'      ,   360.00,   340.00),   # Semaglutide 50mg
    ('SMO10'     ,   119.00,   135.00),   # Sermorelin 10mg
    ('SMO5'      ,    90.00,   105.00),   # Sermorelin 5mg
    ('SUR10'     ,   820.00,   550.00),   # Survodutide 10mg
    ('SUR2'      ,   265.00,   305.00),   # Survodutide 2mg
    ('SUR5'      ,   480.00,   420.00),   # Survodutide 5mg
    ('TA10'      ,   176.00,   200.00),   # Thymosin Alpha-1 10mg
    ('TA2'       ,    73.00,    85.00),   # Thymosin Alpha-1 2mg
    ('TA5'       ,   105.00,   120.00),   # Thymosin Alpha-1 5mg
    ('TR10'      ,   108.00,   115.00),   # Tirzepatide 10mg
    ('TR100'     ,   795.00,   340.00),   # Tirzepatide 100mg
    ('TR15'      ,   162.00,   140.00),   # Tirzepatide 15mg
    ('TR20'      ,   216.00,   170.00),   # Tirzepatide 20mg
    ('TR30'      ,   274.00,   200.00),   # Tirzepatide 30mg
    ('TR40'      ,   365.00,   230.00),   # Tirzepatide 40mg
    ('TR5'       ,    71.00,    65.00),   # Tirzepatide 5mg
    ('TR50'      ,   456.00,   265.00),   # Tirzepatide 50mg
    ('TR60'      ,   547.00,   305.00),   # Tirzepatide 60mg
    ('TSM10'     ,   195.00,   225.00),   # Tesamorelin 10mg
    ('TSM2'      ,    72.00,    80.00),   # Tesamorelin 2mg
    ('TSM20'     ,   290.00,   330.00),   # Tesamorelin 20mg
    ('TSM5'      ,   115.00,    95.00),   # Tesamorelin 5mg
    ('TY10'      ,    77.00,    85.00),   # Thymalin 10mg
    ('XA10'      ,    92.00,   105.00),   # Semax 10mg
    ('XA5'       ,    53.00,    60.00),   # Semax 5mg
]


# New on the 2026-09-03 sheets.
ADDED_SKUS_2026_09_03 = ['SM100', 'SM60', 'TR120']


# ── Today's baseline: the China standard (1-24 kit) price ───────────────────
# This is the number printed on the customer price sheet, so it is the one a
# buyer can hold us to.
BASELINE: dict[str, float] = {
    '10AD'      :   305.00,   # AOD-9604 10mg
    '2AD'       :    95.00,   # AOD-9604 2mg
    '2S10'      :   115.00,   # SS-31 10mg
    '2S50'      :   330.00,   # SS-31 50mg
    '50AM'      :   135.00,   # 5-Amino/MQ 50mg
    '5AD'       :   180.00,   # AOD-9604 5mg
    '5AM'       :    50.00,   # 5-Amino/MQ 5mg
    '5AM10'     :    85.00,   # 5-Amino/MQ 10mg
    'ACTH5'     :   210.00,   # ACTH 5mg
    'ADA10'     :   180.00,   # Admax 10mg
    'ADA5'      :   140.00,   # Admax 5mg
    'AE1'       :   280.00,   # ACE-031 1mg
    'AP2'       :   100.00,   # Adipotide 2mg
    'AP5'       :   190.00,   # Adipotide 5mg
    'AR50'      :    85.00,   # AICAR 50mg
    'BAC10'     :    20.00,   # Bacteriostatic Water 10ml
    'BB10'      :   120.00,   # BPC+TB Blend 10mg
    'BB20'      :   190.00,   # BPC+TB Blend 20mg
    'BC10'      :    80.00,   # BPC-157 10mg
    'BC5'       :    65.00,   # BPC-157 5mg
    'BT10'      :   160.00,   # TB-500 10mg
    'BT5'       :   105.00,   # TB-500 5mg
    'CAR10'     :   200.00,   # Cardiogen 10mg
    'CAR20'     :   340.00,   # Cardiogen 20mg
    'CART10'    :   150.00,   # Cartalax 10mg
    'CART20'    :   220.00,   # Cartalax 20mg
    'CD5'       :   175.00,   # CJC-1295 (with DAC) 5mg
    'CGL10'     :   205.00,   # Cagrilintide 10mg
    'CGL5'      :   130.00,   # Cagrilintide 5mg
    'CND10'     :   180.00,   # CJC-1295 (no DAC) 10mg
    'CND2'      :    50.00,   # CJC-1295 (no DAC) 2mg
    'CND5'      :   110.00,   # CJC-1295 (no DAC) 5mg
    'CP10'      :   130.00,   # CJC+Ipamorelin Blend 10mg
    'CRY10'     :   180.00,   # Crystagen 10mg
    'CRY20'     :   330.00,   # Crystagen 20mg
    'CU100'     :    95.00,   # GHK-Cu 100mg
    'CU50'      :    65.00,   # GHK-Cu 50mg
    'DS10'      :   120.00,   # DSIP 10mg
    'DS2'       :    45.00,   # DSIP 2mg
    'DS5'       :    65.00,   # DSIP 5mg
    'DUL10'     :   590.00,   # Dulaglutide 10mg
    'DUL5'      :   360.00,   # Dulaglutide 5mg
    'EP0'       :   170.00,   # EPO 3000IU
    'ET10'      :    75.00,   # Epithalon 10mg
    'ET50'      :   275.00,   # Epithalon 50mg
    'F410'      :   265.00,   # FOXO4-DRI 10mg
    'F42'       :    85.00,   # FOXO4-DRI 2mg
    'F45'       :   210.00,   # FOXO4-DRI 5mg
    'FM2'       :    65.00,   # MGF 2mg
    'FMP2'      :   115.00,   # PEG MGF 2mg
    'FN1'       :    85.00,   # Follistatin 1mg
    'G10K'      :   125.00,   # HCG 10000IU
    'G210'      :    65.00,   # GHRP-2 10mg
    'G25'       :    40.00,   # GHRP-2 5mg
    'G5K'       :   120.00,   # HCG 5000IU
    'G610'      :    50.00,   # GHRP-6 10mg
    'G65'       :    45.00,   # GHRP-6 5mg
    'GLOW70'    :   175.00,   # BPC+GHK-Cu+TB Blend 70mg
    'GND2'      :    55.00,   # Gonadorelin 2mg
    'GTT'       :    75.00,   # Glutathione 600mg
    'GTT15'     :   125.00,   # Glutathione 1500mg
    'GTT4'      :    55.00,   # Glutathione 400mg
    'H10'       :    90.00,   # HGH 191AA 10iu
    'H15'       :   120.00,   # HGH 191AA 15iu
    'H8'        :    75.00,   # HGH 191AA 8iu
    'HUM10'     :   245.00,   # Humanin 10mg
    'HX2'       :    65.00,   # Hexarelin 2mg
    'HX5'       :   120.00,   # Hexarelin 5mg
    'IG1'       :   235.00,   # IGF-1 LR3 1mg
    'IGD'       :    85.00,   # IGF-DES 2mg
    'IP10'      :    95.00,   # Ipamorelin 10mg
    'IP2'       :    50.00,   # Ipamorelin 2mg
    'IP5'       :    65.00,   # Ipamorelin 5mg
    'KLOW'      :   215.00,   # BPC+TB+GHK-Cu+KPV Blend 80mg
    'KPV10'     :   115.00,   # KPV 10mg
    'KPV5'      :    70.00,   # KPV 5mg
    'KS10'      :   130.00,   # KissPeptin-10 10mg
    'KS5'       :    85.00,   # KissPeptin-10 5mg
    'LC216'     :   105.00,   # Lipo-C 10ml
    'LGT10'     :   455.00,   # Liraglutide 10mg
    'LGT20'     :   845.00,   # Liraglutide 20mg
    'LGT5'      :   255.00,   # Liraglutide 5mg
    'MAT10'     :    95.00,   # Matrixyl 10mg
    'MDT10'     :   235.00,   # Mazdutide 10mg
    'MDT5'      :   220.00,   # Mazdutide 5mg
    'MEL10'     :   150.00,   # Melatonin 10mg
    'MIC10'     :   340.00,   # MIC (Lipo-C+B12) 10ml
    'ML10'      :   105.00,   # Melanotan II 10mg
    'MS10'      :    95.00,   # MOTS-c 10mg
    'MS20'      :   130.00,   # MOTS-c 20mg
    'MS40'      :   225.00,   # MOTS-c 40mg
    'NJ100'     :    85.00,   # NAD 100mg
    'NJ1000'    :   210.00,   # NAD 1000mg
    'NJ500'     :   150.00,   # NAD 500mg
    'NP810'     :    65.00,   # Snap-8 10mg
    'NP8100'    :   430.00,   # Snap-8 100mg
    'OT10'      :   170.00,   # Oxytocin 10mg
    'OT2'       :    80.00,   # Oxytocin 2mg
    'OT5'       :    95.00,   # Oxytocin 5mg
    'P41'       :    80.00,   # PT-141 10mg
    'PI10'      :   115.00,   # Pinealon 10mg
    'PI5'       :    85.00,   # Pinealon 5mg
    'PN5'       :   330.00,   # PNC-27 5mg
    'RA10'      :   170.00,   # Ara-290 10mg
    'RA16'      :   285.00,   # Ara-290 16mg
    'RT10'      :    95.00,   # Retatrutide 10mg
    'RT100'     :   430.00,   # Retatrutide 100mg
    'RT15'      :   160.00,   # Retatrutide 15mg
    'RT20'      :   180.00,   # Retatrutide 20mg
    'RT30'      :   240.00,   # Retatrutide 30mg
    'RT40'      :   305.00,   # Retatrutide 40mg
    'RT5'       :    65.00,   # Retatrutide 5mg
    'RT50'      :   340.00,   # Retatrutide 50mg
    'RT60'      :   380.00,   # Retatrutide 60mg
    'SK10'      :   105.00,   # Selank 10mg
    'SK5'       :    65.00,   # Selank 5mg
    'SLU5'      :   135.00,   # SLU-PP-322 5mg
    'SM10'      :   115.00,   # Semaglutide 10mg
    'SM100'     :   455.00,   # Semaglutide 100mg
    'SM15'      :   125.00,   # Semaglutide 15mg
    'SM20'      :   160.00,   # Semaglutide 20mg
    'SM30'      :   210.00,   # Semaglutide 30mg
    'SM40'      :   285.00,   # Semaglutide 40mg
    'SM5'       :    65.00,   # Semaglutide 5mg
    'SM50'      :   340.00,   # Semaglutide 50mg
    'SM60'      :   400.00,   # Semaglutide 60mg
    'SMO10'     :   135.00,   # Sermorelin 10mg
    'SMO5'      :   105.00,   # Sermorelin 5mg
    'SUR10'     :   550.00,   # Survodutide 10mg
    'SUR2'      :   305.00,   # Survodutide 2mg
    'SUR5'      :   420.00,   # Survodutide 5mg
    'TA10'      :   200.00,   # Thymosin Alpha-1 10mg
    'TA2'       :    85.00,   # Thymosin Alpha-1 2mg
    'TA5'       :   120.00,   # Thymosin Alpha-1 5mg
    'TR10'      :   115.00,   # Tirzepatide 10mg
    'TR100'     :   340.00,   # Tirzepatide 100mg
    'TR120'     :   380.00,   # Tirzepatide 120mg
    'TR15'      :   140.00,   # Tirzepatide 15mg
    'TR20'      :   170.00,   # Tirzepatide 20mg
    'TR30'      :   200.00,   # Tirzepatide 30mg
    'TR40'      :   230.00,   # Tirzepatide 40mg
    'TR5'       :    65.00,   # Tirzepatide 5mg
    'TR50'      :   265.00,   # Tirzepatide 50mg
    'TR60'      :   305.00,   # Tirzepatide 60mg
    'TSM10'     :   225.00,   # Tesamorelin 10mg
    'TSM2'      :    80.00,   # Tesamorelin 2mg
    'TSM20'     :   330.00,   # Tesamorelin 20mg
    'TSM5'      :    95.00,   # Tesamorelin 5mg
    'TY10'      :    85.00,   # Thymalin 10mg
    'XA10'      :   105.00,   # Semax 10mg
    'XA5'       :    60.00,   # Semax 5mg
}


# The other two China tiers. Pinned for the same reason as the standard price:
# a transposed column in a regenerated core/price_sheets.py would otherwise
# quietly sell 100-kit orders at the 25-kit price, or worse.
RESELLER_BASELINE: dict[str, float] = {
    '10AD'      :   213.00,
    '2AD'       :    66.00,
    '2S10'      :    80.00,
    '2S50'      :   231.00,
    '50AM'      :    94.00,
    '5AD'       :   126.00,
    '5AM'       :    35.00,
    '5AM10'     :    59.00,
    'ACTH5'     :   147.00,
    'ADA10'     :   126.00,
    'ADA5'      :    98.00,
    'AE1'       :   196.00,
    'AP2'       :    70.00,
    'AP5'       :   133.00,
    'AR50'      :    59.00,
    'BAC10'     :    14.00,
    'BB10'      :    84.00,
    'BB20'      :   133.00,
    'BC10'      :    56.00,
    'BC5'       :    45.00,
    'BT10'      :   112.00,
    'BT5'       :    73.00,
    'CAR10'     :   140.00,
    'CAR20'     :   238.00,
    'CART10'    :   105.00,
    'CART20'    :   154.00,
    'CD5'       :   122.00,
    'CGL10'     :   143.00,
    'CGL5'      :    91.00,
    'CND10'     :   126.00,
    'CND2'      :    35.00,
    'CND5'      :    77.00,
    'CP10'      :    91.00,
    'CRY10'     :   126.00,
    'CRY20'     :   231.00,
    'CU100'     :    66.00,
    'CU50'      :    45.00,
    'DS10'      :    84.00,
    'DS2'       :    31.00,
    'DS5'       :    45.00,
    'DUL10'     :   413.00,
    'DUL5'      :   252.00,
    'EP0'       :   119.00,
    'ET10'      :    52.00,
    'ET50'      :   192.00,
    'F410'      :   185.00,
    'F42'       :    59.00,
    'F45'       :   147.00,
    'FM2'       :    45.00,
    'FMP2'      :    80.00,
    'FN1'       :    59.00,
    'G10K'      :    87.00,
    'G210'      :    45.00,
    'G25'       :    28.00,
    'G5K'       :    84.00,
    'G610'      :    35.00,
    'G65'       :    31.00,
    'GLOW70'    :   122.00,
    'GND2'      :    38.00,
    'GTT'       :    52.00,
    'GTT15'     :    87.00,
    'GTT4'      :    38.00,
    'H10'       :    63.00,
    'H15'       :    84.00,
    'H8'        :    52.00,
    'HUM10'     :   171.00,
    'HX2'       :    45.00,
    'HX5'       :    84.00,
    'IG1'       :   164.00,
    'IGD'       :    59.00,
    'IP10'      :    66.00,
    'IP2'       :    35.00,
    'IP5'       :    45.00,
    'KLOW'      :   150.00,
    'KPV10'     :    80.00,
    'KPV5'      :    49.00,
    'KS10'      :    91.00,
    'KS5'       :    59.00,
    'LC216'     :    73.00,
    'LGT10'     :   318.00,
    'LGT20'     :   591.00,
    'LGT5'      :   178.00,
    'MAT10'     :    66.00,
    'MDT10'     :   164.00,
    'MDT5'      :   154.00,
    'MEL10'     :   105.00,
    'MIC10'     :   238.00,
    'ML10'      :    73.00,
    'MS10'      :    66.00,
    'MS20'      :    91.00,
    'MS40'      :   157.00,
    'NJ100'     :    59.00,
    'NJ1000'    :   147.00,
    'NJ500'     :   105.00,
    'NP810'     :    45.00,
    'NP8100'    :   301.00,
    'OT10'      :   119.00,
    'OT2'       :    56.00,
    'OT5'       :    66.00,
    'P41'       :    56.00,
    'PI10'      :    80.00,
    'PI5'       :    59.00,
    'PN5'       :   231.00,
    'RA10'      :   119.00,
    'RA16'      :   199.00,
    'RT10'      :    66.00,
    'RT100'     :   301.00,
    'RT15'      :   112.00,
    'RT20'      :   126.00,
    'RT30'      :   168.00,
    'RT40'      :   213.00,
    'RT5'       :    45.00,
    'RT50'      :   238.00,
    'RT60'      :   266.00,
    'SK10'      :    73.00,
    'SK5'       :    45.00,
    'SLU5'      :    94.00,
    'SM10'      :    80.00,
    'SM100'     :   318.00,
    'SM15'      :    87.00,
    'SM20'      :   112.00,
    'SM30'      :   147.00,
    'SM40'      :   199.00,
    'SM5'       :    45.00,
    'SM50'      :   238.00,
    'SM60'      :   280.00,
    'SMO10'     :    94.00,
    'SMO5'      :    73.00,
    'SUR10'     :   385.00,
    'SUR2'      :   213.00,
    'SUR5'      :   294.00,
    'TA10'      :   140.00,
    'TA2'       :    59.00,
    'TA5'       :    84.00,
    'TR10'      :    80.00,
    'TR100'     :   238.00,
    'TR120'     :   266.00,
    'TR15'      :    98.00,
    'TR20'      :   119.00,
    'TR30'      :   140.00,
    'TR40'      :   161.00,
    'TR5'       :    45.00,
    'TR50'      :   185.00,
    'TR60'      :   213.00,
    'TSM10'     :   157.00,
    'TSM2'      :    56.00,
    'TSM20'     :   231.00,
    'TSM5'      :    66.00,
    'TY10'      :    59.00,
    'XA10'      :    73.00,
    'XA5'       :    42.00,
}

TRADING_BASELINE: dict[str, float] = {
    '10AD'      :   152.00,
    '2AD'       :    47.00,
    '2S10'      :    57.00,
    '2S50'      :   165.00,
    '50AM'      :    67.00,
    '5AD'       :    90.00,
    '5AM'       :    25.00,
    '5AM10'     :    42.00,
    'ACTH5'     :   105.00,
    'ADA10'     :    90.00,
    'ADA5'      :    70.00,
    'AE1'       :   140.00,
    'AP2'       :    50.00,
    'AP5'       :    95.00,
    'AR50'      :    42.00,
    'BAC10'     :    10.00,
    'BB10'      :    60.00,
    'BB20'      :    95.00,
    'BC10'      :    40.00,
    'BC5'       :    32.00,
    'BT10'      :    80.00,
    'BT5'       :    52.00,
    'CAR10'     :   100.00,
    'CAR20'     :   170.00,
    'CART10'    :    75.00,
    'CART20'    :   110.00,
    'CD5'       :    87.00,
    'CGL10'     :   102.00,
    'CGL5'      :    65.00,
    'CND10'     :    90.00,
    'CND2'      :    25.00,
    'CND5'      :    55.00,
    'CP10'      :    65.00,
    'CRY10'     :    90.00,
    'CRY20'     :   165.00,
    'CU100'     :    47.00,
    'CU50'      :    32.00,
    'DS10'      :    60.00,
    'DS2'       :    22.00,
    'DS5'       :    32.00,
    'DUL10'     :   295.00,
    'DUL5'      :   180.00,
    'EP0'       :    85.00,
    'ET10'      :    37.00,
    'ET50'      :   137.00,
    'F410'      :   132.00,
    'F42'       :    42.00,
    'F45'       :   105.00,
    'FM2'       :    32.00,
    'FMP2'      :    57.00,
    'FN1'       :    42.00,
    'G10K'      :    62.00,
    'G210'      :    32.00,
    'G25'       :    20.00,
    'G5K'       :    60.00,
    'G610'      :    25.00,
    'G65'       :    22.00,
    'GLOW70'    :    87.00,
    'GND2'      :    27.00,
    'GTT'       :    37.00,
    'GTT15'     :    62.00,
    'GTT4'      :    27.00,
    'H10'       :    45.00,
    'H15'       :    60.00,
    'H8'        :    37.00,
    'HUM10'     :   122.00,
    'HX2'       :    32.00,
    'HX5'       :    60.00,
    'IG1'       :   117.00,
    'IGD'       :    42.00,
    'IP10'      :    47.00,
    'IP2'       :    25.00,
    'IP5'       :    32.00,
    'KLOW'      :   107.00,
    'KPV10'     :    57.00,
    'KPV5'      :    35.00,
    'KS10'      :    65.00,
    'KS5'       :    42.00,
    'LC216'     :    52.00,
    'LGT10'     :   227.00,
    'LGT20'     :   422.00,
    'LGT5'      :   127.00,
    'MAT10'     :    47.00,
    'MDT10'     :   117.00,
    'MDT5'      :   110.00,
    'MEL10'     :    75.00,
    'MIC10'     :   170.00,
    'ML10'      :    52.00,
    'MS10'      :    47.00,
    'MS20'      :    65.00,
    'MS40'      :   112.00,
    'NJ100'     :    42.00,
    'NJ1000'    :   105.00,
    'NJ500'     :    75.00,
    'NP810'     :    32.00,
    'NP8100'    :   215.00,
    'OT10'      :    85.00,
    'OT2'       :    40.00,
    'OT5'       :    47.00,
    'P41'       :    40.00,
    'PI10'      :    57.00,
    'PI5'       :    42.00,
    'PN5'       :   165.00,
    'RA10'      :    85.00,
    'RA16'      :   142.00,
    'RT10'      :    47.00,
    'RT100'     :   215.00,
    'RT15'      :    80.00,
    'RT20'      :    90.00,
    'RT30'      :   120.00,
    'RT40'      :   152.00,
    'RT5'       :    32.00,
    'RT50'      :   170.00,
    'RT60'      :   190.00,
    'SK10'      :    52.00,
    'SK5'       :    32.00,
    'SLU5'      :    78.00,
    'SM10'      :    57.00,
    'SM100'     :   227.00,
    'SM15'      :    62.00,
    'SM20'      :    80.00,
    'SM30'      :   105.00,
    'SM40'      :   142.00,
    'SM5'       :    32.00,
    'SM50'      :   170.00,
    'SM60'      :   200.00,
    'SMO10'     :    67.00,
    'SMO5'      :    52.00,
    'SUR10'     :   275.00,
    'SUR2'      :   152.00,
    'SUR5'      :   210.00,
    'TA10'      :   100.00,
    'TA2'       :    42.00,
    'TA5'       :    60.00,
    'TR10'      :    57.00,
    'TR100'     :   170.00,
    'TR120'     :   190.00,
    'TR15'      :    70.00,
    'TR20'      :    85.00,
    'TR30'      :   100.00,
    'TR40'      :   115.00,
    'TR5'       :    32.00,
    'TR50'      :   132.00,
    'TR60'      :   152.00,
    'TSM10'     :   112.00,
    'TSM2'      :    40.00,
    'TSM20'     :   165.00,
    'TSM5'      :    47.00,
    'TY10'      :    42.00,
    'XA10'      :    52.00,
    'XA5'       :    30.00,
}


# The US warehouse: (per vial, per kit). One price at any quantity.
US_BASELINE: dict[str, tuple] = {
    'BAC10'   : (  10.00,    50.00),   # Bacteriostatic Water 10ml
    'BB10'    : (  55.00,   275.00),   # BPC+TB Blend 10mg
    'BB20'    : (  80.00,   400.00),   # BPC+TB Blend 20mg
    'BC10'    : (  45.00,   225.00),   # BPC-157 10mg
    'BT10'    : (  50.00,   250.00),   # TB-500 10mg
    'CP10'    : (  65.00,   325.00),   # CJC+Ipamorelin Blend 10mg
    'CU100'   : (  50.00,   250.00),   # GHK-Cu 100mg
    'CU50'    : (  35.00,   175.00),   # GHK-Cu 50mg
    'GLOW70'  : (  70.00,   350.00),   # BPC+GHK-Cu+TB Blend 70mg
    'KLOW'    : (  80.00,   400.00),   # BPC+TB+GHK-Cu+KPV Blend 80mg
    'KPV10'   : (  45.00,   225.00),   # KPV 10mg
    'ML10'    : (  35.00,   175.00),   # Melanotan II 10mg
    'MS10'    : (  40.00,   200.00),   # MOTS-c 10mg
    'MS40'    : ( 100.00,   500.00),   # MOTS-c 40mg
    'NJ1000'  : (  75.00,   375.00),   # NAD 1000mg
    'NJ500'   : (  45.00,   225.00),   # NAD 500mg
    'P41'     : (  40.00,   200.00),   # PT-141 10mg
    'RT10'    : (  50.00,   250.00),   # Retatrutide 10mg
    'RT20'    : (  75.00,   375.00),   # Retatrutide 20mg
    'RT30'    : ( 100.00,   500.00),   # Retatrutide 30mg
    'RT60'    : ( 150.00,   750.00),   # Retatrutide 60mg
    'SK10'    : (  40.00,   200.00),   # Selank 10mg
    'SM10'    : (  30.00,   150.00),   # Semaglutide 10mg
    'SM20'    : (  50.00,   250.00),   # Semaglutide 20mg
    'SM30'    : (  60.00,   300.00),   # Semaglutide 30mg
    'TR10'    : (  36.00,   180.00),   # Tirzepatide 10mg
    'TR20'    : (  45.00,   225.00),   # Tirzepatide 20mg
    'TR30'    : (  55.00,   275.00),   # Tirzepatide 30mg
    'TSM10'   : (  55.00,   275.00),   # Tesamorelin 10mg
    'XA10'    : (  40.00,   200.00),   # Semax 10mg
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
    """The log and the numbers cannot disagree.

    Only the LATEST entry per SKU is checked: the log is append-only history, so
    BAC10 legitimately appears twice ($12 -> $17 on 2026-08-31, $17 -> $20 on
    2026-09-03) and only the last one describes today. A SKU that has since been
    removed is skipped — REMOVED_SKUS is what governs those, and
    test_removed_skus_are_really_gone asserts they are gone."""
    removed = {r[0] for r in REMOVED_SKUS}
    latest: dict[str, float] = {}
    for sku, _old, new, _date, _who, _why in INTENTIONAL_CHANGES:
        latest[sku] = new           # later entries win
    for sku, new in latest.items():
        if sku in removed:
            continue
        assert abs(BASELINE[sku] - new) < 0.005, (
            f"{sku} is logged as changed to ${new:.2f} but the baseline says "
            f"${BASELINE[sku]:.2f}")


def test_the_change_log_is_in_date_order():
    """Append-only, newest last — the rule the 'latest entry wins' check above
    depends on. A back-dated insertion would silently invert it."""
    dates = [c[3] for c in INTENTIONAL_CHANGES]
    assert dates == sorted(dates), f"INTENTIONAL_CHANGES is out of order: {dates}"


def test_every_price_move_is_accounted_for():
    """The 2026-09-03 sheet replacement, enumerated.

    This is what keeps a wholesale regeneration honest. Every SKU whose price
    differs from the 2026-08-31 baseline must appear in PRICE_MOVES_2026_09_03
    with both numbers, and every SKU that appeared or vanished must be in
    ADDED_SKUS_2026_09_03 or REMOVED_SKUS. A 148th price that moved without
    being written down fails here.
    """
    moves = {sku: (old, new) for sku, old, new in PRICE_MOVES_2026_09_03}
    removed = {r[0] for r in REMOVED_SKUS}

    unlogged = []
    for sku, old in BASELINE_2026_08_31.items():
        if sku in removed:
            continue
        assert sku in BASELINE, f"{sku} vanished from BASELINE but is not in REMOVED_SKUS"
        if abs(BASELINE[sku] - old) >= 0.005 and sku not in moves:
            unlogged.append((sku, old, BASELINE[sku]))
    assert not unlogged, f"prices moved with no entry in PRICE_MOVES_2026_09_03: {unlogged}"

    for sku, old, new in PRICE_MOVES_2026_09_03:
        assert abs(BASELINE_2026_08_31[sku] - old) < 0.005, (
            f"{sku} is logged as moving from ${old:.2f} but the old baseline says "
            f"${BASELINE_2026_08_31[sku]:.2f}")
        assert abs(BASELINE[sku] - new) < 0.005, (
            f"{sku} is logged as moving to ${new:.2f} but the baseline says "
            f"${BASELINE[sku]:.2f}")

    appeared = set(BASELINE) - set(BASELINE_2026_08_31)
    assert appeared == set(ADDED_SKUS_2026_09_03), (
        f"new SKUs not logged: {sorted(appeared - set(ADDED_SKUS_2026_09_03))}")


def test_no_baseline_price_sells_below_cost():
    """The 3x floor is gone with negotiation — nothing can push a price down any
    more. What remains worth asserting is that the SHEET does not sell at a loss,
    asked at the deepest tier because that is the one that would."""
    under = [(sku, TRADING_BASELINE[sku], catalog.get(sku).cost)
             for sku in BASELINE
             if catalog.get(sku).cost and TRADING_BASELINE[sku] < catalog.get(sku).cost]
    assert under == [], f"trading-tier price below cost: {under}"


@pytest.mark.parametrize("sku", sorted(BASELINE))
def test_all_three_china_tiers_are_pinned(sku):
    """Each tier is a separate number a buyer is charged, so each is pinned."""
    item = catalog.get(sku)
    assert abs(item.list_price - BASELINE[sku]) < 0.005
    assert abs(item.reseller_price - RESELLER_BASELINE[sku]) < 0.005
    assert abs(item.trading_price - TRADING_BASELINE[sku]) < 0.005


@pytest.mark.parametrize("kits,expected_key", [(1, "std"), (24, "std"), (25, "res"),
                                               (99, "res"), (100, "trd"), (500, "trd")])
def test_the_tier_boundaries_are_where_jordan_put_them(kits, expected_key):
    """1-24 / 25-99 / 100+ (Jordan, 2026-09-03). Off-by-one here is a real
    mispricing on the exact orders most likely to be big."""
    table = {"std": BASELINE, "res": RESELLER_BASELINE, "trd": TRADING_BASELINE}
    for sku in ("RT10", "BAC10", "SM10"):
        item = catalog.get(sku)
        assert abs(item.price(kits) - table[expected_key][sku]) < 0.005, (
            f"{sku} at {kits} kits should price from the {expected_key} sheet")


@pytest.mark.parametrize("sku,prices", sorted(US_BASELINE.items()))
def test_us_warehouse_prices_are_pinned(sku, prices):
    """The US sheet is a different catalog at different prices; it needs its own
    pin, and it must NOT move with order size."""
    vial, kit = prices
    item = catalog.get(sku)
    assert abs(item.us_vial_price - vial) < 0.005
    assert abs(item.us_kit_price - kit) < 0.005
    for kits in (1, 25, 100, 500):
        assert abs(item.price(kits, "us") - kit) < 0.005, (
            f"{sku} moved with volume at the US warehouse — it must not")


def test_the_us_warehouse_is_a_subset_of_the_catalog():
    """A US row that resolves to no SKU would be unfulfillable. The generator
    refuses to emit one, and this is the standing check that it stayed true."""
    assert set(US_BASELINE) <= set(BASELINE)
    assert len(US_BASELINE) == 30


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
