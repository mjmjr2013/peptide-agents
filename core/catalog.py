from __future__ import annotations
"""
The catalog — ONE SKU-keyed view of every product we sell.

WHY THIS EXISTS (2026-08-31). Product facts were spread across three files that
each knew part of the truth and none of which agreed:

    core/pricing.py        cost basis + the names injected into Lily's prompt
    core/price_image.py    SKU, category, and the price the customer actually sees
    website/coa.html       a third hardcoded copy with its own spellings

Nothing tied a SKU to its cost, and nothing anywhere knew what a kit WEIGHS —
so `_shipping_fee()` could only look at dollars. HANDOFF §29 traced a whole
class of revenue bug to that split; this module closes it.

WHAT THIS IS NOT: a fourth copy. Re-typing 155 rows of live money data would be
the single riskiest edit possible on this system. Instead the catalog is JOINED
from the existing sources at import time and the join is asserted total, so the
files stay authoritative for what they already drive —

    price_image.CATEGORIES  ->  the customer price-list image, XLSX and PDF
    pricing.CATALOG         ->  the prompt text Lily quotes from

— and drift between them becomes a TEST FAILURE (`audit()`) instead of a
silently unpriceable line. Facts that had no home before (form, shipped weight,
label artwork) live here, keyed by SKU, because there is now a place to put them.

READ `unit_weight_g` AS THE SHIPPED WEIGHT OF ONE KIT: ten filled vials plus the
box, foam and desiccant they travel in — the number a scale shows, not the mass
of the peptide.

ADDING A PRODUCT: add the cost row to pricing.CATALOG and the sheet row to
price_image.CATEGORIES exactly as before, then give the SKU a weight here. The
audit fails until all three agree, which is the point.
"""
import math
import re
from pathlib import Path

from core import price_image, pricing
from core.aliases import canon
from core.price_sheets import ROWS as _PRICE_ROWS

_US_BY_SKU = {r["sku"]: r for r in pricing.US_CATALOG}

# ── Form classes ─────────────────────────────────────────────────────────────
# The distinction that matters for shipping is water. A lyophilized kit is
# powder in glass; a liquid kit is ten vials of mostly water and weighs ~3.3x
# as much for a small fraction of the value. Bacteriostatic water at $12/kit is
# ~$48/kg of value against ~$11,920/kg for RT100 — which is why free shipping
# can never be decided on dollars alone (HANDOFF §29).
#
# This is deliberately a CLASS, derived from the spec's unit of measure, not a
# list of SKUs. BAC10 is not special; it is simply liquid, and so are STW10,
# LC216 and MIC10 — and so is anything liquid added later, automatically.
LYOPHILIZED = "lyophilized"
LIQUID = "liquid"

# Shipped weight of one kit, in grams, by form. Jordan's measured figures,
# 2026-08-31. A liquid kit is 3.6x a lyophilized one — that ratio is the whole
# reason weight has to enter the shipping decision.
FORM_DEFAULT_WEIGHT_G: dict[str, float] = {
    LYOPHILIZED: 75.0,   # 10 glass vials of powder + foam and desiccant
    LIQUID: 270.0,       # 10 x 10 mL of water + the same packing
}

# Per-PRODUCT overrides, in grams per kit, for products whose vials are heavier
# than their form's default. NAD and Glutathione ship in larger vials — their
# doses are hundreds to thousands of milligrams, so the glass is bigger even
# though the contents are still powder. Keyed by canonical product name, so
# every dose of that product inherits it and a new dose needs no edit.
PRODUCT_WEIGHT_G: dict[str, float] = {
    canon("NAD"): 170.0,
    canon("Glutathione"): 170.0,
}

# Per-SKU overrides, in grams per kit. Highest precedence — use this only when
# ONE dose of a product differs from its siblings. Empty is the healthy state.
SKU_WEIGHT_G: dict[str, float] = {}

# The shipping box itself: mailer, void fill and tape, weighed empty. Added ONCE
# PER PACKAGE, not per kit, so it belongs to the split math rather than to any
# product. With a 2 kg cap this is 17.5% of the allowance, which is why the
# split has to work in gross weight — see core/shipping.py.
PACKAGE_TARE_G = 350.0

# ── Sticker artwork ──────────────────────────────────────────────────────────
# THE FILENAME IS THE MAPPING: static/labels/<SKU>.png. There is deliberately no
# hand-maintained SKU->file table, because a table is one more copy to drift out
# of step with reality — the failure mode of §29, §30 and the label album itself,
# which spells several SKUs differently again (5AM5, KLOW80, HGH10, AD5...).
#
# A SKU with no file has NO sticker, and the manifest says so IN RED rather than
# printing a blank cell: at a bench a blank reads as "no sticker needed". Never
# point a SKU at "close enough" artwork — a sticker with the wrong strength on it
# is the exact failure this whole manifest exists to prevent, which is why the
# label mapping matches on product AND dose and refuses anything less.
LABEL_DIR = Path(__file__).parent.parent / "static" / "labels"
LABEL_EXTENSIONS = (".png", ".jpg", ".jpeg")


