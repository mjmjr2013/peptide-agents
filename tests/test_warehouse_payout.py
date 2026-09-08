"""The nightly payout — orchestration, not arithmetic.

Every test here is about MONEY MOVING WRONG, because that is the only failure
this feature has that cannot be undone. The fee math is covered in
test_warehouse_fees.py; what is covered here is the ordering of claim, send and
record, and what happens when each of the three fails.

Airtable and Tron are both faked. That is the point: these tests must be able to
simulate a broadcast that half-succeeds, which no real network will do on demand.
"""
import time

import pytest

from config import settings
from core.airtable_client import AirtableClient
from core import warehouse_fees, tron_payout
import agents.warehouse_payout as wp


# A real, checksum-valid Tron address (the USDT contract's own) so address
# validation is genuinely exercised rather than stubbed out.
GOOD_ADDR = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# The two fixture orders under $12/box + $15.90/kg gross (350 g tare per box):
#   NL-A  1 kit  RT100   1 box    425 g   12.00 +  6.76 =  18.76
#   NL-B 40 kits RT100   2 boxes 3700 g   24.00 + 58.83 =  82.83
A_ONLY = 18.76
NIGHT = 101.59

# Captured before any fixture stubs it out, so the send-layer tests below
# exercise the real refusals rather than the fake.
_ORIGINAL_SEND = tron_payout.send


class FakeAirtable:
    """Just enough Airtable to test the claim/send/record ordering."""

    def __init__(self, orders):
        self.records = {o["id"]: o for o in orders}
        self.calls = []
        self.claim_fails_for = set()
        self.get_fails_for = set()
        self.get_fails_after_claim = False
        self.release_fails_for = set()
        self.confirm_fails_for = set()

    CLAIM_PREFIX = "claim:"

    # -- reads
    def payout_fields_missing(self):
        return []

    # Cutoff filtering, claim/verify/sweep semantics are the PRODUCTION methods,
    # bound to this fake's storage. A fake that reimplements them tests a
    # different program than the one that runs — the first draft of this file did
    # exactly that and its start-date test passed against logic production had
    # already stopped using.
    parse_fee_cutoff = AirtableClient.parse_fee_cutoff
    _as_date = AirtableClient._as_date
    CLAIM_STALE_SECONDS = AirtableClient.CLAIM_STALE_SECONDS
    verify_claims = AirtableClient.verify_claims
    claim_warehouse_fee = AirtableClient.claim_warehouse_fee

    def get_order(self, oid):
        # `after_claim` lets a test fail the VERIFY read only, leaving the
        # claim-time read working — the two are different failures and the code
        # must tell them apart.
        if oid in self.get_fails_for and (
                not self.get_fails_after_claim
                or self.records[oid]["fields"].get("warehouse_fee_paid")):
            raise RuntimeError("airtable 429")
        return self.records[oid]

    class _Rows(list):
        """Stands in for pyairtable's Table.all(formula=...) result."""

    def get_stuck_warehouse_claims(self, stale_seconds=None):
        cutoff = time.time() - (self.CLAIM_STALE_SECONDS if stale_seconds is None
                                else stale_seconds)
        out = []
        for r in self.records.values():
            f = r["fields"]
            if not f.get("warehouse_fee_paid"):
                continue
            tx = str(f.get("warehouse_fee_tx") or "")
            if tx in ("", "pending"):
                out.append(r)
            elif tx.startswith(self.CLAIM_PREFIX):
                try:
                    at = float(tx.removeprefix(self.CLAIM_PREFIX).split(":")[0])
                except (ValueError, IndexError):
                    at = 0.0
                if at <= cutoff:
                    out.append(r)
        return out

    def get_orders_awaiting_warehouse_fee(self, since_iso=""):
        out = []
        for o in self.records.values():
            if o["fields"].get("warehouse_fee_paid"):
                continue
            if since_iso and (o["fields"].get("paid_at", "")[:10] < since_iso[:10]):
                continue
            out.append(o)
        return out

    def get_items_for_order(self, order):
        return [{"fields": it} for it in order["_items"]]

    # -- writes
    class _Orders:
        """The two Table methods AirtableClient.claim_warehouse_fee touches."""

        def __init__(self, fake):
            self.fake = fake

        def update(self, oid, fields):
            self.fake.calls.append(("claim", oid))
            if oid in self.fake.claim_fails_for:
                raise RuntimeError("airtable 422")
            self.fake.records[oid]["fields"].update(fields)
            return self.fake.records[oid]

    @property
    def orders(self):
        return self._Orders(self)

    def confirm_warehouse_fee(self, oid, tx):
        self.calls.append(("confirm", oid))
        if oid in self.confirm_fails_for:
            raise RuntimeError("airtable 500")
        self.records[oid]["fields"]["warehouse_fee_tx"] = tx

    def release_warehouse_fee(self, oid):
        self.calls.append(("release", oid))
        if oid in self.release_fails_for:
            raise RuntimeError("airtable 500")
        self.records[oid]["fields"].update(
            {"warehouse_fee_paid": False, "warehouse_fee_tx": "",
             "warehouse_fee_usd": None, "warehouse_boxes": None})


