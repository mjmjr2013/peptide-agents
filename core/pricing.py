"""
Pricing catalog — costs in USD (converted from CNY at 7.25).
Sell prices start at 6x cost, floor at 3x cost.
Agent has authority to negotiate between 6x and 3x based on
buyer type, order volume, and interaction quality.
"""
from __future__ import annotations

# Cost per kit (10 vials) in USD
CATALOG: list[dict] = [
    {"product": "Semaglutide", "spec": "5mg x10", "cost": 9.66},
    {"product": "Semaglutide", "spec": "10mg x10", "cost": 15.17},
    {"product": "Semaglutide", "spec": "15mg x10", "cost": 22.07},
    {"product": "Semaglutide", "spec": "20mg x10", "cost": 27.59},
    {"product": "Semaglutide", "spec": "30mg x10", "cost": 35.86},
    {"product": "Semaglutide", "spec": "40mg x10", "cost": 44.14, "list_override": 288.00},
    {"product": "Semaglutide", "spec": "50mg x10", "cost": 62.07, "list_override": 360.00},
    {"product": "Tirzepatide", "spec": "5mg x10", "cost": 11.72},
    {"product": "Tirzepatide", "spec": "10mg x10", "cost": 17.93},
    {"product": "Tirzepatide", "spec": "15mg x10", "cost": 27.59, "list_override": 162.00},
    {"product": "Tirzepatide", "spec": "20mg x10", "cost": 37.24, "list_override": 216.00},
    {"product": "Tirzepatide", "spec": "30mg x10", "cost": 45.52},
    {"product": "Tirzepatide", "spec": "40mg x10", "cost": 66.21, "list_override": 365.00},
    {"product": "Tirzepatide", "spec": "50mg x10", "cost": 75.86},
    {"product": "Tirzepatide", "spec": "60mg x10", "cost": 92.41, "list_override": 547.00},
    {"product": "Tirzepatide", "spec": "80mg x10", "cost": 121.38},
    {"product": "Tirzepatide", "spec": "100mg x10", "cost": 132.41},
    {"product": "Retatrutide", "spec": "5mg x10", "cost": 11.03},
    {"product": "Retatrutide", "spec": "10mg x10", "cost": 17.24, "list_override": 94.82},
    {"product": "Retatrutide", "spec": "15mg x10", "cost": 27.59, "list_override": 142.00},
    {"product": "Retatrutide", "spec": "20mg x10", "cost": 36.55, "list_override": 189.00},
    {"product": "Retatrutide", "spec": "30mg x10", "cost": 45.52},
    {"product": "Retatrutide", "spec": "40mg x10", "cost": 62.07, "list_override": 365.00},
    {"product": "Retatrutide", "spec": "50mg x10", "cost": 78.62, "list_override": 456.00},
    {"product": "Retatrutide", "spec": "60mg x10", "cost": 95.17, "list_override": 547.00},
    {"product": "Retatrutide", "spec": "80mg x10", "cost": 118.62, "list_override": 729.00},
    {"product": "Retatrutide", "spec": "100mg x10", "cost": 148.97},
    {"product": "Cagrilintide", "spec": "5mg x10", "cost": 19.03},
    {"product": "Cagrilintide", "spec": "10mg x10", "cost": 30.07},
    {"product": "Mazdutide", "spec": "5mg x10", "cost": 32.00},
    {"product": "Mazdutide", "spec": "10mg x10", "cost": 33.79},
    {"product": "Survodutide", "spec": "2mg x10", "cost": 44.14},
    {"product": "Survodutide", "spec": "5mg x10", "cost": 80.00},
    {"product": "Survodutide", "spec": "10mg x10", "cost": 136.55},
    {"product": "Dulaglutide", "spec": "5mg x10", "cost": 52.41},
    {"product": "Dulaglutide", "spec": "10mg x10", "cost": 85.52},
    {"product": "Liraglutide", "spec": "5mg x10", "cost": 37.24},
    {"product": "Liraglutide", "spec": "10mg x10", "cost": 66.21},
    {"product": "Liraglutide", "spec": "20mg x10", "cost": 122.76},
    {"product": "BPC-157", "spec": "5mg x10", "cost": 9.66},
    {"product": "BPC-157", "spec": "10mg x10", "cost": 11.86},
    {"product": "TB-500", "spec": "5mg x10", "cost": 15.17},
    {"product": "TB-500", "spec": "10mg x10", "cost": 23.17},
    {"product": "BPC+TB Blend", "spec": "10mg x10", "cost": 17.93},
    {"product": "BPC+TB Blend", "spec": "20mg x10", "cost": 27.59},
    {"product": "BPC+TB+GHK-Cu+KPV Blend", "spec": "80mg x10", "cost": 31.03, "list_override": 220.00},
    {"product": "BPC+GHK-Cu+TB Blend", "spec": "70mg x10", "cost": 25.52},
    {"product": "GHK-Cu", "spec": "50mg x10", "cost": 11.72},
    {"product": "GHK-Cu", "spec": "100mg x10", "cost": 19.31},
    {"product": "Ipamorelin", "spec": "2mg x10", "cost": 7.72},
    {"product": "Ipamorelin", "spec": "5mg x10", "cost": 9.66},
    {"product": "Ipamorelin", "spec": "10mg x10", "cost": 16.55},
    {"product": "CJC-1295 (no DAC)", "spec": "2mg x10", "cost": 6.90},
    {"product": "CJC-1295 (no DAC)", "spec": "5mg x10", "cost": 16.28},
    {"product": "CJC-1295 (no DAC)", "spec": "10mg x10", "cost": 26.21},
    {"product": "CJC-1295 (with DAC)", "spec": "5mg x10", "cost": 25.10, "list_override": 166.00},
    {"product": "CJC+Ipamorelin Blend", "spec": "10mg x10", "cost": 18.90},
    {"product": "Melanotan II", "spec": "10mg x10", "cost": 24.83},
    {"product": "AOD-9604", "spec": "2mg x10", "cost": 16.55},
    {"product": "AOD-9604", "spec": "5mg x10", "cost": 31.72},
    {"product": "AOD-9604", "spec": "10mg x10", "cost": 55.17},
    {"product": "PT-141", "spec": "10mg x10", "cost": 11.86},
    {"product": "Sermorelin", "spec": "5mg x10", "cost": 14.90},
    {"product": "Sermorelin", "spec": "10mg x10", "cost": 19.72},
    {"product": "Sermorelin Acetate", "spec": "2mg x10", "cost": 20.69},
    {"product": "Sermorelin Acetate", "spec": "5mg x10", "cost": 37.24},
    {"product": "Sermorelin Acetate", "spec": "10mg x10", "cost": 66.21},
    {"product": "GHRP-2", "spec": "5mg x10", "cost": 5.52},
    {"product": "GHRP-2", "spec": "10mg x10", "cost": 9.66},
    {"product": "GHRP-6", "spec": "5mg x10", "cost": 6.21},
    {"product": "GHRP-6", "spec": "10mg x10", "cost": 6.90},
    {"product": "HGH 191AA", "spec": "8iu x10", "cost": 10.76},
    {"product": "HGH 191AA", "spec": "10iu x10", "cost": 13.24},
    {"product": "HGH 191AA", "spec": "15iu x10", "cost": 17.52},
    {"product": "HCG", "spec": "5000IU x10", "cost": 17.24},
    {"product": "HCG", "spec": "10000IU x10", "cost": 27.31},
    {"product": "IGF-1 LR3", "spec": "1mg x10", "cost": 33.93},
    {"product": "IGF-DES", "spec": "2mg x10", "cost": 12.69},
    {"product": "Follistatin", "spec": "1mg x10", "cost": 48.28},
    {"product": "Tesamorelin", "spec": "2mg x10", "cost": 11.86},
    {"product": "Tesamorelin", "spec": "5mg x10", "cost": 19.03},
    {"product": "Tesamorelin", "spec": "10mg x10", "cost": 32.41},
    {"product": "Tesamorelin", "spec": "20mg x10", "cost": 48.28},
    {"product": "Thymosin Alpha-1", "spec": "2mg x10", "cost": 12.14},
    {"product": "Thymosin Alpha-1", "spec": "5mg x10", "cost": 17.38},
    {"product": "Thymosin Alpha-1", "spec": "10mg x10", "cost": 29.24},
    {"product": "Thymalin", "spec": "10mg x10", "cost": 12.69},
    {"product": "Epithalon", "spec": "10mg x10", "cost": 10.62},
    {"product": "Epithalon", "spec": "50mg x10", "cost": 40.00},
    {"product": "MOTS-c", "spec": "10mg x10", "cost": 13.52},
    {"product": "MOTS-c", "spec": "20mg x10", "cost": 18.62},
    {"product": "MOTS-c", "spec": "40mg x10", "cost": 32.83},
    {"product": "Gonadorelin", "spec": "2mg x10", "cost": 9.24},
    {"product": "Hexarelin", "spec": "2mg x10", "cost": 9.24},
    {"product": "Hexarelin", "spec": "5mg x10", "cost": 17.24},
    {"product": "KissPeptin-10", "spec": "5mg x10", "cost": 12.00},
    {"product": "KissPeptin-10", "spec": "10mg x10", "cost": 19.17},
    {"product": "Oxytocin", "spec": "2mg x10", "cost": 11.86},
    {"product": "Oxytocin", "spec": "5mg x10", "cost": 20.69},
    {"product": "Oxytocin", "spec": "10mg x10", "cost": 38.62},
    {"product": "Selank", "spec": "5mg x10", "cost": 9.10},
    {"product": "Selank", "spec": "10mg x10", "cost": 15.17},
    {"product": "Semax", "spec": "5mg x10", "cost": 8.69},
    {"product": "Semax", "spec": "10mg x10", "cost": 15.17},
    {"product": "KPV", "spec": "5mg x10", "cost": 10.34},
    {"product": "KPV", "spec": "10mg x10", "cost": 16.55},
    {"product": "NAD", "spec": "100mg x10", "cost": 16.55},
    {"product": "NAD", "spec": "500mg x10", "cost": 40.00},
    {"product": "NAD", "spec": "1000mg x10", "cost": 44.14},
    {"product": "Glutathione", "spec": "400mg x10", "cost": 11.03},
    {"product": "Glutathione", "spec": "600mg x10", "cost": 14.48},
    {"product": "Glutathione", "spec": "1500mg x10", "cost": 27.59},
    {"product": "Melatonin", "spec": "10mg x10", "cost": 22.07},
    {"product": "AICAR", "spec": "50mg x10", "cost": 13.24},
    {"product": "SS-31", "spec": "10mg x10", "cost": 16.55},
    {"product": "SS-31", "spec": "50mg x10", "cost": 68.97},
    {"product": "FOXO4-DRI", "spec": "2mg x10", "cost": 38.62},
    {"product": "FOXO4-DRI", "spec": "5mg x10", "cost": 62.07},
    {"product": "FOXO4-DRI", "spec": "10mg x10", "cost": 104.83},
    {"product": "ACE-031", "spec": "1mg x10", "cost": 40.41},
    {"product": "Humanin", "spec": "10mg x10", "cost": 122.76},
    {"product": "Snap-8", "spec": "10mg x10", "cost": 22.07},
    {"product": "Snap-8", "spec": "100mg x10", "cost": 110.34},
    {"product": "5-Amino/MQ", "spec": "5mg x10", "cost": 30.34},
    {"product": "5-Amino/MQ", "spec": "10mg x10", "cost": 43.45},
    {"product": "5-Amino/MQ", "spec": "50mg x10", "cost": 135.17},
    {"product": "MGF", "spec": "2mg x10", "cost": 9.66},
    {"product": "PEG MGF", "spec": "2mg x10", "cost": 16.83},
    {"product": "Lipo-C", "spec": "10ml x10", "cost": 15.17},
    {"product": "MIC (Lipo-C+B12)", "spec": "10ml x10", "cost": 49.66},
    {"product": "Matrixyl", "spec": "10mg x10", "cost": 13.52},
    {"product": "Pinealon", "spec": "5mg x10", "cost": 12.41},
    {"product": "Pinealon", "spec": "10mg x10", "cost": 20.69},
    {"product": "DSIP", "spec": "2mg", "cost": 6.21},
    {"product": "DSIP", "spec": "5mg", "cost": 9.52},
    {"product": "DSIP", "spec": "10mg", "cost": 17.24},
    {"product": "Adipotide", "spec": "2mg x10", "cost": 14.21},
    {"product": "Adipotide", "spec": "5mg x10", "cost": 27.59},
    {"product": "Ara-290", "spec": "10mg x10", "cost": 24.83},
    {"product": "Ara-290", "spec": "16mg x10", "cost": 41.38},
    {"product": "Cardiogen", "spec": "10mg x10", "cost": 28.97},
    {"product": "Cardiogen", "spec": "20mg x10", "cost": 49.66},
    {"product": "Cartalax", "spec": "10mg x10", "cost": 31.72},
    {"product": "Cartalax", "spec": "20mg x10", "cost": 53.79},
    {"product": "Crystagen", "spec": "10mg x10", "cost": 26.21},
    {"product": "Crystagen", "spec": "20mg x10", "cost": 48.28},
    {"product": "PNC-27", "spec": "5mg x10", "cost": 48.28},
    {"product": "Admax", "spec": "5mg x10", "cost": 26.21},
    {"product": "Admax", "spec": "10mg x10", "cost": 44.14},
    {"product": "ACTH", "spec": "5mg x10", "cost": 30.34},
    {"product": "SLU-PP-322", "spec": "5mg x10", "cost": 35.86},
    {"product": "EPO", "spec": "3000IU", "cost": 24.83},
    {"product": "Dermorphin", "spec": "2mg x10", "cost": 11.86},
    {"product": "Dermorphin", "spec": "5mg x10", "cost": 20.69},
    {"product": "Dermorphin", "spec": "10mg x10", "cost": 33.10},
    {"product": "Dermorphin", "spec": "20mg x10", "cost": 55.17},
    {"product": "NAD", "spec": "100mg x10", "cost": 16.67},
    {"product": "NAD", "spec": "500mg x10", "cost": 67.50},
    {"product": "NAD", "spec": "1000mg x10", "cost": 123.33},
    {"product": "Bacteriostatic Water", "spec": "10ml x10", "cost": 2.00, "list_override": 12.00},
    {"product": "Sterile Water", "spec": "10ml x10", "cost": 2.00, "list_override": 12.00},
]

