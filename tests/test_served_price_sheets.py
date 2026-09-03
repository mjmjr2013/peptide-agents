"""
The price sheets customers actually receive — 2026-08-31.

WHY THIS EXISTS (HANDOFF §30a). When bac and sterile water went $12 → $17, the
code was right and the deploy nearly served the wrong number anyway.
`static/price_list.{xlsx,xls,pdf}` are TRACKED IN GIT, `main.py` rebuilds them
only `if not exists()`, and on Railway they exist. So production would have kept
handing out a $12 price sheet from `/price-list.xlsx`, `/price-list.xls`,
`/price-list.pdf` and `/北线集团研究肽价格表.xlsx` while the agent quoted $17 —
a customer sent one number and charged another.

It was caught by hand. That is not a control, so this file makes it a test.

The trap worth remembering: the two PNGs are gitignored and self-heal on every
deploy, so the artifact you would think to eyeball was the one already fine. The
three binaries nobody opens were the stale ones.

Run:  python3 -m pytest tests/test_served_price_sheets.py -q
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog, price_image                            # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"

# Every artifact that is committed to the repo and served to customers. The PNGs
# are deliberately absent: they are gitignored and rebuilt on each deploy.
TRACKED_SHEETS = ["price_list.xlsx", "price_list.xls", "price_list.pdf"]


@pytest.mark.parametrize("name", TRACKED_SHEETS)
def test_the_sheet_exists_and_is_not_empty(name):
    f = STATIC / name
    assert f.is_file(), f"static/{name} is missing — main.py serves it directly"
    assert f.stat().st_size > 1000, f"static/{name} looks truncated"


def _xlsx_prices() -> dict[str, float]:
    """Every (SKU -> price) actually written in the workbook we serve."""
    import openpyxl
    wb = openpyxl.load_workbook(STATIC / "price_list.xlsx")
    out: dict[str, float] = {}
    for row in wb.active.iter_rows(values_only=True):
        cells = (list(row) + [None] * 4)[:4]
        sku, _product, _spec, price = cells
        if sku and isinstance(price, str) and price.strip().startswith("$"):
            out[str(sku).strip()] = float(re.sub(r"[^0-9.]", "", price))
    return out


def test_the_served_workbook_matches_the_catalog_exactly():
    """The load-bearing test. `/price-list.xlsx` is the sheet a buyer opens, and
    openpyxl is already a dependency, so this reads the real bytes rather than
    trusting that someone remembered to regenerate."""
    served = _xlsx_prices()
    assert len(served) == len(catalog.BY_SKU), (
        f"workbook has {len(served)} priced rows, catalog has {len(catalog.BY_SKU)}")

    stale = {sku: (price, catalog.get(sku).list_price) for sku, price in served.items()
             if catalog.get(sku) and price != catalog.get(sku).list_price}
    assert stale == {}, (
        f"static/price_list.xlsx is STALE — customers are being served prices the "
        f"agent no longer quotes: {stale}. Regenerate with "
        f"RAILWAY_ENVIRONMENT=1 and commit static/. See HANDOFF §30a.")

    assert set(served) == set(catalog.BY_SKU), {
        "in the workbook only": sorted(set(served) - set(catalog.BY_SKU)),
        "in the catalog only": sorted(set(catalog.BY_SKU) - set(served)),
    }


def test_the_water_price_change_actually_reached_the_workbook():
    """The specific thing §30a nearly shipped, pinned by name.

    Water has now moved twice ($12 -> $17 on 2026-08-31, $17 -> $20 on
    2026-09-03) and sterile water left the catalog entirely, so pinning the
    literal is what would go stale next. The property that actually failed in
    §30a is asserted instead: the number in the file customers download equals
    the number the agent quotes. tests/test_price_baseline.py is what pins the
    literal."""
    served = _xlsx_prices()
    assert served["BAC10"] == catalog.get("BAC10").list_price, (
        f"bac water is ${served['BAC10']} on the served sheet but "
        f"${catalog.get('BAC10').list_price} in the catalog")
    assert "STW10" not in served, (
        "sterile water was removed on 2026-09-03 but is still on the served sheet — "
        "rebuild it with regenerate_price_sheets.sh")


# ── The two formats that cannot be read back ─────────────────────────────────
# .xls needs xlrd (not a dependency) and the PDF's numbers are drawn with a
# subsetted matplotlib font, so no text extractor recovers them — pdftotext
# returns the headings with the digits missing entirely. A stamp is the only
# practical way to know those two are current.

def _stamp() -> dict:
    f = STATIC / "price_list.stamp.json"
    assert f.is_file(), (
        "static/price_list.stamp.json is missing. Certify the committed sheets "
        "with: python3 -c \"from core.price_image import stamp_static_sheets as s; s()\"")
    return json.loads(f.read_text())


def test_the_sheets_carry_todays_prices():
    """Catches the exact §30a mistake. A regeneration that went to iCloud leaves
    the committed sheets — and therefore this fingerprint — behind."""
    stamp = _stamp()
    assert stamp["fingerprint"] == price_image.price_fingerprint(), (
        f"the committed price sheets were certified against a different set of "
        f"prices (stamp {stamp['fingerprint']}, current "
        f"{price_image.price_fingerprint()}), last verified {stamp.get('verified_at')}. "
        f"Regenerate with RAILWAY_ENVIRONMENT=1 and commit static/. See HANDOFF §30a.")


@pytest.mark.parametrize("name", TRACKED_SHEETS)
def test_each_sheet_is_the_file_that_was_certified(name):
    """The .xls needs a library we do not depend on and the PDF's numbers are
    drawn with a subsetted font no extractor recovers, so neither can be read
    back. Hashing the bytes covers them: swap an old one in and this goes red."""
    stamp = _stamp()
    assert name in stamp["artifacts"], f"{name} is not covered by the stamp"
    assert price_image._sha256(STATIC / name) == stamp["artifacts"][name], (
        f"static/{name} is not the file that was certified against the current "
        f"prices — it has been replaced or rebuilt without re-stamping.")


def test_verify_static_sheets_agrees_with_the_parsed_workbook():
    """Two independent routes to the same conclusion: the helper the deploy path
    uses, and this file reading the cells itself."""
    assert price_image.verify_static_sheets() == price_image.price_fingerprint()
    assert _xlsx_prices()["BAC10"] == catalog.get("BAC10").list_price


def test_a_machine_without_chinese_fonts_refuses_to_build_the_sheets():
    """The sheets are bilingual. matplotlib substitutes silently, so a build on
    the wrong machine produces a sheet whose Chinese is all hollow boxes and
    nothing downstream notices — confirmed by rendering one on 2026-08-31."""
    import matplotlib.font_manager as fm
    real = fm.fontManager.ttflist
    try:
        fm.fontManager.ttflist = []
        with pytest.raises(price_image.CJKFontMissing):
            price_image._assert_cjk_font_available()
    finally:
        fm.fontManager.ttflist = real


def test_the_font_guard_checks_the_fonts_the_renderer_actually_asks_for():
    """The first version of this guard looked for ANY CJK font, including ones
    the renderer never requests. A container that happened to have Noto CJK
    installed sailed through it and produced a PDF of empty boxes anyway, because
    the Chinese sheet asks only for the four Mac fonts and then falls back to
    DejaVu Sans — which resolves everywhere and has no CJK glyphs at all.

    So the guard's list must be exactly the renderer's list minus that fallback.
    """
    import inspect
    src = inspect.getsource(price_image.generate_price_list_image)
    requested = re.search(r'rcParams\["font\.family"\]\s*=\s*\[(.*?)\]', src, re.S)
    assert requested, "could not find the CN font list — did the renderer change?"
    names = re.findall(r'"([^"]+)"', requested.group(1))
    assert names[-1] == "DejaVu Sans", "the fallback moved; re-check this guard"
    assert price_image.CJK_FONTS == names[:-1], (
        f"the guard checks {price_image.CJK_FONTS} but the renderer asks for "
        f"{names[:-1]}. A guard that checks a different list than the renderer "
        f"uses will pass while the output is tofu.")
    assert "DejaVu Sans" not in price_image.CJK_FONTS, \
        "DejaVu resolves on every machine and has no CJK glyphs — it can never count"