def label_path(sku: str):
    """Filesystem path to a SKU's sticker image, or None if none is on file."""
    key = (sku or "").strip().upper()
    if not key:
        return None
    for ext in LABEL_EXTENSIONS:
        p = LABEL_DIR / f"{key}{ext}"
        if p.is_file():
            return p
    return None


def _load_label_text() -> dict[str, str]:
    """What is PRINTED on each sticker, keyed by SKU.

    Not the catalog name: the stickers carry Northline's own product naming
    ("GLP-3 RT" for Retatrutide, "WOLVERINE", "MT-2"), and a crew matching the
    sheet against a sheet of stickers needs the printed wording, not ours.
    Generated from the label album filenames; edit the JSON, not this code.
    """
    import json
    f = LABEL_DIR / "label_text.json"
    if not f.is_file():
        return {}
    try:
        return {str(k).upper(): str(v) for k, v in json.loads(f.read_text()).items()}
    except Exception as e:                       # never let bad JSON break pricing
        print(f"[catalog] could not read {f}: {e!r}")
        return {}


SKU_LABEL_TEXT: dict[str, str] = _load_label_text()


def labels_missing() -> list[str]:
    """Every SKU with no sticker on file — the gap list to chase with Jordan."""
    return sorted(s for s in BY_SKU if label_path(s) is None)


# Artwork we are deliberately KEEPING for a SKU that is not currently sold.
#
# Jordan pulled these on 2026-09-03 pending confirmation from the lab that they
# can be supplied — "for now", not discontinued. Dermorphin's stickers were
# deleted outright on 2026-08-31 because that decision was final; these are not,
# and re-cutting three sheets of artwork to un-pause a product is pure waste.
#
# Being listed here ONLY exempts the file from the orphan check. It does not
# make the SKU sellable: it is absent from core/price_sheets.py, so it does not
# price, and an order line for it fails closed like any other (HANDOFF §29).
RETIRED_LABEL_SKUS: frozenset[str] = frozenset({"RT80", "TR80", "STW10"})


def labels_orphaned() -> list[str]:
    """Sticker files that match no SKU we sell and are not deliberately kept —
    artwork for a discontinued or not-yet-listed product. Harmless, but worth
    knowing about."""
    if not LABEL_DIR.is_dir():
        return []
    return sorted(p.stem for p in LABEL_DIR.iterdir()
                  if p.suffix.lower() in LABEL_EXTENSIONS
                  and p.stem.upper() not in BY_SKU
                  and p.stem.upper() not in RETIRED_LABEL_SKUS)

# ── Customs risk ─────────────────────────────────────────────────────────────
# The 2 kg package cap is about SEIZURE RISK, not carrier limits: a small parcel
# attracts less attention than a heavy one. Bacteriostatic water is not a
# controlled or interesting substance, so a box of nothing but bac water carries
# no such risk and does not need splitting (Jordan, 2026-08-31).
#
# This is a NARROW exemption, listed by SKU on purpose. It is not "liquids" and
# it is not "cheap things" — Lipo-C and MIC are liquid too and are NOT here. It
# is the specific products whose contents are uninteresting at a border: water.
# Sterile water is the same thing without the benzyl alcohol, so it qualifies on
# the same reasoning and was added alongside bac water when the two were priced
# the same (Jordan, 2026-08-31) — pricing freight in only works if both actually
# ship the cheap way.
#
# A wrong entry here means an oversized box of something that should have been
# split, so add to this ONLY on an explicit decision, never by inference from
# form or price.
#
# STW10 (sterile water) was removed on 2026-09-03: Daniel's new sheets do not
# carry it, and Jordan pulled it pending confirmation from the lab that they can
# sell it. Its reasoning is preserved above so it can be added straight back if
# it returns — the exemption was never about the SKU, it was about the contents.
UNRESTRICTED_SKUS: frozenset[str] = frozenset({"BAC10"})


def is_unrestricted(sku: str) -> bool:
    """True if this SKU may ship in a package that ignores the weight cap."""
    return (sku or "").strip().upper() in UNRESTRICTED_SKUS


def _unit_of(spec: str) -> str:
    """The unit of measure in a spec: 'mg', 'ml', 'iu', 'mcg' or 'g'."""
    m = re.search(r"\d+\.?\d*\s*(mg|ml|iu|mcg|g)\b", (spec or "").lower())
    return m.group(1) if m else ""


