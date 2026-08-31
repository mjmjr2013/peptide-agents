"""
The sticker artwork actually installed in static/labels — 2026-08-31.

These run against the REAL files, not fixtures. The single most valuable
assertion in this repo lives here:

    the strength printed on a sticker must equal the strength of the SKU it is
    filed under.

Mislabelling 100mg as 10mg is the failure Northline most wants engineered out,
and the labels came from an album whose filenames use their OWN SKU codes —
`5AM5`, `KLOW80`, `HGH10`, `AD5` — none of which are ours. Mapping 138 files onto
155 SKUs by hand is exactly where a 10mg sticker gets filed under the 100mg SKU
and nobody notices until a customer does.

Run:  python3 -m pytest tests/test_labels.py -q
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog                                          # noqa: E402

LABELS = catalog.LABEL_DIR
INSTALLED = sorted(p for p in LABELS.iterdir()
                   if p.suffix.lower() in catalog.LABEL_EXTENSIONS) if LABELS.is_dir() else []


def _dose(text: str) -> str | None:
    """The leading dose token in a string: 'GLP-3 RT 100mg' -> '100mg'."""
    m = re.search(r"(\d+\.?\d*)\s*(mg|ml|iu)\b", (text or "").lower())
    return f"{m.group(1)}{m.group(2)}" if m else None


def test_some_artwork_is_actually_installed():
    assert INSTALLED, "static/labels is empty — the manifest would flag every row"


@pytest.mark.parametrize("path", INSTALLED, ids=lambda p: p.stem)
def test_every_sticker_file_is_named_for_a_real_sku(path):
    """The filename IS the mapping, so a typo in a filename is a lost sticker."""
    assert catalog.get(path.stem) is not None, (
        f"static/labels/{path.name} is not a SKU we sell. Rename it to the "
        f"catalog SKU or remove it — the album uses its own codes.")


@pytest.mark.parametrize("sku", sorted(catalog.SKU_LABEL_TEXT), ids=lambda s: s)
def test_the_printed_strength_matches_the_sku_it_is_filed_under(sku):
    """THE test. A sticker saying 10mg filed under the 100mg SKU would put the
    wrong strength on a real vial, which is the whole thing we are preventing."""
    item = catalog.get(sku)
    assert item is not None, f"{sku} has label text but is not in the catalog"
    printed = _dose(catalog.SKU_LABEL_TEXT[sku])
    expected = _dose(item.spec)
    assert printed is not None, f"{sku}: no strength in {catalog.SKU_LABEL_TEXT[sku]!r}"
    assert printed == expected, (
        f"{sku} ({item.product} {item.spec}) is filed against a sticker printed "
        f"{printed!r}. Filing a sticker under the wrong strength is the exact "
        f"failure this manifest exists to prevent.")


def test_every_installed_sticker_has_recorded_wording():
    """The manifest prints the sticker's wording next to the picture; without it
    the crew gets our catalog name and the sticker says something else."""
    missing = [p.stem for p in INSTALLED
               if p.stem.upper() not in catalog.SKU_LABEL_TEXT]
    assert missing == [], f"stickers with no wording in label_text.json: {missing}"


def test_label_text_json_only_describes_stickers_we_have():
    orphans = [s for s in catalog.SKU_LABEL_TEXT if catalog.label_path(s) is None]
    assert orphans == [], f"wording recorded for SKUs with no sticker file: {orphans}"


def test_no_orphaned_artwork():
    assert catalog.labels_orphaned() == []


def test_the_gap_list_is_honest():
    """labels_missing() is what tells Jordan which stickers still need making, and
    it drives the red warning on the manifest. It must be exact."""
    missing = set(catalog.labels_missing())
    have = {s for s in catalog.BY_SKU if catalog.label_path(s) is not None}
    assert missing | have == set(catalog.BY_SKU)
    assert missing & have == set()


@pytest.mark.parametrize("path", INSTALLED, ids=lambda p: p.stem)
def test_stickers_are_legible_and_not_oversized(path):
    """Big enough to read in the spreadsheet, small enough that 130-odd of them
    do not bloat the Railway image."""
    from PIL import Image
    im = Image.open(path)
    assert im.width >= 400, f"{path.name} is {im.width}px wide — too small to read"
    assert path.stat().st_size < 400_000, f"{path.name} is {path.stat().st_size // 1024}KB"


def test_the_two_waters_are_not_confused():
    """Bac water and sterile water are different SKUs at the same price and the
    album had a 3ml bac water sticker that fuzzy matching happily paired with our
    10ml SKU. If sterile water ever gets artwork it must be its own."""
    bac = catalog.label_path("BAC10")
    stw = catalog.label_path("STW10")
    if bac and stw:
        assert bac.read_bytes() != stw.read_bytes(), \
            "BAC10 and STW10 are sharing one sticker"
    assert "3ml" not in (catalog.SKU_LABEL_TEXT.get("BAC10") or "").lower(), \
        "BAC10 is a 10ml kit — a 3ml sticker on it is a mislabel"
