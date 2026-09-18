"""QA notifier (agents/qa_notifier.py) — one email per status transition,
marked sent only when Gmail accepted it (HANDOFF §34)."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import qa_notifier as qn  # noqa: E402


def _row(status, **extra):
    f = {"qa_id": 3, "phone": "+18014007102", "status": status,
         "summary": "coin switch left customer with no address", "fix_notes": "Lily now re-sends "
         "instructions when the customer switches coin or asks for the address again."}
    f.update(extra)
    return {"id": "rec3", "fields": f}


def test_quiet_statuses_compose_nothing():
    assert qn.compose(_row("open")["fields"]) is None
    assert qn.compose(_row("in_progress")["fields"]) is None
    assert qn.compose({}) is None


def test_pr_open_tells_jordan_how_to_approve():
    subj, body = qn.compose(_row("pr_open", pr_url="https://github.com/x/y/pull/9")["fields"])
    assert subj.startswith("Fix ready to approve: QA-3")
    assert "tick `approve`" in body and "https://github.com/x/y/pull/9" in body
    assert "Original alert:" in body and "re-sends" in body


def test_self_approved_pr_reads_differently():
    subj, body = qn.compose(_row("pr_open", approve=True)["fields"])
    assert subj.startswith("Fix queued to deploy")
    assert "pre-approved" in body


def test_every_loud_status_has_a_subject():
    for st, word in (("fixed", "Deployed"), ("false_positive", "False alarm"),
                     ("needs_jordan", "Needs your decision"), ("wont_fix", "Closed"),
                     ("deploy_failed", "Deploy problem")):
        subj, body = qn.compose(_row(st, fix_commit="abcdef1234")["fields"])
        assert word in subj, st
        assert "QA-3" in subj
    assert "abcdef12" in qn.compose(_row("fixed", fix_commit="abcdef1234")["fields"])[1]


class _FakeAirtable:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def get_qa_issues_to_notify(self):
        return self.rows

    def update_qa_issue(self, rid, **fields):
        self.updates.append((rid, fields))


def test_notify_marks_only_after_a_successful_send(monkeypatch):
    fake = _FakeAirtable([_row("pr_open"), _row("open"), _row("fixed")])
    monkeypatch.setattr(qn, "airtable", fake)
    import agents.weekly_report as wr
    outcomes = iter([True, False])
    sent = []
    monkeypatch.setattr(wr, "_send_email",
                        lambda subj, body, att, **kw: sent.append(subj) or next(outcomes))
    out = qn.notify_qa_transitions()
    assert out["checked"] == 3 and out["sent"] == 1
    assert sent == ["Fix ready to approve: QA-3 (+18014007102)", "Deployed: QA-3 (+18014007102)"]
    # pr_open → marked (sent), open → marked quietly, fixed → NOT marked (send failed → retry)
    assert fake.updates == [("rec3", {"notified_status": "pr_open"}),
                            ("rec3", {"notified_status": "open"})]


def test_notify_survives_airtable_outage(monkeypatch):
    class Down:
        def get_qa_issues_to_notify(self):
            raise RuntimeError("airtable down")
    monkeypatch.setattr(qn, "airtable", Down())
    assert qn.notify_qa_transitions() == {"checked": 0, "sent": 0}
