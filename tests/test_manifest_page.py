"""
The manifest web page and per-package tracking — 2026-08-31.

Jordan's brief: the page itself should read like the labelling workbook —
collapsed orders that expand, packages broken out, the sticker beside each row —
and tracking should be entered there, one box per package, feeding Airtable
directly rather than into a spreadsheet nobody can act on.

What these protect:
  1. COLLAPSED BY DEFAULT. The list is a list of orders; detail is one tap away.
  2. ONE TRACKING BOX PER PACKAGE, each carrying its own package number.
  3. PARTIAL TRACKING NEVER LOOKS FINISHED. An order with 1 of 3 numbers stays on
     the list and the customer is NOT told the shipment is booked.
  4. The customer is told how many parcels to expect.

Run:  python3 -m pytest tests/test_manifest_page.py -q
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog, manifest, manifest_page                 # noqa: E402


@pytest.fixture
def stickers(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(catalog, "LABEL_DIR", tmp_path)
    for sku in ("RT10", "RT100"):
        Image.new("RGB", (660, 270), (31, 42, 68)).save(tmp_path / f"{sku}.png")
    monkeypatch.setattr(catalog, "SKU_LABEL_TEXT",
                        {"RT10": "GLP-3 RT 10mg", "RT100": "GLP-3 RT 100mg"})


def order(ref, items, tracked=False, photo=False, trk="", name="Jane Doe"):
    return {"id": "rec" + ref[-4:], "fields": {
        "order_ref": ref, "ship_name": name, "address_line1": "12 Main St",
        "city": "Provo", "state_province": "UT", "postal_code": "84604",
        "country": "USA", "ship_phone": "+15551230000",
        "tracking_sent": tracked, "vial_photo_sent": photo,
        "tracking_number": trk, "_items": items}}


def fetch(o):
    return [{"fields": i} for i in o["fields"]["_items"]]


def li(sku, product, spec, kits):
    return {"supplier_sku": sku, "product": product, "spec": spec, "kits": kits}


def view(orders):
    return manifest.build_view(orders, fetch)


# ── Tracking storage round-trips ─────────────────────────────────────────────

def test_a_single_package_stores_a_bare_number():
    """Exactly what the field held before packages existed, so old orders and
    every other consumer keep reading correctly."""
    assert manifest.format_tracking({1: "ABC123"}, 1) == "ABC123"
    assert manifest.parse_tracking("ABC123") == {1: "ABC123"}


def test_several_packages_round_trip():
    stored = manifest.format_tracking({1: "AAA", 2: "BBB", 3: "CCC"}, 3)
    assert manifest.parse_tracking(stored) == {1: "AAA", 2: "BBB", 3: "CCC"}
    assert manifest.tracking_numbers(stored) == ["AAA", "BBB", "CCC"]


def test_partial_entry_round_trips_and_keeps_its_positions():
    """Jason may enter package 2 before package 1. Position must survive."""
    stored = manifest.format_tracking({2: "BBB"}, 3)
    assert manifest.parse_tracking(stored) == {2: "BBB"}


def test_empty_tracking_is_empty():
    assert manifest.parse_tracking("") == {}
    assert manifest.parse_tracking(None) == {}
    assert manifest.format_tracking({}, 3) == ""
    assert manifest.format_tracking({1: "   "}, 1) == ""


# ── The view ─────────────────────────────────────────────────────────────────

def test_an_order_is_not_complete_until_every_package_has_a_number(stickers):
    v = view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)],
                    trk="1/2 AAA")])[0]
    assert v["package_count"] == 2
    assert v["tracking"] == {1: "AAA"}
    assert v["tracking_complete"] is False

    v2 = view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)],
                     trk="1/2 AAA | 2/2 BBB")])[0]
    assert v2["tracking_complete"] is True


def test_a_single_package_order_completes_on_one_number(stickers):
    v = view([order("NL-1", [li("RT10", "Retatrutide", "10mg x10", 1)],
                    trk="AAA")])[0]
    assert v["package_count"] == 1 and v["tracking_complete"] is True


def test_the_view_carries_the_sticker_url(stickers):
    v = view([order("NL-1", [li("RT10", "Retatrutide", "10mg x10", 1)])])[0]
    row = v["packages"][0]["rows"][0]
    assert row["label_url"] == "/static/labels/RT10.png"
    assert row["strength"] == "10mg"
    assert row["label_text"] == "GLP-3 RT 10mg"


# ── The page ─────────────────────────────────────────────────────────────────

def test_orders_are_collapsed_and_the_summary_is_just_the_headline(stickers):
    """Jordan: the SKU list should not show initially — click to expand."""
    html = manifest_page.render(
        view([order("NL-0A1B", [li("RT10", "Retatrutide", "10mg x10", 3)])]), [], "T")
    assert "<details class=\"card\">" in html
    assert "open>" not in html and "<details open" not in html, \
        "orders must start collapsed"
    summary = re.search(r"<summary>(.*?)</summary>", html, re.S).group(1)
    assert "NL-0A1B" in summary and "Jane Doe" in summary
    assert "RT10" not in summary, "the SKU list belongs inside, not in the headline"
    assert "1 package" in summary and "3 kits" in summary


def test_expanding_shows_the_sku_rows_with_stickers_and_strength(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT10", "Retatrutide", "10mg x10", 3),
                             li("RT100", "Retatrutide", "100mg x10", 2)])]), [], "T")
    assert html.count('class="sticker"') == 2
    assert "/static/labels/RT10.png" in html and "/static/labels/RT100.png" in html
    assert ">10mg<" in html and ">100mg<" in html


def test_one_tracking_box_per_package_each_naming_its_own_package(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)])]), [], "T")
    assert html.count('name="tracking"') == 2, "a two-package order needs two boxes"
    assert 'name="package" value="1"' in html
    assert 'name="package" value="2"' in html
    assert html.count('name="of" value="2"') == 2
    assert "package 1 of 2" in html and "package 2 of 2" in html


def test_a_package_that_already_has_a_number_shows_it(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)],
                    trk="1/2 AAA")]), [], "T")
    assert "Tracking: <b>AAA</b>" in html
    assert "1/2 tracked" in html, "the headline must show progress at a glance"
    # package 2 still needs one
    assert 'name="package" value="2"' in html


def test_packages_are_broken_out_with_their_weight(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)])]), [], "T")
    assert "PACKAGE 1 of 2" in html and "PACKAGE 2 of 2" in html
    assert "1.85 kg" in html


def test_water_says_why_it_is_not_split(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("BAC10", "Bacteriostatic Water", "10ml x10", 84)])]),
        [], "T")
    assert "PACKAGE 1 of 1" in html and "84 kits" in html
    assert "ships whole" in html


def test_a_missing_sticker_is_called_out_in_the_page_too(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("SM10", "Semaglutide", "10mg x10", 2)])]), [], "T")
    assert "NO STICKER ON FILE" in html
    assert 'class="sticker"' not in html


def test_the_two_tabs_are_labelled_with_their_counts(stickers):
    to_label = view([order("NL-1", [li("RT10", "Retatrutide", "10mg x10", 1)])])
    to_photo = view([order("NL-2", [li("RT10", "Retatrutide", "10mg x10", 1)],
                           tracked=True)])
    html = manifest_page.render(to_label, to_photo, "T")
    assert "Label &amp; Ship (1)" in html
    assert "Photo Before Ship (1)" in html


def test_the_photo_tab_offers_upload_and_no_tracking_boxes(stickers):
    to_photo = view([order("NL-2", [li("RT10", "Retatrutide", "10mg x10", 1)],
                           tracked=True)])
    html = manifest_page.render([], to_photo, "T")
    assert 'action="/manifest/photo"' in html
    assert 'name="tracking"' not in html, "already tracked — nothing to re-enter"


def test_empty_tabs_say_so(stickers):
    html = manifest_page.render([], [], "T")
    assert "Nothing new to label" in html
    assert "No orders waiting on a photo" in html


def test_the_token_is_carried_on_every_form(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT100", "Retatrutide", "100mg x10", 40)])]), [], "TOK")
    assert html.count('name="token" value="TOK"') == 2


def test_customer_data_is_escaped(stickers):
    html = manifest_page.render(
        view([order("NL-1", [li("RT10", "Retatrutide", "10mg x10", 1)],
                    name='<script>alert(1)</script>')]), [], "T")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_page_prints_expanded():
    """The crew may print it. Collapsed detail must not vanish on paper."""
    assert "@media print" in manifest_page.CSS
    assert "details>.body{display:block!important}" in manifest_page.CSS.replace(" ", "").replace("\n", "")


def test_expanding_an_order_needs_no_javascript():
    """A warehouse phone on a bad connection must still be able to open an order.
    <details> is native; JS only remembers which tab was last used."""
    html = manifest_page.render([], [], "T")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    assert "details" not in script.lower()
    assert "localStorage" in script and script.count("try{") >= 2, \
        "every storage access must be wrapped — it throws in private mode"


# ── The save handler and what the customer is told ───────────────────────────

def test_partial_save_does_not_mark_the_order_tracked():
    """The behaviour that keeps a half-labelled order on the list. If this
    regresses, an order with one of three numbers disappears from the manifest
    and the remaining two parcels never get labels."""
    from unittest import mock
    from core.airtable_client import AirtableClient
    client = AirtableClient.__new__(AirtableClient)
    orders = mock.MagicMock()
    with mock.patch.object(type(client), "orders",
                           new=mock.PropertyMock(return_value=orders)):
        client._advance_fulfillment = mock.MagicMock()

        client.set_order_tracking("rec1", "1/3 AAA", complete=False)
        orders.update.assert_called_once_with("rec1", {"tracking_number": "1/3 AAA"})
        client._advance_fulfillment.assert_not_called()

        client.set_order_tracking("rec1", "1/3 AAA | 2/3 BBB | 3/3 CCC")
        args = client._advance_fulfillment.call_args[0]
        assert args[1] == "labeled" and args[2]["tracking_sent"] is True


def test_the_customer_is_told_how_many_parcels_to_expect():
    """A buyer told about one parcel who receives three assumes two are lost."""
    import agents.messaging_agent as ma
    from unittest import mock
    sent = {}
    with mock.patch.object(ma, "twilio_client") as tw, \
         mock.patch.object(ma, "_whatsapp_window_open", return_value=True), \
         mock.patch.object(ma.airtable, "log_message"):
        tw.messages.create.side_effect = lambda **kw: sent.update(kw) or mock.MagicMock(sid="SM1")
        assert ma.send_tracking_to_customer("whatsapp:+1555", ["AAA", "BBB", "CCC"], "Jane")
    body = sent["body"]
    assert "3 packages" in body
    assert all(n in body for n in ("AAA", "BBB", "CCC"))
    assert "not arrive on the same day" in body


def test_a_single_parcel_message_is_unchanged():
    """One parcel is still by far the common case — that wording must not move."""
    import agents.messaging_agent as ma
    from unittest import mock
    sent = {}
    with mock.patch.object(ma, "twilio_client") as tw, \
         mock.patch.object(ma, "_whatsapp_window_open", return_value=True), \
         mock.patch.object(ma.airtable, "log_message"):
        tw.messages.create.side_effect = lambda **kw: sent.update(kw) or mock.MagicMock(sid="SM1")
        ma.send_tracking_to_customer("whatsapp:+1555", "AAA", "Jane")
    assert "your tracking number is *AAA*" in sent["body"]
    assert "packages" not in sent["body"]


def test_the_approved_template_still_takes_one_variable():
    """Outside the 24h window only the approved template can be sent, and it has
    a single placeholder. Joining the numbers into it avoids a re-approval, which
    HANDOFF §18a records as slow."""
    import agents.messaging_agent as ma, json
    from unittest import mock
    sent = {}
    with mock.patch.object(ma, "twilio_client") as tw, \
         mock.patch.object(ma, "_whatsapp_window_open", return_value=False), \
         mock.patch.object(ma.settings, "tracking_content_sid", "HX123"), \
         mock.patch.object(ma.airtable, "log_message"):
        tw.messages.create.side_effect = lambda **kw: sent.update(kw) or mock.MagicMock(sid="SM1")
        ma.send_tracking_to_customer("whatsapp:+1555", ["AAA", "BBB"], "Jane")
    assert json.loads(sent["content_variables"]) == {"1": "AAA, BBB"}


def test_no_tracking_numbers_sends_nothing():
    import agents.messaging_agent as ma
    assert ma.send_tracking_to_customer("whatsapp:+1555", []) is False
    assert ma.send_tracking_to_customer("", ["AAA"]) is False
    assert ma.send_tracking_to_customer("whatsapp:+1555", ["  "]) is False


# ── Oldest first ─────────────────────────────────────────────────────────────
# Jordan's concern: a delivery of fresh stock arrives and Jason fulfils whatever
# is in front of him, so an order that has already waited two weeks waits longer.

def dated(ref, paid=None, created=None, items=None):
    o = order(ref, items or [li("RT10", "Retatrutide", "10mg x10", 1)])
    if paid:
        o["fields"]["paid_at"] = paid
    if created:
        o["createdTime"] = created
    return o


def test_orders_are_sorted_oldest_first(stickers):
    views = view([dated("NL-NEW", paid="2026-08-30T10:00:00Z"),
                  dated("NL-OLD", paid="2026-08-05T10:00:00Z"),
                  dated("NL-MID", paid="2026-08-19T10:00:00Z")])
    assert [v["ref"] for v in views] == ["NL-OLD", "NL-MID", "NL-NEW"]


def test_the_date_comes_from_when_the_customer_paid(stickers):
    """`created_at` is in the Airtable schema but nothing ever writes it, so it is
    empty on every order. paid_at is the business event anyway."""
    v = view([dated("NL-1", paid="2026-08-12T04:11:00Z",
                    created="2026-08-01T00:00:00.000Z")])[0]
    assert v["ordered_at"] == "2026-08-12" and v["date_source"] == "paid"


def test_airtable_created_time_is_the_fallback(stickers):
    v = view([dated("NL-1", created="2026-08-15T09:00:00.000Z")])[0]
    assert v["ordered_at"] == "2026-08-15" and v["date_source"] == "created"


def test_the_order_ref_date_is_the_last_resort(stickers):
    v = view([order("NL-20260820-AAAA", [li("RT10", "Retatrutide", "10mg x10", 1)])])[0]
    assert v["ordered_at"] == "2026-08-20" and v["date_source"] == "ref"


def test_an_order_with_no_date_sorts_last_not_first(stickers):
    """Every order here is paid, so a missing date is an oddity — it must not be
    able to jump the queue on the strength of being unparseable."""
    views = view([order("ODD", [li("RT10", "Retatrutide", "10mg x10", 1)]),
                  dated("NL-OLD", paid="2026-08-05T10:00:00Z")])
    assert [v["ref"] for v in views] == ["NL-OLD", "ODD"]
    assert views[1]["ordered_at"] == ""


def test_the_page_shows_the_date_and_how_long_it_has_waited(stickers, monkeypatch):
    from datetime import date
    import core.manifest as m
    real = m.days_waiting
    monkeypatch.setattr(m, "days_waiting",
                        lambda iso, today=None: real(iso, date(2026, 8, 31)))
    html = manifest_page.render(
        view([dated("NL-1", paid="2026-08-12T00:00:00Z")]), [], "T")
    assert "12 Aug" in html and "waiting 19 days" in html


def test_an_old_order_is_visually_flagged(stickers, monkeypatch):
    from datetime import date
    import core.manifest as m
    real = m.days_waiting
    monkeypatch.setattr(m, "days_waiting",
                        lambda iso, today=None: real(iso, date(2026, 8, 31)))
    def badge(paid):
        html = manifest_page.render(view([dated("X", paid=paid)]), [], "T")
        # look at the badge itself — the stylesheet mentions every class name
        return re.search(r'<span class="(age[^"]*)"', html).group(1)

    assert badge("2026-08-30T00:00:00Z") == "age"          # yesterday
    assert badge("2026-08-22T00:00:00Z") == "age warn"     # 9 days
    assert badge("2026-08-05T00:00:00Z") == "age late"     # 26 days


def test_a_missing_date_is_badged_rather_than_left_blank(stickers):
    html = manifest_page.render(
        view([order("ODD", [li("RT10", "Retatrutide", "10mg x10", 1)])]), [], "T")
    assert "date unknown" in html


def test_the_page_tells_the_crew_to_work_oldest_first(stickers):
    html = manifest_page.render([], [], "T")
    assert "Oldest orders are at the top" in html


def test_dates_are_written_unambiguously(stickers):
    """12/08 and 08/12 mean opposite things depending on where you are, and the
    crew reading this is in China."""
    from core.manifest import pretty_date
    assert pretty_date("2026-08-12") == "12 Aug"
    assert pretty_date("") == ""
    assert pretty_date("nonsense") == "nonsense"


def test_newest_first_sort_no_longer_keys_on_an_empty_field():
    """core/airtable_client sorted on `created_at` to pick the most recent of a
    customer's awaiting orders — a field nothing writes, so it picked whatever
    Airtable listed first. Payment recovery and order superseding both use it."""
    from core.airtable_client import AirtableClient
    key = AirtableClient._newest_first
    older = {"createdTime": "2026-08-01T00:00:00Z", "fields": {}}
    newer = {"createdTime": "2026-08-20T00:00:00Z", "fields": {}}
    assert sorted([older, newer], key=key, reverse=True)[0] is newer
    paid = {"createdTime": "2026-08-01T00:00:00Z", "fields": {"paid_at": "2026-08-25"}}
    assert key(paid) == "2026-08-25"
    assert key({"fields": {}}) == ""
