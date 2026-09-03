#!/usr/bin/env python3
"""Generate core/price_sheets.py from Daniel's four spreadsheets.

WHY THIS IS GENERATED (2026-09-03). The new pricing model has five numbers per
SKU (cost, China standard, reseller, trading, and for 30 SKUs a US kit and vial
price). Re-typing 151 rows x 5 numbers of live money data by hand is exactly the
edit CLAUDE.md says never to make, so it is not made: this script reads the
workbooks and writes the module, and re-running it on a new set of sheets is the
whole update procedure.

    python3 tools/build_price_sheets.py ~/Downloads

INPUTS (all from the Harrison & Daniel thread, 2026-09-03, after the duplicate
and discontinued rows were removed — see HANDOFF §31):

    true_cost_sheet_corrected_with_USD.xlsx        cost, RMB and USD
    Northline_Group_China_Warehouse.xlsx           standard tier
    Northline_Group_Resellers.xlsx                 25+ tier
    Northline_Group_Trading_Company_FINAL.xlsx     100+ tier
    Northline_Group_US_Warehouse_FINAL_Price_Sheet.xlsx   flat US list

SKU CODES ARE OURS, NOT DANIEL'S. His sheets renamed 13 SKUs (EP0->EPO,
H8->HGH8, LGT5->LIR5, ...). We keep our codes because static/labels/<SKU>.png is
the sticker mapping and every Airtable order ever placed carries the old code —
renaming would orphan 13 sticker files and break history for no gain. The
mapping below is applied on the way IN and is the only place his codes appear.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import openpyxl

# Daniel's SKU code -> ours. Same physical product, different label.
SKU_ALIASES = {
    "10AM": "5AM10", "EPO": "EP0", "GTT6": "GTT",
    "HGH8": "H8", "HGH10": "H10", "HGH15": "H15",
    "KLOW80": "KLOW",
    "LIR5": "LGT5", "LIR10": "LGT10", "LIR30": "LGT20",
    "MT10": "MEL10", "PIN5": "PI5", "PIN10": "PI10",
}

# SKUs on Daniel's sheet that we do not carry. Empty today; kept as the place to
# record a deliberate exclusion so it survives the next regeneration.
EXCLUDE: set[str] = set()

# Product/spec wording for SKUs that are NEW to us. Everything else keeps the
# spelling core/pricing.py and core/price_image.py already use, so no customer-
# facing string moves except the price itself.
NEW_SKU_NAMES = {
    "SM60":  ("Semaglutide", "60mg x10", "Semaglutide", "60mg", "GLP-1 Peptides"),
    "SM100": ("Semaglutide", "100mg x10", "Semaglutide", "100mg", "GLP-1 Peptides"),
    "TR120": ("Tirzepatide", "120mg x10", "Tirzepatide", "120mg", "GLP-1 Peptides"),
}


def _canon_sku(raw) -> str:
    s = str(raw or "").strip()
    return SKU_ALIASES.get(s, s)


def _usd(rmb, cached, formula) -> float:
    """USD cost for one kit.

    The cost sheet's USD column is a FORMULA (`=D2/6.7222`), and a workbook
    written by anything other than Excel carries no cached result — openpyxl
    then reads it as None. So the rate is parsed out of the formula and applied
    here rather than trusted to have been recalculated. The cached value is used
    when present, which keeps this exact to Daniel's own arithmetic.
    """
    if isinstance(cached, (int, float)):
        return round(float(cached), 2)
    m = re.search(r"/\s*([\d.]+)", str(formula or ""))
    if not m:
        raise SystemExit(f"cannot read the USD rate out of {formula!r}")
    return round(float(rmb) / float(m.group(1)), 2)


def _rows(path: Path, first_data_row: int, sku_col=1, formulas=False):
    wb = openpyxl.load_workbook(path, data_only=not formulas)
    ws = wb.active
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if i < first_data_row or not row:
            continue
        raw = row[sku_col - 1]
        if raw in (None, ""):
            continue
        s = str(raw).strip()
        if s.upper().startswith("NORTHLINE") or s in ("SKU", "Cat. No."):
            continue
        yield s, row


def build(src: Path) -> dict:
    cost, china, reseller, trading = {}, {}, {}, {}

    cost_file = src / "true_cost_sheet_corrected_with_USD.xlsx"
    raw_usd = {str(sku).strip(): row[4]
               for sku, row in _rows(cost_file, 2, formulas=True)}
    for sku, row in _rows(cost_file, 2):
        cost[_canon_sku(sku)] = (
            str(row[1]).strip(), str(row[2]).strip(), float(row[3]),
            _usd(row[3], row[4], raw_usd.get(str(sku).strip())))
    for fname, sink in [
        ("Northline_Group_China_Warehouse.xlsx", china),
        ("Northline_Group_Resellers.xlsx", reseller),
        ("Northline_Group_Trading_Company_FINAL.xlsx", trading),
    ]:
        for sku, row in _rows(src / fname, 10):
            sink[_canon_sku(sku)] = float(row[3])

    skus = set(cost) & set(china) & set(reseller) & set(trading)
    missing = (set(cost) | set(china)) - skus
    if missing:
        raise SystemExit(f"sheets disagree on SKUs: {sorted(missing)}")

    # The US sheet has no SKU column — it names products the way a US buyer says
    # them ("Wolverine (BPC-157/TB-500) 10mg total", "GLOW 70mg"). Resolve each
    # through core/aliases.py so a US order carries the same SKU as a China one
    # and reaches the same sticker and manifest. A row that will not resolve is
    # fatal here rather than unpriceable at 2am (HANDOFF §29).
    from core import catalog

    us = []
    for _, row in _rows(src / "Northline_Group_US_Warehouse_FINAL_Price_Sheet.xlsx", 10):
        label = str(row[0]).strip()
        if label.upper().startswith(("EXPRESS", "NORTHLINE", "PRODUCT")):
            continue
        m = re.match(r"^(.*?)\s*([\d.]+\s*(?:mg|ml|mL|iu|IU))\b", label)
        if not m:
            raise SystemExit(f"cannot read a dose out of US row {label!r}")
        product = re.sub(r"\s*\(.*?\)\s*", " ", m.group(1)).strip()
        item = catalog.find(product, m.group(2))
        if item is None:
            raise SystemExit(
                f"US row {label!r} resolves to no SKU — add the spelling to "
                f"core/aliases.py before regenerating")
        us.append((item.sku, label, float(row[1]), float(row[2])))

    return {"cost": cost, "china": china, "reseller": reseller,
            "trading": trading, "skus": sorted(skus - EXCLUDE), "us": us}


HEADER = '''from __future__ import annotations
"""GENERATED FILE — do not hand-edit. See tools/build_price_sheets.py.

