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
See is_promo_redeemed() in core.airtable_client.

WHITE LABEL IS PER-KIT, NOT PER-LINE. A line carries `kits` (what the warehouse ships
and the supplier bulk orders) and `wl_kits` (how many of those vials get the CUSTOMER's
branding). They differ whenever add-on vials ship under our own label instead — see
DIEGO26 below. Only designs with wl_kits > 0 are sent to the label factory.
"""

# ── DIEGO26 ────────────────────────────────────────────────────────────────────
# Group buyer sourced by Daniel (2026-08-17). Verix wholesale price match.
# Original 21 lines / 25 kits @ $2,193.50, all customer-branded.
# 5 kits added 2026-08-19 @ $700.14 (Daniel-approved, priced per the Verix sheet
# line-by-line — deliberately NOT the same % as the original lines).
# The add-on vials are NOT customer-branded: Daniel is arranging Northline Group
# labels for those directly with Jason, off-system. So the customer artwork — and the
# $400 white-label fee — cover the ORIGINAL 21 designs / 25 kits only.
_DIEGO26_ITEMS = [
    # (sku, product, spec, kits, wl_kits)
    ("RT10",   "Retatrutide",        "10mg",   3, 3),
    ("RT15",   "Retatrutide",        "15mg",   1, 0),   # added 08-19 — Northline label
    ("RT30",   "Retatrutide",        "30mg",   4, 3),   # 3 branded + 1 added (Northline)
    ("BC10",   "BPC-157",            "10mg",   2, 1),   # 1 branded + 1 added (Northline)
    ("BT10",   "TB-500",             "10mg",   1, 1),
    ("BB20",   "BPC+TB Blend",       "20mg",   1, 1),   # customer wrote "10mg/10mg"
    ("GLOW70", "BPC+TB+GHK Blend",   "70mg",   1, 1),   # customer wrote "Glow"
    ("KLOW",   "BPC+TB+GHK+KPV",     "80mg",   1, 1),   # customer wrote "Klow"
    ("CU50",   "GHK-Cu",             "50mg",   1, 1),
    ("KPV10",  "KPV",                "10mg",   1, 1),
    ("P41",    "PT-141",             "10mg",   1, 1),
    ("ML10",   "Melanotan II",       "10mg",   1, 1),   # customer wrote "MT2"
    ("CND10",  "CJC-1295 (no DAC)",  "10mg",   1, 1),
    ("CP10",   "CJC+Ipa Blend",      "10mg",   1, 1),   # ONE product, not two lines
    ("IP10",   "Ipamorelin",         "10mg",   1, 1),
    ("TSM10",  "Tesamorelin",        "10mg",   2, 1),   # 1 branded + 1 added (Northline)
    ("NJ1000", "NAD",                "1000mg", 1, 1),
    ("GTT",    "Glutathione",        "600mg",  1, 1),
    ("MS20",   "MOTS-c",             "20mg",   2, 1),   # 1 branded + 1 added (Northline)
    ("SK10",   "Selank",             "10mg",   1, 1),
    ("XA10",   "Semax",              "10mg",   1, 1),
    ("KS10",   "KissPeptin-10",      "10mg",   1, 1),
]

DEALS: dict[str, dict] = {
    "DIEGO26": {
        "code": "DIEGO26",
        "label": "Verix price match — Diego group",
        "items": _DIEGO26_ITEMS,
        "items_total": 2893.64,     # 2193.50 original + 700.14 added 08-19
        "white_label_fee": 400.00,  # vs $840 table rate for 21 designs; Daniel-approved
        "shipping": 100.00,         # fixed quote — do NOT recompute via shipping rules
        "requires_artwork": True,   # white label included → collect label art before payment
        "one_time": True,           # burns once an order carrying it is paid
        "notes": ("Daniel-approved price match. Do not renegotiate or apply further "
                  "discounts. Add-on vials ship under Northline labels — Daniel arranges "
                  "those with Jason directly, they are NOT part of the factory job."),
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


def total_kits(deal: dict) -> int:
    """Every vial kit in the order — what the warehouse ships and the supplier bulks."""
    return sum(int(i[3]) for i in deal["items"])


def branded_kits(deal: dict) -> int:
    """Kits carrying the CUSTOMER's branding (the rest ship under our own label)."""
    return sum(int(i[4]) for i in deal["items"])


def branded_designs(deal: dict) -> list[tuple[str, str, int]]:
    """(product, spec, kits) for each distinct design the label factory must print.
    Excludes lines the customer's artwork does not cover."""
    out: dict[tuple[str, str], int] = {}
    for _sku, product, spec, _kits, wl in deal["items"]:
        if int(wl) > 0:
            out[(product, spec)] = out.get((product, spec), 0) + int(wl)
    return [(p, s, n) for (p, s), n in out.items()]


def unbranded_lines(deal: dict) -> list[tuple[str, str, int]]:
    """(product, spec, kits) for vials that do NOT carry the customer's branding —
    these ship under our own Northline label."""
    out: dict[tuple[str, str], int] = {}
    for _sku, product, spec, kits, wl in deal["items"]:
        n = int(kits) - int(wl)
        if n > 0:
            out[(product, spec)] = out.get((product, spec), 0) + n
    return [(p, s, n) for (p, s), n in out.items()]


def variation_count(deal: dict) -> int:
    """Distinct product+mg the factory prints = the white-label variation count."""
    return len(branded_designs(deal))


def labeling_split(deal: dict) -> dict | None:
    """Warehouse-facing labeling breakdown, or None when the order needs no special
    handling (nothing customer-branded). Surfaced on the manifest card so the packer
    can see at a glance which vials take which label."""
    branded = sorted(branded_designs(deal))
    unbranded = sorted(unbranded_lines(deal))
    if not branded:
        return None
    return {
        "branded": branded,
        "unbranded": unbranded,
        "branded_kits": branded_kits(deal),
        "unbranded_kits": total_kits(deal) - branded_kits(deal),
        "mixed": bool(unbranded),
    }


def order_items(deal: dict) -> list[dict]:
    """Line items in the shape create_pending_order() expects. line_total is left at 0:
    the deal is priced as a whole (a price match), so per-line prices are not meaningful
    and inventing them would misrepresent what the customer agreed to."""
    return [{"product": p, "spec": sp, "kits": k, "sku": sku, "line_total": 0}
            for sku, p, sp, k, _wl in deal["items"]]


def summary_line(deal: dict) -> str:
    """Short human summary for confirmations and ops alerts."""
    unbranded = total_kits(deal) - branded_kits(deal)
    tail = f", {unbranded} under our own label" if unbranded else ""
    return (f"{deal['code']}: {total_kits(deal)} kits, "
            f"${grand_total(deal):,.2f} total "
            f"(items ${deal['items_total']:,.2f} + white label "
            f"${deal['white_label_fee']:,.2f} + shipping ${deal['shipping']:,.2f}); "
            f"{branded_kits(deal)} kits customer-branded across "
            f"{variation_count(deal)} designs{tail}")