def _order(oid, ref, items, paid_at="2026-09-10"):
    return {"id": oid, "createdTime": paid_at + "T00:00:00.000Z",
            "fields": {"order_ref": ref, "payment_status": "paid", "paid_at": paid_at},
            "_items": items}


ONE_BOX = [{"supplier_sku": "RT100", "product": "Retatrutide", "spec": "100mg", "kits": 1}]
TWO_BOX = [{"supplier_sku": "RT100", "product": "Retatrutide", "spec": "100mg", "kits": 40}]
BAD_LINE = [{"supplier_sku": "", "product": "Blorbotide", "spec": "50mg", "kits": 1}]


@pytest.fixture
def env(monkeypatch):
    """A correctly configured, dry-run system with two payable orders."""
    monkeypatch.setattr(settings, "warehouse_fee_per_box_usd", 12.0, raising=False)
    monkeypatch.setattr(settings, "warehouse_fee_per_kg_usd", 15.90, raising=False)
    monkeypatch.setattr(settings, "warehouse_fee_start_date", "2026-09-05", raising=False)
    monkeypatch.setattr(settings, "jason_tron_address", GOOD_ADDR, raising=False)
    monkeypatch.setattr(settings, "payout_private_key", "ab" * 32, raising=False)
    monkeypatch.setattr(settings, "payout_dry_run", False, raising=False)
    monkeypatch.setattr(settings, "payout_asset", "USDT", raising=False)
    monkeypatch.setattr(settings, "payout_max_usd", 1500.0, raising=False)
    monkeypatch.setattr(settings, "operator_emails", ["ops@example.com"], raising=False)
    monkeypatch.setattr(settings, "payout_statement_emails", ["jason@example.com"],
                        raising=False)
    monkeypatch.setattr(settings, "manifest_cc", [], raising=False)
    # No wallet exists in a test, so preflight's on-chain reads must not run.
    monkeypatch.setattr(tron_payout, "preflight",
                        lambda amount_usd=None: {"ok": True, "problems": []})
    monkeypatch.setattr(wp, "CLAIM_SETTLE_SECONDS", 0.0)

    sends = []

    def fake_send(amount, memo=""):
        sends.append(amount)
        return {"tx_hash": f"TX{len(sends)}", "amount": amount, "asset": "USDT",
                "to": GOOD_ADDR, "dry_run": False, "ts": 0}
    monkeypatch.setattr(tron_payout, "send", fake_send)

    fake = FakeAirtable([_order("r1", "NL-A", ONE_BOX), _order("r2", "NL-B", TWO_BOX)])
    monkeypatch.setattr(wp, "airtable", fake)
    alerts = []
    monkeypatch.setattr(wp, "_alert", lambda s, b: alerts.append((s, b)))
    statements = []
    monkeypatch.setattr(wp, "_send_statement",
                        lambda batch, result, label: statements.append((batch, result)) or True)
    fake.alerts, fake.statements, fake.sends = alerts, statements, sends
    return fake


