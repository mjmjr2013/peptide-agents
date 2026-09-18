"""The transcript reviewer's rulebook and its QA-queue hand-off (HANDOFF §34).

The rulebook must track what Lily is actually told. When it drifted (still
describing discount authority two weeks after §31 removed negotiation) three of
four alerts in a row were false positives. These tests pin the rules that
produced those false alarms, and the queue behaviour that keeps one open row per
phone so Jordan hears about a problem once.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import transcript_reviewer as tr  # noqa: E402

DANIEL = "+14806366814"      # holds USSTOCK26 (core.deals) — an internal line
CUSTOMER = "+18014007102"


# ── Rulebook ─────────────────────────────────────────────────────────────────

def test_rulebook_has_no_negotiation_left_in_it():
    p = tr._build_review_prompt(CUSTOMER)
    for stale in ("max 5% off", "max 10%", "max 15%", "Discount authority:", "check with my boss",
                  "escalated to the boss"):
        assert stale not in p, stale
    assert "PRICES ARE FIXED" in p
    assert "NO\n  escalation to a boss" in p


def test_rulebook_carries_both_warehouses_and_both_shipping_schedules():
    from core.shipping import STANDARD_USD, EXPEDITED_USD, FREE_OVER_USD, US_FLAT_USD
    p = tr._build_review_prompt(CUSTOMER)
    assert "CATALOG — CHINA warehouse" in p and "CATALOG — US warehouse" in p
    assert f"${STANDARD_USD} standard" in p and f"${EXPEDITED_USD} expedited" in p
    assert f"over ${FREE_OVER_USD}" in p
    assert f"${US_FLAT_USD} flat" in p and "NO free threshold" in p


def test_rulebook_names_the_coin_switch_failure_as_high():
    """The 2026-09-17 incident: customer switched USDT→BTC, asked for the
    address, and got 'checking with finance'. The judge must call that HIGH
    and must NOT flag the genuine post-payment verification beat."""
    p = tr._build_review_prompt(CUSTOMER)
    assert "SWITCH coin" in p
    assert "there is nothing to verify" in p
    assert "NOT a\n    stall" in p or "NOT a stall" in p.replace("\n    ", " ")


def test_at_cost_line_is_annotated_and_customers_are_not():
    assert "INTERNAL LINE" in tr._at_cost_note(DANIEL)
    assert tr._at_cost_note(CUSTOMER) == ""
    assert "INTERNAL LINE" in tr._build_review_prompt(DANIEL)
    assert "INTERNAL LINE" not in tr._build_review_prompt(CUSTOMER)


def test_verdict_json_asks_for_a_suspected_cause():
    assert '"suspected_cause"' in tr._build_review_prompt(CUSTOMER)


# ── Verdict helpers ──────────────────────────────────────────────────────────

def test_parse_json_tolerates_prose_and_keeps_new_field():
    v = tr._parse_json('Sure:\n{"ok": false, "summary": "s", "suspected_cause": "coin switch", '
                       '"issues": [{"severity": "high", "issue": "x"}]}\nthanks')
    assert v and v["suspected_cause"] == "coin switch"
    assert tr._parse_json("no json here") is None


def test_top_severity():
    assert tr.top_severity({"issues": [{"severity": "low"}, {"severity": "high"}]}) == "high"
    assert tr.top_severity({"issues": [{"severity": "medium"}]}) == "medium"
    assert tr.top_severity({"issues": []}) == "low"


def test_format_transcript_labels_sides():
    t = tr.format_transcript([{"direction": "inbound", "body": "hi", "sent_at": "2026-09-17T12:00"},
                              {"direction": "outbound", "body": "hello dear", "sent_at": "2026-09-17T12:01"}])
    assert t.splitlines() == ["[2026-09-17T12:00] CUSTOMER: hi", "[2026-09-17T12:01] LILY: hello dear"]


# ── Queue hand-off ───────────────────────────────────────────────────────────

class _FakeAirtable:
    def __init__(self, created_flags):
        self.created_flags = list(created_flags)
        self.calls = []

    def queue_qa_issue(self, phone, severity, summary, issues, transcript, suspected_cause="", now_iso=None):
        self.calls.append((phone, severity, summary, issues, transcript, suspected_cause))
        created = self.created_flags.pop(0)
        if created == "raise":
            raise RuntimeError("airtable down")
        return {"id": "recX", "fields": {"qa_id": 7}}, created


def _verdict(phone, sev="high"):
    return {"phone": phone, "ok": False, "summary": f"problem on {phone}",
            "issues": [{"severity": sev, "issue": "x"}], "transcript": "T", "suspected_cause": "c"}


def test_queue_flagged_returns_only_new_rows(monkeypatch):
    fake = _FakeAirtable([True, False])
    monkeypatch.setattr(tr, "airtable", fake)
    flagged = [_verdict("+1"), _verdict("+2")]
    new = tr.queue_flagged(flagged)
    assert [v["phone"] for v in new] == ["+1"]
    assert [v["queued"] for v in flagged] == ["new", "updated"]
    assert all(v["qa_id"] == 7 for v in flagged)
    assert fake.calls[0][:2] == ("+1", "high")
    assert fake.calls[0][5] == "c"


def test_queue_failure_still_alerts(monkeypatch):
    """The queue is a bonus on top of the email — never a reason to lose it."""
    fake = _FakeAirtable(["raise"])
    monkeypatch.setattr(tr, "airtable", fake)
    new = tr.queue_flagged([_verdict("+1")])
    assert len(new) == 1 and new[0]["queued"] == "failed"


def test_run_review_emails_once_per_new_problem(monkeypatch):
    fake = _FakeAirtable([True, False])
    monkeypatch.setattr(tr, "airtable", fake)
    monkeypatch.setattr(tr, "_threads_with_recent_activity",
                        lambda hours: {"+1": [], "+2": [], "+3": []})

    def fake_review(phone, msgs):
        if phone == "+3":
            return {"phone": phone, "ok": True, "summary": "fine", "issues": []}
        return _verdict(phone)
    monkeypatch.setattr(tr, "review_thread", fake_review)
    sent = []
    import agents.weekly_report as wr
    monkeypatch.setattr(wr, "_send_email", lambda subj, body, att, **kw: sent.append((subj, body)) or True)
    out = tr.run_transcript_review(6)
    assert out == {"threads": 3, "flagged": 2, "new": 1}
    assert len(sent) == 1
    subj, body = sent[0]
    assert "1 conversation(s)" in subj and "1 HIGH" in subj
    assert "QA-7" in body and "+1" in body and "+2" not in body
    assert "QA Issues" in body


def test_run_review_sends_nothing_when_clean(monkeypatch):
    monkeypatch.setattr(tr, "_threads_with_recent_activity", lambda hours: {"+1": []})
    monkeypatch.setattr(tr, "review_thread",
                        lambda p, m: {"phone": p, "ok": True, "summary": "ok", "issues": []})
    fake = _FakeAirtable([])
    monkeypatch.setattr(tr, "airtable", fake)
    sent = []
    import agents.weekly_report as wr
    monkeypatch.setattr(wr, "_send_email", lambda *a, **k: sent.append(a) or True)
    assert tr.run_transcript_review(6) == {"threads": 1, "flagged": 0, "new": 0}
    assert not sent and not fake.calls