MARKUP_START = 6.0   # open at 6x cost
MARKUP_FLOOR = 3.0   # never go below 3x cost


def get_price(cost: float, markup: float, list_override: float | None = None) -> float:
    if list_override and markup == MARKUP_START:
        return round(list_override, 2)
    return round(cost * markup, 2)


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_item(product: str, spec: str = "") -> dict | None:
    """Best-effort match of a Claude-supplied product/spec to a CATALOG row.
    Returns the matching dict or None if it can't be confidently matched."""
    np = _norm(product)
    if not np:
        return None
    candidates = [it for it in CATALOG if _norm(it["product"]) == np]
    if not candidates:
        candidates = [it for it in CATALOG
                      if np in _norm(it["product"]) or _norm(it["product"]) in np]
    nspec = _norm(spec)
    if nspec:
        for it in candidates:  # catalog order: smaller doses first, so prefix match is safe
            nis = _norm(it["spec"])
            if nis.startswith(nspec) or nspec == nis or nspec in nis:
                return it
    if len(candidates) == 1:
        return candidates[0]
    return None


def get_floor_price(product: str, spec: str = "") -> float | None:
    """Per-kit floor price (3x cost) for a product/spec, or None if unmatched."""
    item = find_item(product, spec)
    if item is None:
        return None
    return get_price(item["cost"], MARKUP_FLOOR)


