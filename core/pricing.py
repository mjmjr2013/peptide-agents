from __future__ import annotations
"""
Pricing — fixed prices, no negotiation.

WHAT CHANGED (2026-09-03, Jordan). The agent used to open at 6x cost, hold a 3x
floor, and negotiate in between under a discount cap that grew with order size.
That is gone. Every price is now a number on a sheet Daniel publishes, chosen by
TWO facts and nothing else:

    which warehouse   the buyer picks — US or China
    how many kits     the buyer is ordering, in total across the whole order

There is no discount authority left to exercise, no floor to defend and no
escalation for a buyer who wants more off, because there is no "more off" to
give. `core/price_sheets.py` holds the numbers; this module is the lookup.

TIERS (China only — Jordan, 2026-09-03):

        1-24 kits    standard
       25-99 kits    reseller
        100+ kits    trading company

The tier is decided by the TOTAL kits on the order, not per line. A buyer taking
30 kits spread over three products is a 30-kit buyer and gets reseller pricing on
all three — the older per-line lookup would have given them three 10-kit lookups
and the standard rate. Callers pass `kits` as that total; see
`_validate_line_items` in agents/messaging_agent.py, which sums first and prices
second for exactly this reason.

The US warehouse is a SHORTER list at ONE price for any quantity, and it is the
only place we sell single vials. No tier applies there.

The breakpoints are no longer secret. Lily may say "at 25 kits this drops to
$X" — that is now an upsell, not a leak (Jordan, 2026-09-03).
"""
import re

from core.aliases import canon as _canon_product
from core.price_sheets import ROWS as _ROWS, US_ROWS as _US_ROWS

# ── Warehouses ───────────────────────────────────────────────────────────────
WAREHOUSE_CHINA = "china"
WAREHOUSE_US = "us"
WAREHOUSES = (WAREHOUSE_CHINA, WAREHOUSE_US)
DEFAULT_WAREHOUSE = WAREHOUSE_CHINA

# ── Tiers ────────────────────────────────────────────────────────────────────
TIER_STANDARD = "standard"
TIER_RESELLER = "reseller"
TIER_TRADING = "trading"

# (minimum total kits, tier). Editing this is a pricing change — it moves what
# real buyers are charged.
TIER_BREAKS: tuple[tuple[int, str], ...] = (
    (100, TIER_TRADING),
    (25, TIER_RESELLER),
    (0, TIER_STANDARD),
)

# Index into a core.price_sheets.ROWS tuple, by tier.
_TIER_COLUMN = {TIER_STANDARD: 4, TIER_RESELLER: 5, TIER_TRADING: 6}

TIER_LABELS = {
    TIER_STANDARD: "standard (1-24 kits)",
    TIER_RESELLER: "reseller (25-99 kits)",
    TIER_TRADING: "trading company (100+ kits)",
}


def tier_for_kits(kits: float) -> str:
    """The China tier for a TOTAL order size in kits."""
    try:
        n = float(kits or 0)
    except (TypeError, ValueError):
        n = 0.0
    for minimum, tier in TIER_BREAKS:
        if n >= minimum:
            return tier
    return TIER_STANDARD


def next_tier_at(kits: float) -> tuple[int, str] | None:
    """The next breakpoint above `kits`, as (kits_needed, tier), or None.

    Lily uses this to upsell: "at 25 kits this drops to $X". It exists because
    the breakpoints are public now — before 2026-09-03 naming one was forbidden.
    """
    try:
        n = float(kits or 0)
    except (TypeError, ValueError):
        n = 0.0
    for minimum, tier in sorted(TIER_BREAKS):
        if n < minimum:
            return minimum, tier
    return None


# ── The catalog, as the rest of the system already expects it ────────────────
# Shape is unchanged from the negotiation era (product / spec / cost) so
# core/catalog.py's join and every existing caller keep working. `cost` is now
# Daniel's true USD cost, and `sku` is carried because price_sheets is keyed by
# it and the join no longer has to guess.
CATALOG: list[dict] = [
    {"sku": sku, "product": row[0], "spec": row[1], "cost": row[3]}
    for sku, row in sorted(_ROWS.items())
]

US_CATALOG: list[dict] = [
    {"sku": sku, "sheet_label": label, "vial_price": vial, "kit_price": kit}
    for sku, label, vial, kit in _US_ROWS
]
_US_BY_SKU: dict[str, dict] = {r["sku"]: r for r in US_CATALOG}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _norm_product(s: str) -> str:
    """Normalize a PRODUCT name, folding known alternate spellings together.

    Separate from _norm (which also normalizes specs) so the alias table is only
    ever consulted for product names. See core/aliases.py for why this exists.
    """
    return _canon_product(s)


def find_item(product: str, spec: str = "") -> dict | None:
    """Best-effort match of a Claude-supplied product/spec to a CATALOG row.
    Returns the matching dict or None if it can't be confidently matched.

    NOTE on ordering: the old CATALOG was hand-ordered smallest dose first, and
    the prefix match relied on that. This one is sorted by SKU, so candidates are
    tried shortest-spec-first explicitly — otherwise "10mg" could match "100mg"
    for a product whose SKU order happened to put the larger dose first.
    """
    np = _norm_product(product)
    if not np:
        return None
    candidates = [it for it in CATALOG if _norm_product(it["product"]) == np]
    if not candidates:
        candidates = [it for it in CATALOG
                      if np in _norm_product(it["product"]) or _norm_product(it["product"]) in np]
    nspec = _norm(spec)
    if nspec:
        for it in sorted(candidates, key=lambda r: len(_norm(r["spec"]))):
            nis = _norm(it["spec"])
            if nis.startswith(nspec) or nspec == nis or nspec in nis:
                return it
    if len(candidates) == 1:
        return candidates[0]
    return None