Every price the business charges, keyed by SKU. Regenerate with:

    python3 tools/build_price_sheets.py ~/Downloads

`cost_usd` is Daniel's own USD column, not a conversion we perform — the RMB
figure is carried alongside only so a disagreement is visible.

The three China columns are the SAME catalog at three order sizes; the US
warehouse is a separate, shorter list at ONE price for any quantity
(Jordan, 2026-09-03).
"""

# sku -> (product_for_lily, spec_for_lily, cost_rmb, cost_usd,
#         china_standard, reseller, trading)
ROWS: dict[str, tuple] = {
'''

FOOTER = '''}}

# The US warehouse list: sku, the wording the US sheet uses, vial price, kit
# price. ONE price at any quantity — the 25+/100+ tiers are China-only
# (Jordan, 2026-09-03).
US_ROWS: list[tuple] = [
{us}]
'''


def emit(data: dict, catalog_names: dict) -> str:
    out = [HEADER]
    for sku in data["skus"]:
        p, s = catalog_names.get(sku, (None, None))
        if p is None:
            named = NEW_SKU_NAMES.get(sku)
            if named is None:
                raise SystemExit(
                    f"{sku} is on the new sheets but has no name in "
                    f"pricing.CATALOG and no entry in NEW_SKU_NAMES")
            p, s = named[0], named[1]
        rmb, usd = data["cost"][sku][2], data["cost"][sku][3]
        out.append(
            f'    {sku!r}: ({p!r}, {s!r}, {rmb!r}, {usd!r}, '
            f'{data["china"][sku]!r}, {data["reseller"][sku]!r}, '
            f'{data["trading"][sku]!r}),\n')
    us_lines = "".join(f"    ({sku!r}, {label!r}, {vial!r}, {kit!r}),\n"
                       for sku, label, vial, kit in data["us"])
    out.append(FOOTER.format(us=us_lines))
    return "".join(out)


def _names(repo: Path) -> dict[str, tuple[str, str]]:
    """SKU -> the (product, spec) wording Lily quotes.

    Read from the PREVIOUS generation of core/price_sheets.py so regenerating is
    idempotent and never silently re-words a live product. On the very first run
    that file does not exist yet, so we bootstrap from core/catalog.py — which is
    the old join of pricing.CATALOG (Lily's spellings) onto the price sheet.
    """
    sys.path.insert(0, str(repo))
    try:
        from core import price_sheets
        return {sku: (row[0], row[1]) for sku, row in price_sheets.ROWS.items()}
    except Exception:
        from core import catalog
        return {sku: (it.product, it.spec) for sku, it in catalog.BY_SKU.items()}


def main() -> None:
    src = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Downloads"
    repo = Path(__file__).resolve().parent.parent
    catalog_names = _names(repo)
    data = build(src)
    target = repo / "core" / "price_sheets.py"
    target.write_text(emit(data, catalog_names))
    print(f"wrote {target} — {len(data['skus'])} SKUs, {len(data['us'])} US rows")


if __name__ == "__main__":
    main()