def get_list_price(product: str, spec: str = "") -> float | None:
    """Per-kit list price for a product/spec, or None if unmatched.

    Source of truth is the customer-facing price-list image (whole dollars) so the
    agent always quotes exactly what the customer sees on the sheet. Falls back to
    a whole-dollar 6x cost (rounded up) for any catalog item not on the sheet."""
    item = find_item(product, spec)
    if item is None:
        return None
    from core.price_image import get_image_price
    img = get_image_price(item["product"], item["spec"])
    if img is not None:
        return img
    if item.get("list_override"):
        return round(item["list_override"], 2)
    import math
    return float(math.ceil(item["cost"] * MARKUP_START))


# Volume-based discount caps (percent off list price). Orders over 100 kits are
# quoted and negotiated like any other order (same 15% cap as the 50+ tier); the
# agent only escalates to a human when the buyer wants MORE than the cap allows.
HANDOFF_KITS = 100


def max_discount_for_qty(kits: float) -> float:
    """Max allowed discount fraction off list for an order size. The agent may
    negotiate down to (but not past) this fraction without human approval. For
    orders over 100 kits the cap is the same 15% — beyond that the agent escalates
    to an operator rather than going lower on its own."""
    if kits < 25:
        return 0.05
    if kits < 50:
        return 0.10
    return 0.15  # 50+ kits, including 100+