def sku_for(product: str, spec: str = "") -> str | None:
    item = find_item(product, spec)
    return item["sku"] if item else None


# ── Prices ───────────────────────────────────────────────────────────────────

def price_for_sku(sku: str, kits: float = 1,
                  warehouse: str = DEFAULT_WAREHOUSE) -> float | None:
    """Per-kit price for a SKU at an order size, or None if we cannot sell it.

    None is never "use a default" — it means refuse the line and escalate. That
    is the fail-closed rule from HANDOFF §29 and it still holds: a US buyer
    asking for a China-only product must get a human, not a China price.
    """
    key = (sku or "").strip().upper()
    if warehouse == WAREHOUSE_US:
        row = _US_BY_SKU.get(key)
        return float(row["kit_price"]) if row else None
    row = _ROWS.get(key)
    if row is None:
        return None
    return float(row[_TIER_COLUMN[tier_for_kits(kits)]])


def vial_price_for_sku(sku: str) -> float | None:
    """Single-vial price. US warehouse only — China ships full kits."""
    row = _US_BY_SKU.get((sku or "").strip().upper())
    return float(row["vial_price"]) if row else None


def get_price(product: str, spec: str = "", kits: float = 1,
              warehouse: str = DEFAULT_WAREHOUSE) -> float | None:
    """Per-kit price for a product/spec as a human would write it."""
    sku = sku_for(product, spec)
    return price_for_sku(sku, kits, warehouse) if sku else None


def get_list_price(product: str, spec: str = "",
                   warehouse: str = DEFAULT_WAREHOUSE) -> float | None:
    """The single-kit price — what the customer sees on the sheet.

    Kept under its old name because a dozen call sites and tests use it, but it
    no longer means "the price before a discount"; there are no discounts. It is
    simply the price at an order size of one.
    """
    return get_price(product, spec, 1, warehouse)


def cost_of(product: str, spec: str = "") -> float | None:
    item = find_item(product, spec)
    return item["cost"] if item else None


def sells_at(sku: str, warehouse: str) -> bool:
    """Whether a warehouse stocks a SKU at all."""
    key = (sku or "").strip().upper()
    return key in _US_BY_SKU if warehouse == WAREHOUSE_US else key in _ROWS


def us_skus() -> list[str]:
    return [r["sku"] for r in US_CATALOG]


# ── Prompt text ──────────────────────────────────────────────────────────────

def get_catalog_text(warehouse: str = DEFAULT_WAREHOUSE) -> str:
    """The pricing table injected into Lily's prompt.

    China: all three tier prices per row, because Lily may now quote the
    breakpoints out loud. US: one price plus the single-vial price, since that
    warehouse has no tiers and is the only one that sells vials.
    """
    if warehouse == WAREHOUSE_US:
        lines = ["US WAREHOUSE — one price at any quantity. Single vials available.",
                 "Product | Per vial | Per kit (10 vials)", "-" * 62]
        for r in US_CATALOG:
            lines.append(f"{r['sheet_label']} | ${int(r['vial_price'])} | ${int(r['kit_price'])}")
        return "\n".join(lines)

    lines = ["CHINA WAREHOUSE — price per kit (10 vials), by TOTAL kits on the order.",
             "Product | Spec | 1-24 kits | 25-99 kits | 100+ kits", "-" * 76]
    for item in CATALOG:
        row = _ROWS[item["sku"]]
        lines.append(f"{item['product']} | {item['spec']} | "
                     f"${int(row[4])} | ${int(row[5])} | ${int(row[6])}")
    return "\n".join(lines)


def _chunk(lines: list[str], limit: int = 1400) -> list[str]:
    """Split into WhatsApp-sized messages on line boundaries."""
    out, buf = [], ""
    for line in lines:
        if buf and len(buf) + len(line) + 1 > limit:
            out.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        out.append(buf.rstrip())
    return out


def build_price_list_messages(warehouse: str = DEFAULT_WAREHOUSE) -> list[str]:
    """The price list as WhatsApp text, for when the spreadsheet fails to send.

    GENERATED, not hand-written. The old version was a 170-line literal that had
    to be edited in step with the sheet and silently went stale when it wasn't —
    the same class of bug as the stale served sheets in HANDOFF §30b. Building it
    from price_sheets means the fallback can never quote a price the sheet does
    not.
    """
    if warehouse == WAREHOUSE_US:
        head = ["*NORTHLINE GROUP — US WAREHOUSE*",
                "US domestic stock • express shipping • $30 flat",
                "Price per vial / per kit of 10", ""]
        body = [f"*{r['sheet_label']}* — ${int(r['vial_price'])}/vial | "
                f"${int(r['kit_price'])}/kit" for r in US_CATALOG]
        tail = ["", "Reply with a product name to order. 🧬"]
        return _chunk(head + body + tail)

    head = ["*NORTHLINE GROUP — PRICE LIST*",
            "All prices per kit (10 vials) • USD",
            "25+ kits and 100+ kits are priced lower — just ask.", ""]
    grouped: dict[str, list[tuple[str, float]]] = {}
    for item in CATALOG:
        grouped.setdefault(item["product"], []).append(
            (item["spec"].replace(" x10", ""), _ROWS[item["sku"]][4]))
    body = [f"*{p}* " + " | ".join(f"{spec} ${int(price)}" for spec, price in rows)
            for p, rows in grouped.items()]
    tail = ["", "Reply with a product name for a quote, or to place an order. 🧬"]
    return _chunk(head + body + tail)


def get_price_list_messages(warehouse: str = DEFAULT_WAREHOUSE) -> list[str]:
    return build_price_list_messages(warehouse)


PRICE_LIST_MESSAGES = build_price_list_messages(WAREHOUSE_CHINA)
