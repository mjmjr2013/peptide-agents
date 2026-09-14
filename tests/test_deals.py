"""Pin every live deal code (core/deals.py).

A deal is a human-set basket at a human-set total, so nothing derives it — which
also means nothing else would notice a stray edit. These pin the numbers Daniel
approved. If one fails, the deal changed: confirm with him before touching the
expected value.
"""
from core import catalog, deals, shipping


def _line_items(deal):
    return [{"product": p, "spec": s, "kits": k, "sku": sku}
            for sku, p, s, k, _wl in deal["items"]]


# ── DIEGO26 (HANDOFF §24) ─────────────────────────────────────────────────────
def test_diego26_is_pinned():
    d = deals.get_deal("DIEGO26")
    assert deals.total_kits(d) == 30
    assert deals.branded_kits(d) == 25
    assert deals.variation_count(d) == 21
    assert deals.grand_total(d) == 3393.64
    assert d["one_time"] and d["requires_artwork"]


# ── USSTOCK26 — Daniel's at-cost US warehouse stock order (2026-09-11) ───────
def test_usstock26_basket_is_daniels_list():
    """20 lines / 62 kits exactly as sent on iMessage, with the two SKU labels he
    used that are not catalog SKUs mapped (KLOW80→KLOW, 10AM→5AM10)."""
    d = deals.get_deal("USSTOCK26")
    kits = {sku: k for sku, _p, _s, k, _wl in d["items"]}
    assert kits == {
        "RT20": 6, "RT100": 1, "TR10": 3, "TR20": 3, "SM10": 3, "KLOW": 2,
        "BC10": 4, "BT10": 2, "MS10": 2, "MS20": 4, "MS40": 1, "TSM10": 2,
        "NJ1000": 2, "DS5": 6, "50AM": 2, "5AM10": 2, "P41": 3, "XA10": 2,
        "SK10": 2, "BAC10": 10,
    }
    assert deals.total_kits(d) == 62


def test_usstock26_total_is_daniels_figure_and_is_at_cost():
    """$1,140 is his number. It must stay within a few dollars of the catalog's
    cost basis — the whole point is an AT-COST order, and if the cost sheet moves
    far enough that this fails, the deal is no longer what he asked for."""
    d = deals.get_deal("USSTOCK26")
    assert deals.grand_total(d) == 1140.00
    assert d["shipping"] == 0 and d["white_label_fee"] == 0
    cost = sum(catalog.get(sku).cost * k for sku, _p, _s, k, _wl in d["items"])
    assert abs(cost - 1140.00) < 10, f"catalog cost basis is {cost:.2f}"


def test_usstock26_is_plain_stock_not_a_label_job():
    """Northline's own labels, so no artwork step and nothing for the sticker
    factory: the factory path keys on requires_artwork / branded designs."""
    d = deals.get_deal("USSTOCK26")
    assert d["one_time"] is True
    assert d["requires_artwork"] is False
    assert deals.branded_kits(d) == 0
    assert deals.branded_designs(d) == []
    assert deals.labeling_split(d) is None


def test_usstock26_packs_and_prices_for_jason():
    """The same split the manifest shows and the §32 payout pays on. Pinned so a
    weight or cap change is noticed against a real basket: 3 mixed boxes at the
    2 kg cap plus the exempt water box."""
    d = deals.get_deal("USSTOCK26")
    boxes = shipping.split_packages(_line_items(d))
    assert len(boxes) == 4
    assert sum(b["kits"] for b in boxes) == 62
    water = [b for b in boxes if all(c["sku"] == "BAC10" for c in b["contents"])]
    assert len(water) == 1 and water[0]["kits"] == 10