# ── The happy night ──────────────────────────────────────────────────────────

def test_pays_the_sum_of_the_boxes_once(env):
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is True
    assert res["orders"] == 2
    assert res["boxes"] == 3          # 1 + 2
    assert res["total_usd"] == NIGHT


def test_a_second_run_the_same_night_pays_nothing(env):
    """The single most important test in this file. A Railway restart, a manual
    re-run, or a scheduler that fires twice must not pay Jason twice."""
    first = wp.run_daily_warehouse_payout()
    second = wp.run_daily_warehouse_payout()
    assert first["total_usd"] == NIGHT
    assert second["sent"] is False
    assert second["total_usd"] == 0.0


def test_every_paid_order_carries_the_transaction_hash(env):
    wp.run_daily_warehouse_payout()
    for rec in env.records.values():
        assert rec["fields"]["warehouse_fee_paid"] is True
        assert rec["fields"]["warehouse_fee_tx"] not in ("", "pending")


def test_the_claim_happens_before_the_send(env, monkeypatch):
    """Ordering is the whole safety argument: claim-then-send underpays on
    failure (recoverable), send-then-claim double-pays (not)."""
    seen = []
    monkeypatch.setattr(tron_payout, "send",
                        lambda amt, memo="": seen.append(list(env.calls)) or
                        {"tx_hash": "T1", "amount": amt, "asset": "USDT",
                         "to": GOOD_ADDR, "dry_run": False, "ts": 0})
    wp.run_daily_warehouse_payout()
    assert all(c[0] == "claim" for c in seen[0])
    assert len([c for c in seen[0] if c[0] == "claim"]) == 2


# ── Nothing to do ────────────────────────────────────────────────────────────

def test_no_orders_sends_nothing_and_alerts_nobody(env):
    env.records.clear()
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False and res["orders"] == 0
    assert env.alerts == []


def test_orders_paid_before_the_start_date_are_never_swept_in(env):
    """Without this the first live run would pay for the entire back catalogue."""
    env.records["old"] = _order("old", "NL-OLD", TWO_BOX, paid_at="2026-08-01")
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == NIGHT
    assert env.records["old"]["fields"].get("warehouse_fee_paid") is not True


