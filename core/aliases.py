from __future__ import annotations
"""
Product-name canonicalization — ONE place that knows a product's many spellings.

WHY THIS EXISTS (2026-08-30). The catalog lived in two files that spelled five
products differently:

    core/pricing.py            core/price_image.py (the customer price sheet)
    -----------------------    ----------------------------------------------
    BPC+GHK-Cu+TB Blend        BPC+TB+GHK Blend            (SKU GLOW70)
    BPC+TB+GHK-Cu+KPV Blend    BPC+TB+GHK+KPV              (SKU KLOW)
    CJC-1295 (with DAC)        CJC-1295 (w/ DAC)           (SKU CD5)
    CJC+Ipamorelin Blend       CJC+Ipa Blend               (SKU CP10)
    MIC (Lipo-C+B12)           MIC (Lipo+B12)              (SKU MIC10)

Neither name worked for both lookups: get_list_price() only resolved the
pricing.py spelling and get_sku() only resolved the price_image.py spelling. So
every order line for these five was EITHER priced with a null SKU, OR carried a
valid SKU at no price — and an unpriced line skipped the whole floor/discount
clamp in _validate_line_items, so a missing unit_price became $0.00 and the kits
shipped free. See HANDOFF for the full write-up.

The fix is deliberately an alias layer rather than a rename, because renaming
either side changes text a real buyer sees: price_image.CATEGORIES drives the
customer price-list image, XLSX and PDF, and pricing.CATALOG names are injected
into Lily's prompt via get_catalog_text(). Aliasing changes NO displayed string.

Both core/pricing.py and core/price_image.py normalize product names through
canon() here, so any listed spelling resolves to the same catalog row.

ADDING A PRODUCT: put every spelling that appears anywhere — cost file, price
sheet, COA page, deals.py, and whatever customers actually type — in one group.
The first entry is only a human-readable label; matching is on the whole group.
"""
import re

_ALIAS_GROUPS: list[tuple[str, list[str]]] = [
    # (canonical label, [every other spelling of the SAME physical product])
    ("BPC+GHK-Cu+TB Blend", [
        "BPC+TB+GHK Blend",          # price sheet (GLOW70)
        "BPC+TB+GHK-Cu Blend",
        "GLOW 70", "GLOW70", "Glow", # COA page / customer shorthand
    ]),
    ("BPC+TB+GHK-Cu+KPV Blend", [
        "BPC+TB+GHK+KPV",            # price sheet (KLOW)
        "BPC+TB+GHK-Cu+KPV",
        "KLOW 80", "KLOW",           # COA page / customer shorthand
    ]),
    ("CJC-1295 (with DAC)", [
        "CJC-1295 (w/ DAC)",         # price sheet (CD5)
        "CJC-1295 with DAC", "CJC-1295 w/ DAC",
    ]),
    ("CJC+Ipamorelin Blend", [
        "CJC+Ipa Blend",             # price sheet (CP10)
        "CJC-1295 + Ipamorelin",     # COA page
        "CJC+Ipamorelin", "CJC/Ipamorelin Blend",
    ]),
    ("MIC (Lipo-C+B12)", [
        "MIC (Lipo+B12)",            # price sheet (MIC10)
        "MIC Lipo+B12", "MIC",
    ]),
    # ── Names customers actually type (2026-08-31) ───────────────────────────
    # These were documented in Lily's prompt but were not in this table, so a
    # buyer asking for "MT2" or "Wolverine" produced an unresolvable line. Since
    # HANDOFF §29 that fails CLOSED — no free kits — but it stalls the order and
    # pings an operator, which is exactly the manual babysitting this system is
    # meant to remove. Two of them (Wolverine, MT2) are also the spellings
    # website/coa.html uses, so this closes the third-copy drift as well.
    ("BPC+TB Blend", [
        "BPC-157 + TB-500 (Wolverine)",   # coa.html
        "BPC-157 + TB-500", "BPC+TB", "BPC/TB Blend",
        "Wolverine",                      # customer shorthand
    ]),
    ("Melanotan II", [
        "Melanotan II (MT2)",             # coa.html
        "Melanotan 2", "Melanotan-2", "Melanotan",
        "MT2", "MT-2", "MTII",            # customer shorthand
    ]),
    ("Bacteriostatic Water", [
        "Bac Water", "Bact Water", "Bacteriostatic", "BAC-Water", "Bacto Water",
    ]),
    ("Sterile Water", [
        "Sterile H2O", "SWFI",
    ]),
    ("5-Amino/MQ", [
        "5-Amino 1MQ", "5-Amino-1MQ", "5 Amino 1MQ", "5A1MQ", "5-Amino1MQ",
    ]),
    # Sermorelin acetate IS sermorelin — acetate is just the salt form it ships
    # as. The separate "Sermorelin Acetate" cost rows were a stale second quote
    # (~2.5x the real cost) and were removed from pricing.CATALOG; this alias
    # keeps the name resolving, now to the correct row.
    ("Sermorelin", [
        "Sermorelin Acetate",
    ]),
]


def _strip(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# every stripped spelling -> the stripped canonical form for its group
_CANON: dict[str, str] = {}
for _canonical, _variants in _ALIAS_GROUPS:
    _target = _strip(_canonical)
    _CANON[_target] = _target
    for _v in _variants:
        _CANON[_strip(_v)] = _target


def canon(product: str) -> str:
    """Normalized product key, with known alternate spellings folded together.

    Returns the same token for every spelling of one physical product, so a
    lookup succeeds whichever name the caller happens to hold. Unknown products
    pass through as plain normalized text, so this can never break a product it
    does not know about.
    """
    s = _strip(product)
    return _CANON.get(s, s)


def known_aliases() -> dict[str, str]:
    """Every alias -> canonical mapping, for tests and the drift audit."""
    return dict(_CANON)