def get_catalog_text() -> str:
    """Returns a formatted pricing table for use in Claude prompts.

    List Price is the exact whole-dollar number shown on the customer's price
    list (single source of truth). Floor is whole-dollar 3x cost rounded up, so
    every price the agent can quote — list or negotiated floor — is a clean whole
    dollar that matches the sheet and never dips below true cost-floor."""
    import math
    lines = ["Product | Spec | List Price | Floor Price (never go below)"]
    lines.append("-" * 70)
    for item in CATALOG:
        list_price = get_list_price(item["product"], item["spec"])
        if list_price is None:
            list_price = math.ceil(item["cost"] * MARKUP_START)
        floor_price = math.ceil(item["cost"] * MARKUP_FLOOR)
        lines.append(f"{item['product']} | {item['spec']} | ${int(round(list_price))} | ${floor_price}")
    return "\n".join(lines)


# ── Formatted WhatsApp price list ─────────────────────────────────────────────

PRICE_LIST_MESSAGES = [
    """*NORTHLINE GROUP — PRICE LIST*
All prices per kit (10 vials) • USD

*━━ GLP-1s ━━*
*Semaglutide*
  5mg  → $57.96
  10mg → $91.02
  15mg → $132.42
  20mg → $165.54
  30mg → $215.16
  40mg → $264.84
  50mg → $372.42

*Tirzepatide*
  5mg  → $70.32
  10mg → $107.58
  15mg → $165.54
  20mg → $223.44
  30mg → $273.12
  40mg → $397.26
  50mg → $455.16
  60mg → $554.46
  80mg → $728.28
  100mg → $794.46

*Retatrutide*
  5mg  → $66.18
  10mg → $94.82
  15mg → $165.54
  20mg → $219.30
  30mg → $274
  40mg → $365
  50mg → $456
  60mg → $547
  80mg → $729
  100mg → $894""",

    """*━━ Healing Peptides ━━*
*BPC-157*
  5mg  → $57.96
  10mg → $71.16

*TB-500*
  5mg  → $91.02
  10mg → $139.02

*BPC+TB Blend*
  10mg → $107.58
  20mg → $165.54

*BPC+TB+GHK-Cu+KPV Blend*
  80mg → $220

*BPC+GHK-Cu+TB Blend*
  70mg → $153.12

*GHK-Cu*
  50mg  → $70.32
  100mg → $115.86

*KPV*
  5mg  → $62.04
  10mg → $99.30

*PT-141*
  10mg → $71.16

*Melanotan II*
  10mg → $148.98""",

    """*━━ GH / Growth ━━*
*Ipamorelin*
  2mg  → $46.32
  5mg  → $57.96
  10mg → $99.30

*CJC-1295 (no DAC)*
  2mg  → $41.40
  5mg  → $97.68
  10mg → $157.26

*CJC-1295 (with DAC)*
  5mg  → $166

*CJC+Ipamorelin Blend*
  10mg → $113.40

*Sermorelin*
  5mg  → $89.40
  10mg → $118.32

*GHRP-2*
  5mg  → $33.12
  10mg → $57.96

*GHRP-6*
  5mg  → $37.26
  10mg → $41.40

*HGH 191AA*
  8iu  → $64.56
  10iu → $79.44
  15iu → $105.12

*HCG*
  5000IU  → $103.44
  10000IU → $163.86

*IGF-1 LR3*
  1mg → $203.58

*Tesamorelin*
  2mg  → $71.16
  5mg  → $114.18
  10mg → $194.46
  20mg → $289.68""",

    """*━━ Cognitive / Wellness ━━*
*Epithalon*
  10mg → $63.72
  50mg → $240.00

*MOTS-c*
  10mg → $81.12
  20mg → $111.72
  40mg → $196.98

*NAD*
  100mg  → $99.30
  500mg  → $240.00
  1000mg → $264.84

*Glutathione*
  400mg  → $66.18
  600mg  → $86.88
  1500mg → $165.54

*Thymosin Alpha-1*
  2mg  → $72.84
  5mg  → $104.28
  10mg → $175.44

*Selank*   5mg $54.60 | 10mg $91.02
*Semax*    5mg $52.14 | 10mg $91.02
*Pinealon* 5mg $74.46 | 10mg $124.14
*Oxytocin* 2mg $71.16 | 5mg $124.14
*Melatonin* 10mg $132.42
*Gonadorelin* 2mg $55.44

*━━ Other ━━*
*AOD-9604*
  2mg  → $99.30
  5mg  → $190.32
  10mg → $331.02

*5-Amino/MQ*
  5mg  → $182.04
  10mg → $260.70

*Cagrilintide*
  5mg  → $114.18
  10mg → $180.42

*Survodutide*
  5mg  → $480.00
  10mg → $819.30

*━━ Reconstitution Supplies ━━*
*Bacteriostatic Water* 10ml → $12.00
*Sterile Water*        10ml → $12.00

Reply with a product name for a specific quote, or to place an order. 🧬"""
]


def get_price_list_messages() -> list[str]:
    """Returns the full price list as a list of WhatsApp messages."""
    return PRICE_LIST_MESSAGES