def test_refuses_to_run_at_all_without_a_start_date(env, monkeypatch):
    monkeypatch.setattr(settings, "warehouse_fee_start_date", "", raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["ran"] is False
    assert any("START_DATE" in p for p in res["blocked"])


def test_refuses_to_run_when_airtable_lacks_the_ledger_fields(env, monkeypatch):
    """Degrading here would lose the record of who has been paid, and the next
    night would pay them all over again. So it fails closed instead."""
    monkeypatch.setattr(env, "payout_fields_missing", lambda: ["warehouse_fee_paid"])
    res = wp.run_daily_warehouse_payout()
    assert res["ran"] is False
    assert any("warehouse_fee_paid" in p for p in res["blocked"])
    assert env.calls == []


def test_a_blocked_run_alerts_only_when_money_is_actually_waiting(env, monkeypatch):
    monkeypatch.setattr(settings, "warehouse_fee_start_date", "", raising=False)
    env.records.clear()
    wp.run_daily_warehouse_payout()
    assert env.alerts == []          # half-configured and idle => no nightly spam


# ── Unpriceable orders ───────────────────────────────────────────────────────

def test_an_unpriceable_order_is_excluded_and_left_unclaimed(env):
    env.records["r3"] = _order("r3", "NL-C", BAD_LINE)
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == NIGHT                       # not 48
    assert res["skipped"] == 1
    assert env.records["r3"]["fields"].get("warehouse_fee_paid") is not True


def test_an_unpriceable_order_alerts_a_human_every_night(env):
    """He packed it. He is not being paid for it. Somebody has to know."""
    env.records["r3"] = _order("r3", "NL-C", BAD_LINE)
    wp.run_daily_warehouse_payout()
    assert any("left out" in s.lower() for s, _ in env.alerts)


def test_a_night_of_only_unpriceable_orders_sends_nothing(env):
    env.records = {"r3": _order("r3", "NL-C", BAD_LINE)}
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False and res["total_usd"] == 0.0


# ── The send fails ───────────────────────────────────────────────────────────

def test_a_failed_send_returns_every_order_to_the_queue(env, monkeypatch):
    real_send = tron_payout.send
    failing = {"on": True}

    def boom(amount, memo=""):
        if failing["on"]:
            raise tron_payout.PayoutError("insufficient balance")
        return real_send(amount, memo=memo)
    monkeypatch.setattr(tron_payout, "send", boom)

    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False
    assert all(r["fields"].get("warehouse_fee_paid") is not True
               for r in env.records.values())
    # ...and the next night pays them, in full, exactly once
    failing["on"] = False
    assert wp.run_daily_warehouse_payout()["total_usd"] == NIGHT


def test_a_failed_send_that_cannot_be_rolled_back_names_the_stuck_orders(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send",
                        lambda a, memo="": (_ for _ in ()).throw(tron_payout.PayoutError("nope")))
    env.release_fails_for = {"r2"}
    wp.run_daily_warehouse_payout()
    body = "\n".join(b for _, b in env.alerts)
    assert "NL-B" in body and "untick" in body.lower()


def test_an_uncertain_broadcast_is_never_rolled_back(env, monkeypatch):
    """The money may already be gone. Rolling back here would re-send it
    tomorrow, which is the one outcome no amount of apologising fixes."""
    def uncertain(amount, memo=""):
        raise tron_payout.UncertainBroadcast("confirmation unreadable", tx_hash="TXMAYBE")
    monkeypatch.setattr(tron_payout, "send", uncertain)
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] == "uncertain"
    assert all(r["fields"]["warehouse_fee_paid"] is True for r in env.records.values())
    assert all(r["fields"]["warehouse_fee_tx"] == "TXMAYBE" for r in env.records.values())
    assert any("UNKNOWN" in s or "🚨" in s for s, _ in env.alerts)
    # and the next run finds nothing left to pay — no second transfer
    monkeypatch.setattr(tron_payout, "send",
                        lambda a, memo="": pytest.fail("re-sent an uncertain payment"))
    assert wp.run_daily_warehouse_payout()["total_usd"] == 0.0


# ── The claim fails ──────────────────────────────────────────────────────────

def test_an_order_that_could_not_be_claimed_is_not_paid_for(env):
    """If the tick did not land, paying now means paying again tomorrow."""
    env.claim_fails_for = {"r2"}
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == A_ONLY      # r1 only
    assert any("could not be marked" in s for s, _ in env.alerts)


def test_all_claims_failing_sends_nothing(env):
    env.claim_fails_for = {"r1", "r2"}
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False and res["total_usd"] == 0.0


# ── The record-back fails ────────────────────────────────────────────────────

def test_a_missing_tx_hash_does_not_cause_a_second_payment(env):
    env.confirm_fails_for = {"r1"}
    wp.run_daily_warehouse_payout()
    # The hash never landed, so the order still carries this run's claim marker.
    assert env.records["r1"]["fields"]["warehouse_fee_tx"].startswith("claim:")
    assert env.records["r1"]["fields"]["warehouse_fee_paid"] is True
    assert wp.run_daily_warehouse_payout()["total_usd"] == 0.0
    assert any("claim marker" in s.lower() for s, _ in env.alerts)


