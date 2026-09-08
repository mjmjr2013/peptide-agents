from __future__ import annotations
"""
What the warehouse rep is owed — pure arithmetic, no money moves here.

WHY THIS EXISTS (2026-09-04). Jason is paid per BOX, not per order and not per
kit, so his pay is a function of the same physics `core/shipping.py` already
computes for the manifest: kit weights -> the 2 kg cap -> a balanced split -> a
package count. Deriving the fee from `split_packages()` rather than from a
second count is the whole point. If the two ever disagreed, the manifest would
tell Jason to pack three boxes and the payout would pay him for two, and the
only way to find out would be him complaining.

THIS MODULE NEVER SENDS ANYTHING. It has no key, no network, no Airtable. It
turns an order into a number and can be run against any order, past or future,
without side effects — which is what makes it testable and what makes a disputed
payment answerable. `core/tron_payout.py` moves funds; `agents/warehouse_payout.py`
decides when. Keeping the three apart is deliberate.

FAIL CLOSED, ALWAYS TOWARD NOT PAYING. `order_weight_g` returns lines it could
not weigh, and an unweighable line contributes NOTHING to the total — so an
order with a bad SKU would silently price as fewer boxes. Every function here
refuses such an order outright (`billable=False`) instead of paying a number it
knows is wrong. Underpaying Jason is a conversation; overpaying him from a
catalog drift is an irreversible Tron transfer. Same discipline as the price
guard in HANDOFF §29.
"""
import math

from core import catalog, shipping

# ── The rate ─────────────────────────────────────────────────────────────────
# Jordan, 2026-09-04: $12 a box PLUS $15.90 a kilo, "assume box itself is 350g".
#
# TWO COMPONENTS, and they are not interchangeable. The box fee pays for the act
# of packing a parcel; the weight fee pays for what is in it. An order of one
# heavy box and an order of one light box do the same amount of packing and very
# different amounts of lifting, which is exactly why a flat per-box rate alone
# paid $12 for a 23 kg pallet of bacteriostatic water.
#
# THE WEIGHT IS GROSS, NOT PRODUCT. "Assume box itself is 350g" means the 350 g
# empty box is paid for too — so the figure is the one a scale would show with
# the box on it. That is already what `split_packages()` reports as `gross_g`,
# and 350 g is already `catalog.PACKAGE_TARE_G`, the same constant the shipping
# split uses. Do NOT reintroduce a second 350 anywhere: if the real tare ever
# changes, it must change in one place and move the packing, the manifest and
# Jason's pay together.
DEFAULT_PER_BOX_USD = 12.0
DEFAULT_PER_KG_USD = 15.90


def _per_box_usd() -> float:
    from config import settings
    return float(settings.warehouse_fee_per_box_usd)


def _per_kg_usd() -> float:
    from config import settings
    return float(settings.warehouse_fee_per_kg_usd)


class UnpriceableOrder(Exception):
    """Raised only by `fee_for_order(strict=True)`. The batch path does not use
    it — it collects unpriceable orders and reports them instead, so one bad SKU
    cannot stop Jason being paid for the other nine orders that night."""


def gross_grams(packages: list[dict]) -> float:
    """Total weight Jason is paid on: product plus one 350 g box per package.

    Read straight off `split_packages()`, which already adds
    `catalog.PACKAGE_TARE_G` to each package. An order that splits into three
    boxes therefore carries three tares — which is correct, because he packed
    three boxes.

    NOTE this makes the two components interact: a split that produces one more
    box adds both a $12 fee and another 350 g (~$5.57) of tare. That is intended.
    """
    return round(sum(float(p.get("gross_g") or 0.0) for p in packages), 1)


def fee_for_order(items: list[dict], *, strict: bool = False) -> dict:
    """Price one order's warehouse work.

    Returns a dict that is safe to log, email and store verbatim:

        {"billable": bool,        # False => DO NOT PAY for this order
         "reason": str,           # why not, when billable is False
         "packages": int,         # physical boxes Jason ships
         "boxes": int,            # boxes he is PAID for (see billable_boxes)
         "per_box_usd": float,
         "fee_usd": float,
         "product_g": float,
         "unweighed": list[dict], # lines whose weight could not be resolved
         "package_detail": list[dict]}

    `items` are order lines in the shape the rest of the system already uses:
    {"sku"|"product"+"spec", "kits"}. An empty order is billable at $0 — it is
    not an error, it just owes nothing.
    """
    rate, kg_rate = _per_box_usd(), _per_kg_usd()
    grams, unweighed = shipping.order_weight_g(items)

    if unweighed:
        detail = ", ".join(
            f"{li.get('product', '?')} {li.get('spec', '')}".strip() or "(blank line)"
            for li in unweighed)
        reason = (f"{len(unweighed)} line(s) have no catalog weight: {detail}. "
                  f"The box count would be too low, so this order is not paid.")
        if strict:
            raise UnpriceableOrder(reason)
        return {"billable": False, "reason": reason, "packages": 0, "boxes": 0,
                "per_box_usd": rate, "per_kg_usd": kg_rate, "fee_usd": 0.0,
                "box_usd": 0.0, "weight_usd": 0.0, "product_g": grams,
                "gross_g": 0.0, "unweighed": unweighed, "package_detail": []}

    try:
        packages = shipping.split_packages(items)
    except shipping.Unshippable as e:
        reason = f"cannot be packed: {e}"
        if strict:
            raise UnpriceableOrder(reason) from e
        return {"billable": False, "reason": reason, "packages": 0, "boxes": 0,
                "per_box_usd": rate, "per_kg_usd": kg_rate, "fee_usd": 0.0,
                "box_usd": 0.0, "weight_usd": 0.0, "product_g": grams,
                "gross_g": 0.0, "unweighed": [], "package_detail": []}

    boxes = len(packages)
    gross = gross_grams(packages)
    # Round each component to cents, then add. Rounding once at the end would let
    # a half-cent in the weight term drift the total away from the two numbers
    # printed on Jason's statement, and a payment he cannot re-add by hand is a
    # payment he has to take on trust.
    box_usd = round(boxes * rate, 2)
    weight_usd = round((gross / 1000.0) * kg_rate, 2)
    return {"billable": True, "reason": "", "packages": boxes, "boxes": boxes,
            "per_box_usd": rate, "per_kg_usd": kg_rate,
            "box_usd": box_usd, "weight_usd": weight_usd,
            "fee_usd": round(box_usd + weight_usd, 2),
            "product_g": grams, "gross_g": gross,
            "unweighed": [], "package_detail": packages}


