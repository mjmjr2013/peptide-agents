from __future__ import annotations
"""
Pre-approved fixed-price deals, unlocked by a promo code.

A deal is a HUMAN-APPROVED basket at a HUMAN-APPROVED total (Daniel price-matches a
competitor sheet), so it deliberately bypasses the per-item floor/discount validation
in core.pricing — those guards exist to stop Claude from inventing discounts, not to
second-guess a price the owner set himself.

Deals live in code rather than Airtable on purpose:
  * the basket is fixed and shouldn't be casually editable,
  * codes are checked on every inbound message, so a lookup must be free.
Redemption state is NOT stored here — it is derived from Airtable (an order carrying
`promo_code` that reached payment_status='paid'), so it survives redeploys for free.
See is_redeemed() in core.airtable_client.
"""

# ── DIEGO26 ────────────────────────────────────────────────────────────────────
# Group buyer sourced by Daniel (2026-08-17). Verix wholesale price match.
# Original 21 lines @ $2,193.50; 5 lines added 2026-08-19 @ $700.14 (Daniel-approved,
# priced per the Verix sheet line-by-line — deliberately NOT the same % as the
# original lines). White label $400 and shipping $100 are fixed quoted figures.
_DIEGO26_ITEMS = [
    # (sku, product, spec, kits)
    ("RT10",   "Retatrutide",        "10mg",      3),
    ("RT15",   "Retatrutide",        "15mg",      1),   # added 08-19 (new variation)
    ("RT30",   "Retatrutide",        "30mg",      4),   # 3 + 1 added 08-19
    ("BC10",   "BPC-157",            "10mg",      2),   # 1 + 1 added 08-19
    ("BT10",   "TB-500",             "10mg",      1),
    ("BB20",   "BPC+TB Blend",       "20mg",      1),   # customer wrote "10mg/10mg"
    ("GLOW70", "BPC+TB+GHK Blend",   "70mg",      1),   # customer wrote "Glow"
    ("KLOW",   "BPC+TB+GHK+KPV",     "80mg",      1),   # customer wrote "Klow"
    ("CU50",   "GHK-Cu",             "50mg",      1),
    ("KPV10",  "KPV",                "10mg",      1),
    ("P41",    "PT-141",             "10mg",      1),
    ("ML10",   "Melanotan II",       "10mg",      1),   # customer wrote "MT2"
    ("CND10",  "CJC-1295 (no DAC)",  "10mg",      1),
    ("CP10",   "CJC+Ipa Blend",      "10mg",      1),   # ONE product, not two lines
    ("IP10",   "Ipamorelin",         "10mg",      1),
    ("TSM10",  "Tesamorelin",        "10mg",      2),   # 1 + 1 added 08-19
    ("NJ1000", "NAD",                "1000mg",    1),
    ("GTT",    "Glutathione",        "600mg",     1),
    ("MS20",   "MOTS-c",             "20mg",      2),   # 1 + 1 added 08-19
    ("SK10",   "Selank",             "10mg",      1),
    ("XA10",   "Semax",              "10mg",      1),
    ("KS10",   "KissPeptin-10",      "10mg",      1),
]

DEALS: dict[str, dict] = {
    "DIEGO26": {
        "code": "DIEGO26",
        "label": "Verix price match — Diego group",
        "items": _DIEGO26_ITEMS,
        "items_total": 2893.64,     # 2193.50 original + 700.14 added 08-19
        "white_label_fee": 400.00,  # discounted from $840 table rate; covers all variations
        "shipping": 100.00,         # fixed quote — do NOT recompute via shipping rules
        "requires_artwork": True,   # white label included → collect label art before payment
        "one_time": True,           # burns once an order carrying it is paid
        "notes": "Daniel-approved price match. Do not renegotiate or apply further discounts.",
    },
}


def normalize(code: str) -> str:
    return (code or "").strip().upper()


def get_deal(code: str) -> dict | None:
    """Look up a deal by code (case-insensitive). None if unknown."""
    return DEALS.get(normalize(code))


def find_code_in(text: str) -> str | None:
    """Return a known promo code mentioned anywhere in a message, else None.
    Matched on word-ish boundaries so 'my code is diego26!' works but a code
    embedded inside a longer token does not."""
    import re
    up = (text or "").upper()
    for code in DEALS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", up):
            return code
    return None


def grand_total(deal: dict) -> float:
    """Items + white label + shipping, as agreed. Rounded to cents."""
    return round(float(deal["items_total"])
                 + float(deal.get("white_label_fee") or 0)
                 + float(deal.get("shipping") or 0), 2)


def variation_count(deal: dict) -> int:
    """Distinct product+mg combinations = distinct label designs the factory prints."""
    return len({(i[1], i[2]) for i in deal["items"]})


def total_kits(deal: dict) -> int:
    return sum(int(i[3]) for i in deal["items"])


def order_items(deal: dict) -> list[dict]:
    """Line items in the shape create_pending_order() expects. line_total is left at 0:
    the deal is priced as a whole (a price match), so per-line prices are not meaningful
    and inventing them would misrepresent what the customer agreed to."""
    return [{"product": p, "spec": sp, "kits": k, "sku": sku, "line_total": 0}
            for sku, p, sp, k in deal["items"]]


def summary_line(deal: dict) -> str:
    """Short human summary for confirmations and ops alerts."""
    return (f"{deal['code']}: {total_kits(deal)} kits across {variation_count(deal)} products, "
            f"${grand_total(deal):,.2f} total "
            f"(items ${deal['items_total']:,.2f} + white label "
            f"${deal['white_label_fee']:,.2f} + shipping ${deal['shipping']:,.2f})")