def test_an_order_left_with_a_claim_marker_is_swept_and_reported(env):
    """The one thing that reads back the 'marked paid but never sent' state. A
    container killed between claim and broadcast leaves an order that no query
    would ever return again — this is how anyone finds out."""
    env.confirm_fails_for = {"r1"}
    wp.run_daily_warehouse_payout()
    # Not reported while it could still belong to a live run...
    env.alerts.clear()
    wp.run_daily_warehouse_payout()
    assert not any("no transaction" in s.lower() for s, _ in env.alerts)
    # ...but reported once it is old enough to be abandoned.
    f = env.records["r1"]["fields"]
    f["warehouse_fee_tx"] = "claim:1:" + f["warehouse_fee_tx"].split(":")[-1]
    env.alerts.clear()
    wp.run_daily_warehouse_payout()
    assert any("no transaction" in s.lower() for s, _ in env.alerts)


# ── Two schedulers racing (Railway rolling deploy) ───────────────────────────

def test_a_run_that_loses_the_token_race_pays_nothing(env, monkeypatch):
    """Both runs claim (Airtable has no compare-and-swap). The read-back after the
    settle delay is what decides: a run whose token was overwritten drops the
    order rather than paying it. This covers the interleave the scheme HANDLES —
    see the next test for the one it does not."""
    real_verify = env.verify_claims

    def steal(ids, token):
        for oid in ("r1", "r2"):        # a second scheduler re-claims mid-settle
            env.records[oid]["fields"]["warehouse_fee_tx"] = "claim:99:beefbeef"
        return real_verify(ids, token)
    monkeypatch.setattr(env, "verify_claims", steal)

    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False
    assert env.sends == []


def test_a_claimed_order_is_re_read_before_being_claimed_again(env):
    """The fresh per-record read at claim time is what makes the residual race
    narrow: a direct record fetch is far less stale than a formula-filtered list,
    so the second run usually sees the first run's claim and stands down."""
    wp.run_daily_warehouse_payout()                 # run A pays, records TX1
    env.records["r1"]["fields"]["warehouse_fee_paid"] = False   # B's stale queue
    env.records["r2"]["fields"]["warehouse_fee_paid"] = False
    res = wp.run_daily_warehouse_payout()           # run B
    assert res["sent"] is False                     # both refused: real hash present
    assert env.sends == [NIGHT]                      # ...and only one transfer ever


def test_a_real_transaction_hash_is_never_overwritten_by_a_new_claim(env):
    """If a double payment ever does happen, it must not erase the evidence of
    the first one — that is what makes it findable afterwards."""
    wp.run_daily_warehouse_payout()
    first = env.records["r1"]["fields"]["warehouse_fee_tx"]
    env.records["r1"]["fields"]["warehouse_fee_paid"] = False
    wp.run_daily_warehouse_payout()
    assert env.records["r1"]["fields"]["warehouse_fee_tx"] == first


def test_a_hand_run_cannot_overlap_the_scheduler_in_this_process(env, monkeypatch):
    """`python3 -m agents.warehouse_payout` while the nightly tick is in flight."""
    seen = []

    def reentrant(amount, memo=""):
        seen.append(wp.run_daily_warehouse_payout())     # the "second run"
        return {"tx_hash": "TX1", "amount": amount, "asset": "USDT",
                "to": GOOD_ADDR, "dry_run": False, "ts": 0}
    monkeypatch.setattr(tron_payout, "send", reentrant)
    wp.run_daily_warehouse_payout()
    assert seen[0]["sent"] is False
    assert seen[0]["skipped_reason"] == "already running"


# ── The stuck-claim sweep must not cause the thing it reports ────────────────

def test_a_fresh_claim_is_not_reported_as_abandoned(env):
    """A claim marker may belong to a run that is alive RIGHT NOW, mid-broadcast.
    Reporting it as abandoned tells the operator to untick it, and the next run
    pays it again — the sweep would cause a double payment."""
    env.records["r1"]["fields"].update(
        {"warehouse_fee_paid": True,
         "warehouse_fee_tx": f"claim:{int(time.time())}:abc123"})
    assert env.get_stuck_warehouse_claims() == []


