from __future__ import annotations
"""
White-label sticker pricing (Daniel's rate card, 2026-08-17).

Lily has ZERO discount authority on these numbers — she quotes the table and the CODE
computes the figure, the same way payment instructions are code-generated. Anything
below table rate is a human-approved deal that arrives as a promo code (see core.deals).

Rules:
  * Each product name + mg = 1 VARIATION (the label prints the name and strength, so a
    different product OR a different strength is a different label = a new variation,
    even for the same customer's same brand artwork).
  * Minimum 100 stickers per variation (print-run minimum).
  * Repeat orders are 50% off, and qualify STRUCTURALLY: a repeat is a reprint of a
    label we already hold artwork for and the factory already has on file. If the
    customer sends new artwork, or orders a product/mg we have not printed for them,
    that is a new variation at new-order rates. Nothing is self-reported.
  * The volume tier is chosen by the quantity PER VARIATION, then the rate applies to
    the total sticker count (Daniel's worked example: 21 variations x 100 = 2,100
    stickers -> 2,100 x $0.40 = $840 new / $420 repeat).
"""

MIN_PER_VARIATION = 100

# (min_qty_per_variation, max_qty_per_variation_inclusive, new_order_rate)
# Repeat rate is exactly half the new-order rate.
TIERS: list[tuple[int, int | None, float]] = [
    (100, 249, 0.40),
    (250, 499, 0.32),
    (500, 999, 0.25),
    (1000, None, 0.20),
]

REPEAT_MULTIPLIER = 0.5


class WhiteLabelError(ValueError):
    """Raised when a request cannot be quoted (e.g. below the print minimum)."""


def rate_for(qty_per_variation: int, is_repeat: bool = False) -> float:
    """Per-sticker rate for a given per-variation quantity."""
    qty = int(qty_per_variation)
    if qty < MIN_PER_VARIATION:
        raise WhiteLabelError(
            f"minimum is {MIN_PER_VARIATION} stickers per variation (got {qty})")
    for low, high, rate in TIERS:
        if qty >= low and (high is None or qty <= high):
            return round(rate * REPEAT_MULTIPLIER, 4) if is_repeat else rate
    raise WhiteLabelError(f"no tier matches {qty}")  # unreachable: last tier is open-ended


def quote(variations: int, qty_per_variation: int = MIN_PER_VARIATION,
          is_repeat: bool = False) -> dict:
    """Quote a uniform run: `variations` designs at `qty_per_variation` stickers each."""
    v = int(variations)
    if v < 1:
        raise WhiteLabelError("need at least one variation")
    rate = rate_for(qty_per_variation, is_repeat)
    stickers = v * int(qty_per_variation)
    return {
        "variations": v,
        "qty_per_variation": int(qty_per_variation),
        "stickers": stickers,
        "rate": rate,
        "is_repeat": bool(is_repeat),
        "total": round(stickers * rate, 2),
    }


def quote_mixed(lines: list[tuple[int, bool]]) -> dict:
    """Quote a run where variations differ — `lines` is [(qty_per_variation, is_repeat)],
    one entry per variation. Used when a customer reorders some existing labels (repeat
    rate) alongside new ones (new-order rate) in the same order."""
    if not lines:
        raise WhiteLabelError("need at least one variation")
    parts, total, stickers = [], 0.0, 0
    for qty, repeat in lines:
        q = quote(1, qty, repeat)
        parts.append(q)
        total += q["total"]
        stickers += q["stickers"]
    return {
        "variations": len(lines),
        "stickers": stickers,
        "total": round(total, 2),
        "lines": parts,
    }


def table_text() -> str:
    """The rate card, for injecting into Lily's prompt so she can explain it verbatim.
    She must NEVER discount these numbers."""
    rows = []
    for low, high, rate in TIERS:
        band = f"{low}-{high}" if high else f"{low}+"
        rows.append(f"  {band} per design: ${rate:.2f}/sticker new, "
                    f"${rate * REPEAT_MULTIPLIER:.3f}/sticker repeat")
    return (
        "WHITE LABEL (custom branding on the vials) — quote EXACTLY these rates, never discount:\n"
        f"  Each product name + strength = 1 design. Minimum {MIN_PER_VARIATION} stickers per design.\n"
        + "\n".join(rows) +
        "\n  Repeat orders are half price when we reprint a design we already hold artwork for.\n"
        "  A different product or strength is a NEW design at new-order rates.\n"
        "  Products ship with the customer's branding already applied to the vials.\n"
        "  You do NOT state a total yourself — the system calculates and sends it."
    )
