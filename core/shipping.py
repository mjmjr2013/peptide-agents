from __future__ import annotations
"""
Shipping weight and package splitting.

WHY THIS EXISTS (2026-08-31). `_shipping_fee()` decided free shipping on the
DOLLAR subtotal with no weight term at all. Value per kilo across the catalog
spans three orders of magnitude:

    RT100 (Retatrutide 100mg)   $894 / 75 g   = ~$11,920 per kg
    BAC10 (bacteriostatic water) $12 / 270 g  =      ~$44 per kg

So ~84 kits of bacteriostatic water reach the $1,000 free-shipping threshold at
about 23 kg — twelve packages we eat entirely. One kit of RT100 is $894 at 75 g.
The same rule, wildly different economics. HANDOFF §29 called this out and asked
that it be fixed as a liquid/supplies CLASS on the consolidated catalog, never as
a BAC10 special case; `core/catalog.py` provides the class, and this module is
the only place that turns weight into a decision.

TWO THINGS LIVE HERE and they are deliberately separate:

  1. `split_packages()` — HOW MANY BOXES an order ships in. Pure physics, no
     money. The 2 kg cap is self-imposed and the split is BALANCED, not greedy:
     a 3 kg order becomes two ~1.5 kg boxes, not 2.0 + 1.0. Used by the
     warehouse manifest and, later, the labeling manifest.

  2. `shipping_quote()` — WHAT THE CUSTOMER PAYS. Deliberately UNCHANGED: still
     $95 standard, free over $1,000, $235 expedited, decided on dollars.

HOW THE BAC WATER HOLE WAS ACTUALLY CLOSED (Jordan, 2026-08-31). Not by denying
free shipping to heavy orders — by pricing the carriage into the product. Bac
water went $12 -> $17 a kit, so the freight is paid for at the till and a bulk
water buyer still gets the simple flat quote everyone else gets. That decision
also made the cap wrong for water specifically: the 2 kg limit is about seizure
risk, and a box of bac water has none, so bac water is exempt from splitting and
84 kits ship as ONE box instead of fourteen (`catalog.UNRESTRICTED_SKUS`).

The remaining exposure is anything ELSE cheap and heavy. Rather than guess a
threshold, `order_shipping_profile()` measures every order and the agent alerts
an operator when free shipping goes out on 4+ packages — so the next instance is
noticed on the first order rather than on a courier invoice.
"""
import math

from core import catalog

# ── The physical cap ─────────────────────────────────────────────────────────
# Self-imposed, not a carrier limit: 2 kg keeps every package in a class that
# clears customs without extra paperwork and stays easy for one person to
# handle. GROSS weight — the number a scale shows with the box on it — so the
# 350 g empty box comes out of the allowance, leaving 1,650 g of product.
PACKAGE_CAP_G = 2000.0
PAYLOAD_CAP_G = PACKAGE_CAP_G - catalog.PACKAGE_TARE_G


class Unshippable(Exception):
    """A single kit cannot fit in one package. Nothing in the catalog does this
    today (the heaviest kit is 270 g against a 1,650 g payload), but a future
    bulk SKU could, and silently shipping it wrong is worse than raising."""


def order_weight_g(items: list[dict]) -> tuple[float, list[dict]]:
    """Total product grams for an order, plus any lines whose weight is unknown.

    `items` are order lines: {"product", "spec", "kits", ...}. Returns
    (grams, unweighed). An unresolvable line contributes NOTHING to the total,
    so callers must check `unweighed` and refuse rather than under-weighing an
    order — the same fail-closed discipline as the price guard (HANDOFF §29).
    """
    grams, unweighed = 0.0, []
    for li in items or []:
        try:
            kits = int(float(li.get("kits") or li.get("quantity_kits") or 0))
        except (TypeError, ValueError):
            kits = 0
        if kits <= 0:
            continue
        item = None
        if li.get("sku"):
            item = catalog.get(li["sku"])
        if item is None:
            item = catalog.find(li.get("product", ""), li.get("spec", ""))
        if item is None:
            unweighed.append(dict(li))
            continue
        grams += item.unit_weight_g * kits
    return round(grams, 1), unweighed