def test_an_hour_old_claim_is_reported(env):
    env.records["r1"]["fields"].update(
        {"warehouse_fee_paid": True,
         "warehouse_fee_tx": f"claim:{int(time.time()) - 7200}:abc123"})
    stuck = env.get_stuck_warehouse_claims()
    assert [r["id"] for r in stuck] == ["r1"]


def test_a_verify_read_failure_is_reported_not_treated_as_a_lost_race(env):
    """An Airtable 429 during verification would otherwise look identical to
    another run owning the order, and silently underpay with nobody told."""
    env.get_fails_for = {"r2"}
    env.get_fails_after_claim = True
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == A_ONLY
    assert any("could not be re-read" in s.lower() for s, _ in env.alerts)


def test_a_read_failure_at_claim_time_just_rolls_the_order_over(env):
    env.get_fails_for = {"r2"}
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == A_ONLY
    assert env.records["r2"]["fields"].get("warehouse_fee_paid") is not True
    assert any("could not be marked" in s for s, _ in env.alerts)


# ── A malformed Order Item must cost one order, not the system ───────────────

def test_an_order_item_with_no_fields_key_does_not_stop_the_payout(env, monkeypatch):
    bad = _order("r3", "NL-BAD", ONE_BOX)
    env.records["r3"] = bad
    real_items = env.get_items_for_order
    monkeypatch.setattr(env, "get_items_for_order",
                        lambda o: [{}] if o["id"] == "r3" else real_items(o))
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is True
    assert res["total_usd"] == NIGHT      # the two good orders still went out


def test_kits_stored_as_text_refuses_that_order_and_pays_the_rest(env):
    env.records["r3"] = _order("r3", "NL-TXT", [
        {"supplier_sku": "RT100", "product": "Retatrutide", "spec": "100mg",
         "kits": "two and a half"}])
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == NIGHT
    assert res["skipped"] == 1
    assert env.records["r3"]["fields"].get("warehouse_fee_paid") is not True


def test_a_lookup_style_sku_field_is_unwrapped_not_crashed_on(env):
    env.records["r3"] = _order("r3", "NL-LOOK", [
        {"supplier_sku": ["RT100"], "product": "Retatrutide", "spec": "100mg", "kits": 1}])
    res = wp.run_daily_warehouse_payout()
    assert res["total_usd"] == round(NIGHT + A_ONLY, 2)   # NL-LOOK priced as one box


def test_the_job_never_raises_even_when_airtable_explodes(env, monkeypatch):
    """It shares a thread with the daily manifest, the health canary and the
    payment watcher. An exception escaping here takes all of them down."""
    def boom(*a, **k):
        raise RuntimeError("airtable is on fire")
    monkeypatch.setattr(env, "get_items_for_order", boom)
    res = wp.run_daily_warehouse_payout()          # returns, does not raise
    assert res["sent"] is False
    assert env.sends == []
    assert any("left out" in s.lower() for s, _ in env.alerts)


