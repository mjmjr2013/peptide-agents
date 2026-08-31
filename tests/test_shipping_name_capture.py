"""
The recipient NAME must survive, however the customer sends it — 2026-08-31.

Order NL-20260822-DDD6 shipped with no name. The customer sent, two seconds apart:

    02:33:07  "Landon Anderson"
    02:33:09  "773 E 9630 S Sandy UT 84094"

The name arrived FIRST. `_parse_address` extracted it correctly, then the handler
threw the whole parse away because THAT message had no street or city, and routed
it to Lily's chat — which quoted the name back without ever saving it. The address
message then filled everything except the name and country.

What these protect:
  1. A field we successfully parsed is never discarded for want of another field.
  2. What is still missing is read from the ORDER, not from one message and not
     from in-memory state that a deploy wipes.
  3. A junk "name" is never printed onto a parcel.
  4. A nameless order is LOUD on the manifest, not a blank space.

Run:  python3 -m pytest tests/test_shipping_name_capture.py -q
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 3. A junk name is never written ──────────────────────────────────────────

def test_a_real_name_is_accepted():
    import agents.messaging_agent as ma
    for good in ("Landon Anderson", "Lumex Health", "Mary-Jane O'Brien", "Li Wei",
                 "landon anderson"):
        assert ma._plausible_ship_name(good), f"rejected a real name: {good!r}"


def test_a_name_that_looks_like_a_common_word_is_still_a_name():
    """The first stop-list included auxiliaries and silently rejected these. A
    list that eats real names does not protect anyone — it loses the name
    somewhere else instead."""
    import agents.messaging_agent as ma
    for good in ("Will Smith", "Can Yilmaz", "Do Van Hai", "Grace Do", "May Chen",
                 "Mark Grace", "Art Ross"):
        assert ma._plausible_ship_name(good), f"rejected a real name: {good!r}"


def test_chat_is_never_mistaken_for_a_name():
    """A wrong name is printed on the parcel and nobody notices; a missing one is
    visible on the manifest and gets chased. So this errs strict."""
    import agents.messaging_agent as ma
    for bad in ("Any update?", "any update", "ok", "Thanks!", "when will it ship",
                "773 E 9630 S", "yes", "", "   ", "x" * 61,
                "please send me the tracking number as soon as you can today"):
        assert not ma._plausible_ship_name(bad), f"would have labelled a parcel {bad!r}"


# ── 1 & 2. Accumulate into the order ─────────────────────────────────────────

def _merge(order_fields, parsed, body, expecting=()):
    """Run _merge_shipping against a fake order; return (written, final fields)."""
    import agents.messaging_agent as ma
    written = {}

    def set_shipping(order_id, **kw):
        written.update(kw)
        return {}

    with mock.patch.object(ma.airtable, "get_order",
                           return_value={"id": "rec1", "fields": dict(order_fields)}), \
         mock.patch.object(ma.airtable, "set_order_shipping", side_effect=set_shipping):
        final = ma._merge_shipping("rec1", parsed, body, expecting)
    return written, final


def test_a_name_only_message_is_saved_rather_than_discarded():
    """The exact message that was lost: no street, no city, just the name."""
    written, final = _merge({}, {"ship_name": "Landon Anderson"}, "Landon Anderson")
    assert written.get("ship_name") == "Landon Anderson"
    assert final.get("ship_name") == "Landon Anderson"


def test_a_bare_name_is_taken_when_we_just_asked_for_the_name():
    written, _ = _merge({}, {}, "Landon Anderson", expecting=["ship_name"])
    assert written.get("ship_name") == "Landon Anderson"


def test_a_bare_message_is_NOT_guessed_at_when_we_did_not_ask():
    """Context, not a word list, is the real protection: if we never asked for a
    name, an unrecognised message is left alone rather than labelled with."""
    written, _ = _merge({}, {}, "Landon Anderson", expecting=[])
    assert written == {}


def test_an_existing_field_is_never_overwritten():
    """Re-sending an address must not blank or replace a name already captured."""
    written, final = _merge({"ship_name": "Landon Anderson"},
                            {"ship_name": "Someone Else", "city": "Sandy"},
                            "773 E 9630 S Sandy UT 84094")
    assert "ship_name" not in written
    assert final["ship_name"] == "Landon Anderson"
    assert written.get("city") == "Sandy"


def test_a_question_is_not_written_onto_the_label():
    """Even while we are actively waiting for the name."""
    written, _ = _merge({}, {}, "any update?", expecting=["ship_name"])
    assert written == {}
    for chat in ("thanks!", "ok", "yes", "when will it ship"):
        w, _ = _merge({}, {}, chat, expecting=["ship_name"])
        assert w == {}, f"would have labelled a parcel {chat!r}"


def test_the_full_landon_sequence_ends_with_both_name_and_address():
    """Name first, street second — the order that shipped nameless."""
    order = {}
    written1, order = _merge(order, {"ship_name": "Landon Anderson"}, "Landon Anderson")
    order = {**order, **written1}
    written2, order = _merge(order,
                             {"address_line1": "773 E 9630 S", "city": "Sandy",
                              "state_province": "UT", "postal_code": "84094"},
                             "773 E 9630 S Sandy UT 84094")
    order = {**order, **written2}
    assert order.get("ship_name") == "Landon Anderson"
    assert order.get("address_line1") == "773 E 9630 S"
    assert order.get("city") == "Sandy"


def test_missing_is_computed_from_the_order_not_the_message():
    import agents.messaging_agent as ma
    assert ma._missing_ship_fields(
        {"ship_name": "Landon Anderson", "address_line1": "773 E 9630 S",
         "city": "Sandy", "country": "USA"}) == []
    # The real record: street and city present, name and country blank.
    assert ma._missing_ship_fields(
        {"address_line1": "773 E 9630 S", "city": "Sandy"}) == ["ship_name", "country"]


def test_the_name_is_required_before_an_order_counts_as_addressed():
    import agents.messaging_agent as ma
    assert "ship_name" in ma._REQUIRED_SHIP


# ── 4. The manifest makes a missing name impossible to miss ──────────────────

def _view(name):
    return {"id": "rec1", "ref": "NL-1", "name": name, "address": "773 E 9630 S, Sandy",
            "phone": "", "kits": 2, "package_count": 1, "ordered_at": "",
            "ordered_label": "", "age_days": None, "tracking": {}, "photos": {},
            "packages": [], "tracking_complete": False, "photos_complete": False}


def test_a_nameless_order_is_flagged_not_blank():
    from core import manifest_page
    html = manifest_page._card(_view(""), manifest_page.TAB_LABEL, "tok")
    assert "NO NAME" in html
    assert "do not label" in html.lower()


def test_the_name_is_shown_for_labelling_when_present():
    from core import manifest_page
    html = manifest_page._card(_view("Landon Anderson"), manifest_page.TAB_LABEL, "tok")
    assert "Landon Anderson" in html
    assert "NO NAME" not in html
    assert 'class="shipto"' in html, "the name needs its own line to label from"


def test_the_missing_name_style_exists():
    from core import manifest_page
    assert ".shipto.missing{" in manifest_page.CSS


# ── 2b. Redeploy recovery must find a nameless order, not just an addressless one ──

def _recovery(orders):
    from core.airtable_client import AirtableClient
    c = AirtableClient.__new__(AirtableClient)
    with mock.patch.object(AirtableClient, "find_lead_by_phone",
                           return_value={"id": "lead1"}), \
         mock.patch.object(AirtableClient, "orders",
                           new_callable=mock.PropertyMock) as t:
        t.return_value.all.return_value = orders
        return c.get_paid_order_awaiting_address_for_phone("whatsapp:+1555")


def _order(oid, **f):
    f.setdefault("lead_id", ["lead1"])
    return {"id": oid, "fields": f, "createdTime": "2026-08-20T00:00:00Z"}


def test_recovery_finds_an_order_that_has_an_address_but_no_name():
    """The real failure: address_line1 was SET, so the old `address_line1=''`
    query found nothing and the customer's name reply became ordinary chat."""
    got = _recovery([_order("recA", address_line1="773 E 9630 S", city="Sandy")])
    assert got is not None and got["id"] == "recA"


def test_recovery_ignores_an_order_that_already_shipped():
    """Broadening the query must not swallow the next message of a customer whose
    OLD order happens to be missing a field."""
    assert _recovery([_order("recA", address_line1="773 E 9630 S", city="Sandy",
                             tracking_sent=True)]) is None
    assert _recovery([_order("recB", address_line1="773 E 9630 S", city="Sandy",
                             tracking_number="ZS23651014299")]) is None


def test_recovery_ignores_a_complete_order():
    assert _recovery([_order("recA", ship_name="Landon Anderson",
                             address_line1="773 E 9630 S", city="Sandy",
                             country="USA")]) is None