def _dose_of(spec: str) -> float | None:
    """The numeric dose in a spec: '10mg x10' -> 10.0."""
    m = re.search(r"(\d+\.?\d*)\s*(?:mg|ml|iu|mcg|g)\b", (spec or "").lower())
    return float(m.group(1)) if m else None


def _vials_of(spec: str) -> int:
    """Vials per kit. 'x10' means ten; a spec without a multiplier is a single."""
    m = re.search(r"x\s*(\d+)", (spec or "").lower())
    return int(m.group(1)) if m else 1


def _form_of(spec: str) -> str:
    """Lyophilized unless the spec is measured in millilitres."""
    return LIQUID if _unit_of(spec) == "ml" else LYOPHILIZED


class Item:
    """One SKU. Everything the rest of the system needs to know about a product.

    `product`/`spec` are the pricing.CATALOG spellings — the ones Lily sees and
    quotes. `sheet_product`/`sheet_spec` are the price-sheet spellings the
    customer sees. They differ for five products and that is intentional; see
    core/aliases.py.
    """

    __slots__ = ("sku", "product", "spec", "sheet_product", "sheet_spec",
                 "category", "unit", "dose", "vials", "form", "cost",
                 "list_price", "reseller_price", "trading_price",
                 "us_kit_price", "us_vial_price",
                 "unit_weight_g", "weight_source", "label_file")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def is_liquid(self) -> bool:
        return self.form == LIQUID

    @property
    def in_us_warehouse(self) -> bool:
        """True if the US warehouse stocks this SKU. Only 30 of 151 do."""
        return self.us_kit_price is not None

    def price(self, kits: float = 1,
              warehouse: str = pricing.DEFAULT_WAREHOUSE) -> float | None:
        """Per-kit price at an order size. None means we cannot sell it there."""
        return pricing.price_for_sku(self.sku, kits, warehouse)

    def weight_g(self, kits: float = 1) -> float:
        """Shipped weight for `kits` kits of this SKU, in grams."""
        return round(self.unit_weight_g * kits, 1)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self) -> str:
        return f"<Item {self.sku} {self.product} {self.spec} ${self.list_price}>"


def _key(product: str, spec: str) -> tuple[str, str]:
    """The join key: canonical product name + leading dose token."""
    return (canon(product), price_image._norm_dose(spec))


def _build() -> tuple[dict[str, Item], dict[tuple[str, str], Item]]:
    """Join the cost catalog to the price sheet and layer weights on top."""
    # cost side, keyed for lookup
    cost_by_key: dict[tuple[str, str], dict] = {}
    for row in pricing.CATALOG:
        cost_by_key[_key(row["product"], row["spec"])] = row

    by_sku: dict[str, Item] = {}
    by_key: dict[tuple[str, str], Item] = {}

    for category, rows in price_image.CATEGORIES:
        for sku, sheet_product, sheet_spec, price_str in rows:
            k = _key(sheet_product, sheet_spec)
            cost_row = cost_by_key.get(k)
            # A sheet row with no cost row is a hole in the join. audit() reports
            # it; we still build the row so the failure is visible rather than
            # the SKU quietly vanishing from every downstream consumer.
            spec = cost_row["spec"] if cost_row else sheet_spec
            product = cost_row["product"] if cost_row else sheet_product
            cost = cost_row["cost"] if cost_row else None

            try:
                list_price = float(re.sub(r"[^0-9.]", "", price_str))
            except ValueError:
                list_price = None

            _sheet = _PRICE_ROWS.get(sku)
            _us = _US_BY_SKU.get(sku)

            form = _form_of(spec)
            # SKU override beats product override beats form default.
            weight = SKU_WEIGHT_G.get(sku)
            source = "sku"
            if weight is None:
                weight = PRODUCT_WEIGHT_G.get(canon(product))
                source = "product"
            if weight is None:
                weight = FORM_DEFAULT_WEIGHT_G[form]
                source = "form"

            item = Item(
                sku=sku,
                product=product,
                spec=spec,
                sheet_product=sheet_product,
                sheet_spec=sheet_spec,
                category=category,
                unit=_unit_of(spec),
                dose=_dose_of(spec),
                vials=_vials_of(spec),
                form=form,
                cost=cost,
                list_price=list_price,
                reseller_price=_sheet[5] if _sheet else None,
                trading_price=_sheet[6] if _sheet else None,
                us_kit_price=(_us["kit_price"] if _us else None),
                us_vial_price=(_us["vial_price"] if _us else None),
                unit_weight_g=float(weight),
                weight_source=source,
                label_file=None,   # filled in below, once label_path() is defined
            )
            by_sku[sku] = item
            by_key[k] = item

    return by_sku, by_key