def fee_for_batch(orders: list[dict]) -> dict:
    """Price a night's worth of orders.

    `orders` is a list of {"id", "ref", "items", "problems"?} — deliberately NOT
    Airtable records, so this module never learns Airtable's shape and the whole
    batch calculation is testable from a literal. `problems` is an optional list
    of reasons the CALLER already knows make the order unpriceable.

    Returns {"rows", "skipped", "orders", "boxes", "total_usd", "per_box_usd"}.
    `rows` are the orders to pay, each carrying its own fee; `skipped` are the
    ones that could not be priced, each with a `reason`. THE TOTAL COVERS `rows`
    ONLY — a skipped order is never silently folded in at zero and forgotten;
    the caller is expected to surface `skipped` to a human.
    """
    rate, kg_rate = _per_box_usd(), _per_kg_usd()
    rows, skipped = [], []
    for o in orders or []:
        # `problems` lets the CALLER refuse an order outright — it is how a line
        # the caller could not even read (a `kits` field retyped to text, an
        # Order Item row with no fields at all) refuses the order explicitly.
        # Do not try to express that by mangling the product name instead:
        # `catalog.find` matches on a substring, so "[unreadable] Retatrutide"
        # still resolves to Retatrutide and the order prices as if nothing were
        # wrong. An explicit flag cannot be fuzzy-matched away.
        problems = [str(x) for x in (o.get("problems") or []) if str(x).strip()]
        if problems:
            skipped.append({"id": o.get("id"), "ref": o.get("ref", ""),
                            "billable": False, "reason": "; ".join(problems),
                            "packages": 0, "boxes": 0, "per_box_usd": rate,
                            "per_kg_usd": kg_rate, "fee_usd": 0.0,
                            "box_usd": 0.0, "weight_usd": 0.0,
                            "product_g": 0.0, "gross_g": 0.0, "unweighed": [],
                            "package_detail": []})
            continue
        fee = fee_for_order(o.get("items") or [])
        row = {"id": o.get("id"), "ref": o.get("ref", ""), **fee}
        (rows if fee["billable"] else skipped).append(row)
    boxes = sum(r["boxes"] for r in rows)
    return {"rows": rows, "skipped": skipped, "orders": len(rows),
            "boxes": boxes, "gross_g": round(sum(r["gross_g"] for r in rows), 1),
            "box_usd": round(sum(r["box_usd"] for r in rows), 2),
            "weight_usd": round(sum(r["weight_usd"] for r in rows), 2),
            "total_usd": round(sum(r["fee_usd"] for r in rows), 2),
            "per_box_usd": rate, "per_kg_usd": kg_rate}


def statement_text(batch: dict, label: str) -> str:
    """The plain-text statement Jason gets with his payment.

    He should be able to check the number himself without asking anyone. With two
    components that means showing BOTH — boxes and kilos, each priced — not just
    a total. A rate he cannot re-apply to his own count is a rate he has to trust.
    """
    lines = [f"Northline — warehouse payment, {label}", ""]
    # ref can arrive as an int if order_ref is ever an autonumber field.
    refs = {id(r): str(r.get("ref") or "") for r in batch["rows"] + batch["skipped"]}
    w = max([len(v) for v in refs.values()] + [8])
    lines.append(f"  {'ORDER':<{w}}  BOXES   GROSS KG      BOXES $    WEIGHT $     TOTAL")
    for r in sorted(batch["rows"], key=lambda r: str(r.get("ref") or "")):
        lines.append(
            f"  {refs[id(r)]:<{w}}  {r['boxes']:>5}  {r['gross_g'] / 1000.0:>9.2f}  "
            f"{r['box_usd']:>11.2f}  {r['weight_usd']:>10.2f}  {r['fee_usd']:>8.2f}")
    lines += [
        "  " + "-" * (w + 54),
        f"  {'TOTAL':<{w}}  {batch['boxes']:>5}  {batch['gross_g'] / 1000.0:>9.2f}  "
        f"{batch['box_usd']:>11.2f}  {batch['weight_usd']:>10.2f}  "
        f"{batch['total_usd']:>8.2f}",
        "",
        f"Rate: ${batch['per_box_usd']:.2f} per box plus "
        f"${batch['per_kg_usd']:.2f} per kg.",
        "Gross kg includes the empty box at 350 g each, so a three-box order "
        "carries three.",
        "Box count is the same split shown on your manifest.",
        ""]
    if batch["skipped"]:
        lines += ["NOT INCLUDED (Northline is checking these — they will be on a "
                  "later payment, nothing is lost):"]
        lines += [f"  {refs[id(r)]}: {r['reason']}" for r in batch["skipped"]]
        lines += [""]
    return "\n".join(lines)