def test_a_read_failure_on_the_orders_table_does_not_raise(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("airtable is on fire")
    monkeypatch.setattr(env, "get_orders_awaiting_warehouse_fee", boom)
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False and "error" in res


# ── The start-date guard ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["2026-9-5", "09/05/2026", "Sept 5 2026", "2026-13-01"])
def test_a_misformatted_cutoff_blocks_the_run_instead_of_sorting_wrongly(env, monkeypatch, bad):
    """'2026-9-5' sorts ABOVE every real timestamp and pays nobody forever;
    '09/05/2026' sorts BELOW and sweeps in the whole back catalogue. Both look
    like a perfectly reasonable date to a human, so neither may be accepted."""
    monkeypatch.setattr(settings, "warehouse_fee_start_date", bad, raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["ran"] is False
    assert any("YYYY-MM-DD" in p for p in res["blocked"])
    assert env.sends == []


# ── Nobody to tell means nothing moves ───────────────────────────────────────

def test_it_refuses_to_pay_when_no_alert_address_is_configured(env, monkeypatch):
    monkeypatch.setattr(settings, "operator_emails", [], raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["ran"] is False and env.sends == []


def test_it_refuses_to_pay_when_jason_would_get_no_statement(env, monkeypatch):
    monkeypatch.setattr(settings, "payout_statement_emails", [], raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["ran"] is False and env.sends == []


# ── TRX is refused, not approximated ─────────────────────────────────────────

def test_trx_is_refused_because_it_would_pay_dollars_as_a_coin_count(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    monkeypatch.setattr(settings, "payout_asset", "TRX", raising=False)
    with pytest.raises(tron_payout.PayoutError, match="not supported"):
        tron_payout.send(480.0)


# ── The statement ────────────────────────────────────────────────────────────

def test_the_statement_covers_exactly_what_was_paid(env):
    env.claim_fails_for = {"r2"}
    wp.run_daily_warehouse_payout()
    batch, _ = env.statements[0]
    assert batch["total_usd"] == A_ONLY
    assert [r["ref"] for r in batch["rows"]] == ["NL-A"]


# ── The send layer's own refusals ────────────────────────────────────────────

def _real_send(monkeypatch):
    import importlib
    importlib.reload(tron_payout)


def test_send_refuses_a_non_positive_amount(env, monkeypatch):
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    monkeypatch.undo() if False else None
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    for bad in (0, -5, float("nan")):
        with pytest.raises(tron_payout.PayoutError):
            tron_payout.send(bad)


def test_send_refuses_an_address_that_fails_the_checksum(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    monkeypatch.setattr(settings, "jason_tron_address", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6X",
                        raising=False)
    with pytest.raises(tron_payout.PayoutError, match="checksum|valid"):
        tron_payout.send(12.0)


def test_send_refuses_with_no_address_configured(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    monkeypatch.setattr(settings, "jason_tron_address", "", raising=False)
    with pytest.raises(tron_payout.PayoutError):
        tron_payout.send(12.0)


def test_the_circuit_breaker_stops_an_absurd_night(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    monkeypatch.setattr(settings, "payout_max_usd", 100.0, raising=False)
    with pytest.raises(tron_payout.PayoutError, match="ceiling"):
        tron_payout.send(5000.0)


def test_the_circuit_breaker_can_be_switched_off(env, monkeypatch):
    monkeypatch.setattr(tron_payout, "send", _ORIGINAL_SEND)
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    monkeypatch.setattr(settings, "payout_max_usd", 0.0, raising=False)
    assert tron_payout.send(5000.0)["amount"] == 5000.0     # dry run


def test_dry_run_claims_nothing_so_the_backlog_survives(env, monkeypatch):
    """PAYOUT_DRY_RUN is the DEFAULT. A dry run that ticked orders as paid would
    swallow the entire go-live backlog behind a fake transaction hash, and no
    query would ever return those orders again."""
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["dry_run"] is True
    assert res["sent"] is False
    assert res["total_usd"] == NIGHT               # it still tells you the number
    assert env.calls == []                        # ...and wrote nothing
    assert env.sends == []
    # the orders are still there tomorrow
    monkeypatch.setattr(settings, "payout_dry_run", False, raising=False)
    assert wp.run_daily_warehouse_payout()["total_usd"] == NIGHT


def test_a_missing_key_behaves_like_a_dry_run_not_a_silent_payment(env, monkeypatch):
    monkeypatch.setattr(settings, "payout_private_key", "", raising=False)
    res = wp.run_daily_warehouse_payout()
    assert res["sent"] is False and env.calls == []


def test_is_enabled_needs_a_key_and_dry_run_off(env, monkeypatch):
    assert tron_payout.is_enabled() is True
    monkeypatch.setattr(settings, "payout_dry_run", True, raising=False)
    assert tron_payout.is_enabled() is False
    monkeypatch.setattr(settings, "payout_dry_run", False, raising=False)
    monkeypatch.setattr(settings, "payout_private_key", "", raising=False)
    assert tron_payout.is_enabled() is False