BY_SKU, _BY_KEY = _build()
ITEMS: list[Item] = list(BY_SKU.values())

# label_file mirrors label_path() so an Item is self-contained for callers that
# already hold one. The FILE ON DISK stays the source of truth — this is a cache
# filled once at import, not a second registry to keep in step.
for _item in ITEMS:
    _p = label_path(_item.sku)
    _item.label_file = _p.name if _p else None


# ── Lookup ───────────────────────────────────────────────────────────────────

def get(sku: str) -> Item | None:
    """The item for a SKU code, or None."""
    return BY_SKU.get((sku or "").strip().upper())


def find(product: str, spec: str = "") -> Item | None:
    """The item for a product/spec as a human (or Lily) would write it.

    Resolves through the same alias table and dose normalization the pricing
    lookups use, so anything that prices here also resolves here — and anything
    that does not resolve returns None rather than a wrong SKU. Callers must
    treat None as "refuse the line", never as "assume a default" (HANDOFF §29).
    """
    item = _BY_KEY.get(_key(product, spec))
    if item is not None:
        return item
    # Fall back to the pricing matcher, which also does substring and
    # single-candidate matching, then re-key off the row it found.
    row = pricing.find_item(product, spec)
    if row is None:
        return None
    return _BY_KEY.get(_key(row["product"], row["spec"]))


def weight_for(product: str, spec: str = "", kits: float = 1) -> float | None:
    """Shipped grams for `kits` kits of a product/spec, or None if unresolved."""
    item = find(product, spec)
    return None if item is None else item.weight_g(kits)


# ── Self-audit ───────────────────────────────────────────────────────────────

def audit() -> dict[str, list]:
    """Every way the three catalog sources can disagree, as lists of problems.

    An empty list for every key means the catalog is consistent. The test suite
    asserts exactly that, so drift fails CI instead of reaching a customer.
    """
    problems: dict[str, list] = {
        "sheet_rows_without_cost": [],
        "cost_rows_without_sheet": [],
        "duplicate_skus": [],
        "unpriceable_skus": [],
        "unweighed_skus": [],
        "sku_mismatch": [],
    }

    sheet_keys = set()
    seen_skus: dict[str, int] = {}
    for category, rows in price_image.CATEGORIES:
        for sku, sheet_product, sheet_spec, _price in rows:
            sheet_keys.add(_key(sheet_product, sheet_spec))
            seen_skus[sku] = seen_skus.get(sku, 0) + 1
    problems["duplicate_skus"] = sorted(s for s, n in seen_skus.items() if n > 1)

    cost_keys = {_key(r["product"], r["spec"]) for r in pricing.CATALOG}
    for item in ITEMS:
        k = _key(item.sheet_product, item.sheet_spec)
        if k not in cost_keys:
            problems["sheet_rows_without_cost"].append(item.sku)
        if item.list_price is None or item.cost is None:
            problems["unpriceable_skus"].append(item.sku)
        if not item.unit_weight_g or item.unit_weight_g <= 0:
            problems["unweighed_skus"].append(item.sku)
        # the SKU the price sheet assigns must be the SKU we resolve to
        if price_image.get_sku(item.sheet_product, item.sheet_spec) != item.sku:
            problems["sku_mismatch"].append(item.sku)

    for row in pricing.CATALOG:
        if _key(row["product"], row["spec"]) not in sheet_keys:
            problems["cost_rows_without_sheet"].append(f"{row['product']} {row['spec']}")

    return problems


def catalog_summary() -> str:
    """Human-readable roll-up, for the handoff and for eyeballing after a change."""
    lyo = [i for i in ITEMS if i.form == LYOPHILIZED]
    liq = [i for i in ITEMS if i.form == LIQUID]
    heavy = [i for i in ITEMS if i.weight_source in ("sku", "product")]
    lines = [
        f"{len(ITEMS)} SKUs — {len(lyo)} lyophilized, {len(liq)} liquid",
        f"weights: {len(ITEMS) - len(heavy)} from form default, {len(heavy)} overridden "
        f"({', '.join(sorted({i.product for i in heavy})) or 'none'})",
        f"labels:  {len(ITEMS) - len(labels_missing())} of {len(ITEMS)} SKUs have a sticker"
        + (f", {len(labels_orphaned())} orphaned" if labels_orphaned() else ""),
    ]
    probs = {k: v for k, v in audit().items() if v}
    lines.append("audit:   clean" if not probs else f"audit:   {probs}")
    return "\n".join(lines)