def _expand_kits(items: list[dict]) -> list[tuple[float, str, str, str]]:
    """One tuple per physical kit: (grams, sku, product, spec).

    Kits are atomic — a kit is a sealed box of ten vials and cannot be halved —
    but kits of the same SKU split across packages freely, which is what makes a
    balanced split possible at all.
    """
    kits: list[tuple[float, str, str, str]] = []
    for li in items or []:
        try:
            n = int(float(li.get("kits") or li.get("quantity_kits") or 0))
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            continue
        item = (catalog.get(li["sku"]) if li.get("sku") else None) or \
            catalog.find(li.get("product", ""), li.get("spec", ""))
        if item is None:
            continue
        for _ in range(n):
            kits.append((item.unit_weight_g, item.sku, item.product, item.spec))
    return kits


def _pack(kits: list[tuple], n_boxes: int) -> list[list[tuple]] | None:
    """Longest-processing-time fit of kits into exactly n_boxes.

    Heaviest kit first into whichever box is lightest so far. That is the
    classic LPT heuristic and it is what makes the result BALANCED rather than
    greedy — a greedy fill would top out box 1 at 2.0 kg and leave box 2 at 1.0.
    Returns None if the kits do not fit in n_boxes without breaching the cap, so
    the caller can try one more box.
    """
    boxes: list[list[tuple]] = [[] for _ in range(n_boxes)]
    loads = [0.0] * n_boxes
    for kit in sorted(kits, key=lambda k: -k[0]):
        # lightest box that still has room
        candidates = [i for i in range(n_boxes) if loads[i] + kit[0] <= PAYLOAD_CAP_G]
        if not candidates:
            return None
        i = min(candidates, key=lambda i: loads[i])
        boxes[i].append(kit)
        loads[i] += kit[0]
    return boxes


# A courier will refuse a parcel past its own limit even when we are happy to
# send it. Uncapped bac-water boxes are the only ones that can approach this, so
# rather than silently splitting against Jordan's rule, a package over this is
# FLAGGED (`over_courier_limit`) and left intact for a human to look at.
COURIER_MAX_G = 30000.0


def _describe(box: list[tuple], idx: int, of: int, capped: bool) -> dict:
    rollup: dict[str, dict] = {}
    for grams, sku, product, spec in box:
        r = rollup.setdefault(sku, {"sku": sku, "product": product,
                                    "spec": spec, "kits": 0, "grams": 0.0})
        r["kits"] += 1
        r["grams"] = round(r["grams"] + grams, 1)
    payload = round(sum(k[0] for k in box), 1)
    gross = round(payload + catalog.PACKAGE_TARE_G, 1)
    return {
        "index": idx,
        "of": of,
        "kits": len(box),
        "payload_g": payload,
        "gross_g": gross,
        "capped": capped,
        "over_courier_limit": gross > COURIER_MAX_G,
        "contents": sorted(rollup.values(), key=lambda r: (-r["grams"], r["sku"])),
    }


