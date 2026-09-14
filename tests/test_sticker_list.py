"""The sticker-factory email (tools/send_sticker_list.py) — HANDOFF §33i.

The email lists code, product, quantity (vials), and the label image; it must NOT
name the orders (the factory doesn't need them), and the quantity is vials = kits×10.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import send_sticker_list as ssl


ROWS = [
    {"sku": "RT20", "product": "Retatrutide", "spec": "20mg", "kits": 17, "vials": 170,
     "img_path": None},
    {"sku": "BC10", "product": "BPC-157", "spec": "10mg", "kits": 6, "vials": 60,
     "img_path": None},
]


def test_email_does_not_name_the_orders():
    html = ssl._render_html(ROWS, lambda r: "")
    assert "Orders:" not in html
    assert "NL-2026" not in html


def test_rows_show_code_product_and_vial_quantity():
    html = ssl._render_html(ROWS, lambda r: "")
    assert "RT20" in html and "Retatrutide 20mg" in html
    assert ">170<" in html            # vials, not kits
    assert ">60<" in html


def test_total_is_the_sum_of_vials():
    html = ssl._render_html(ROWS, lambda r: "")
    assert ">230<" in html            # 170 + 60


def test_image_is_inlined_when_artwork_exists():
    html = ssl._render_html([{**ROWS[0], "img_path": "x"}], lambda r: "cid:abc")
    assert '<img src="cid:abc"' in html
    missing = ssl._render_html([{**ROWS[0]}], lambda r: "")
    assert "no artwork on file" in missing


def test_quantity_is_vials_kits_times_ten():
    for r in ROWS:
        assert r["vials"] == r["kits"] * 10
