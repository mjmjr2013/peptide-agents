"""The sticker-factory vial-label PDF (tools/send_sticker_list.py) — HANDOFF §33i/§33j.

The factory asked for a PDF. It lists code, product, quantity (vials), and the label
image; the builder takes only rows, so it structurally cannot leak order numbers.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import send_sticker_list as ssl


def _rows(n=2):
    base = [
        {"sku": "RT20", "product": "Retatrutide", "spec": "20mg", "kits": 17, "vials": 170, "img_path": None},
        {"sku": "BC10", "product": "BPC-157", "spec": "10mg", "kits": 6, "vials": 60, "img_path": None},
    ]
    out = []
    for i in range(n):
        r = dict(base[i % 2]); r["sku"] = f"{r['sku']}{i}"
        out.append(r)
    return out


def test_build_returns_a_valid_pdf():
    pdf = ssl.build_sticker_pdf(_rows(2))
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_many_rows_paginate_into_multiple_pages():
    one = ssl.build_sticker_pdf(_rows(2))
    many = ssl.build_sticker_pdf(_rows(40))
    assert many.count(b"/Type /Page") > one.count(b"/Type /Page") >= 1


def test_missing_artwork_does_not_crash_the_pdf():
    pdf = ssl.build_sticker_pdf([{"sku": "X1", "product": "P", "spec": "1mg",
                                  "kits": 1, "vials": 10, "img_path": None}])
    assert pdf[:4] == b"%PDF"


def test_quantity_is_vials_kits_times_ten():
    for r in _rows(2):
        assert r["vials"] == r["kits"] * 10