def split_packages(items: list[dict]) -> list[dict]:
    """Split an order into balanced packages under the 2 kg gross cap.

    Returns one dict per package: {"index", "of", "kits", "payload_g",
    "gross_g", "capped", "over_courier_limit", "contents"}. `contents` is a
    per-SKU roll-up the warehouse can read straight off — and the labeling
    manifest can later hang mini label pictures off.

    The package COUNT is never hardcoded to a kit count: it falls out of the
    weights, so it changes correctly when the mix changes.

    THE BAC WATER EXEMPTION. The 2 kg cap exists to keep parcels uninteresting
    at a border, so it only binds on packages carrying something a border cares
    about. A box of nothing but bacteriostatic water is exempt and ships whole
    (Jordan, 2026-08-31) — 84 kits is ONE box, not fourteen.

    Mixing is still allowed and still preferred when it is cheaper: bac water
    riding along inside the capped boxes costs no extra parcel, while a separate
    uncapped box always costs exactly one. So both arrangements are built and
    the one with fewer packages wins, ties going to the combined shipment.
    """
    kits = _expand_kits(items)
    if not kits:
        return []

    combined = _split_capped(kits)               # everything in capped boxes
    free_kits = [k for k in kits if catalog.is_unrestricted(k[1])]
    if not free_kits:
        return [_describe(b, i, len(combined), True)
                for i, b in enumerate(combined, start=1)]

    # separated: capped boxes for the restricted goods + one uncapped water box
    restricted = [k for k in kits if not catalog.is_unrestricted(k[1])]
    separated = _split_capped(restricted) if restricted else []
    boxes = [(b, True) for b in separated] + [(free_kits, False)]

    if len(combined) <= len(boxes):              # tie -> keep the order together
        return [_describe(b, i, len(combined), True)
                for i, b in enumerate(combined, start=1)]
    return [_describe(b, i, len(boxes), capped)
            for i, (b, capped) in enumerate(boxes, start=1)]


def _split_capped(kits: list[tuple]) -> list[list[tuple]]:
    """Balanced LPT split of kits into as few capped boxes as will hold them."""
    if not kits:
        return []
    heaviest = max(k[0] for k in kits)
    if heaviest > PAYLOAD_CAP_G:
        raise Unshippable(
            f"a single {heaviest:.0f} g kit exceeds the {PAYLOAD_CAP_G:.0f} g payload cap")
    total = sum(k[0] for k in kits)
    n = max(1, math.ceil(total / PAYLOAD_CAP_G))
    boxes = _pack(kits, n)
    while boxes is None:                       # ceil() can under-count when kit
        n += 1                                 # sizes do not divide evenly
        boxes = _pack(kits, n)
    return boxes


# ── What the customer pays ───────────────────────────────────────────────────
# Unchanged behavior by default. These are the live numbers from
# agents/messaging_agent._shipping_fee().
STANDARD_USD = 95
EXPEDITED_USD = 235
FREE_OVER_USD = 1000


def shipping_quote(shipping: str, product_subtotal: float,
                   items: list[dict] | None = None) -> int:
    """The shipping charge shown to the customer, in whole dollars.

    Byte-identical in behavior to the `_shipping_fee()` this replaced. There is
    deliberately NO weight term: heavy freight is priced into the product (see
    the module docstring), not charged at checkout, so a buyer's quote never
    depends on something they cannot see. `items` is accepted so the signature
    is ready for a rule that needs it, and ignored today.
    """
    if shipping == "expedited":
        return EXPEDITED_USD
    if product_subtotal > FREE_OVER_USD:
        return 0
    return STANDARD_USD


def order_shipping_profile(items: list[dict], shipping: str,
                           product_subtotal: float) -> dict:
    """Everything internal about an order's shipping, for reports and alerts.

    Internal-only by design: customers keep seeing the flat quote. This is what
    tells you which orders are losing money on freight — `value_per_kg` is the
    number to sort by, and bac-water bulk buys sink to the bottom of that list.
    """
    grams, unweighed = order_weight_g(items)
    packages = [] if unweighed else split_packages(items)
    kg = grams / 1000.0
    return {
        "product_grams": grams,
        "product_kg": round(kg, 3),
        "packages": len(packages),
        "gross_grams": round(sum(p["gross_g"] for p in packages), 1),
        "charged_usd": shipping_quote(shipping, product_subtotal, items),
        "value_per_kg": round(product_subtotal / kg, 2) if kg else None,
        "unweighed_lines": unweighed,
        "package_detail": packages,
    }
