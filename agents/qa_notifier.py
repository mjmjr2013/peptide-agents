from __future__ import annotations
"""
QA notifier — closes the loop back to Jordan (HANDOFF §34).

The fixer session runs on Jordan's Mac, which has no Gmail credentials (they are
Railway-only, §33i). So the fixer only WRITES its outcome to the `QA Issues` row,
and this hourly beat on Railway emails Jordan whenever a row's `status` differs
from `notified_status` — one email per transition, retried every hour until it
sends, no new secrets anywhere. Airtable stays the single channel.

Transitions Jordan hears about:
  pr_open         a fix is ready — tick `approve` in Airtable or press Merge
  fixed           merged, deployed, running commit confirmed
  false_positive  the alert was wrong; the reviewer was taught
  needs_jordan    a decision only he can make — answer in `jordan_notes`
  wont_fix        looked at, left alone, with the reason
  deploy_failed   merged but could not ship (tests red on main / Railway) — retried
`open` and `in_progress` are silent (the reviewer already emailed the alert).
"""
from datetime import datetime, timezone

from core.airtable_client import airtable

_QUIET = {"open", "in_progress"}


def _label(f: dict) -> str:
    return f"QA-{f.get('qa_id', '?')} ({f.get('phone', '?')})"


def compose(f: dict) -> tuple[str, str] | None:
    """(subject, body) for the row's current status, or None if it is a quiet
    status. Pure — tested without Airtable."""
    status = f.get("status", "")
    if status in _QUIET or not status:
        return None
    label = _label(f)
    notes = (f.get("fix_notes") or "").strip() or "(no notes written)"
    summary = (f.get("summary") or "").strip()
    head = f"Original alert: {summary}\n\n" if summary else ""
    if status == "pr_open":
        pr = f.get("pr_url") or "(no PR link recorded)"
        auto = bool(f.get("approve"))
        subject = f"Fix ready to approve: {label}" if not auto else f"Fix queued to deploy: {label}"
        body = (f"{head}What was wrong and what the fix does:\n{notes}\n\n"
                f"Pull request: {pr}\n\n")
        if auto:
            body += ("This change only touches the QA reviewer itself (nothing a customer "
                     "sees), so it is pre-approved and deploys on the next fixer run.\n")
        else:
            body += ("TO SHIP IT: open Airtable → QA Issues → this row and tick `approve` "
                     "(or press Merge on the pull request). The fixer merges it, re-runs the "
                     "full test suite on main, deploys to Railway, confirms the running "
                     "commit, and emails you again when it is live.\n"
                     "TO STOP IT: write why in `jordan_notes` and set status to wont_fix.\n")
        return subject, body
    if status == "fixed":
        commit = f.get("fix_commit") or "?"
        return (f"Deployed: {label}",
                f"{head}The fix is live on Railway (commit {commit[:8]}).\n\n{notes}\n")
    if status == "false_positive":
        return (f"False alarm: {label}",
                f"{head}The fixer looked at the transcript and the code and found no real "
                f"problem:\n{notes}\n\nThe reviewer has been taught so it does not flag this "
                f"again (that change ships through its own pr_open row if one was needed).\n")
    if status == "needs_jordan":
        return (f"Needs your decision: {label}",
                f"{head}The fixer could not resolve this without you:\n{notes}\n\n"
                f"TO ANSWER: open Airtable → QA Issues → this row, write your answer in "
                f"`jordan_notes` and set status back to `open`. The next fixer run picks it up "
                f"with your answer in hand.\n")
    if status == "wont_fix":
        return (f"Closed without change: {label}", f"{head}{notes}\n")
    if status == "deploy_failed":
        return (f"Deploy problem: {label}",
                f"{head}The fix was approved and merged but could not be shipped. The reason "
                f"is at the bottom of these notes; the fixer retries on its next run and "
                f"emails again when it is live.\n\n{notes}\n")
    return (f"QA status {status}: {label}", f"{head}{notes}\n")


def notify_qa_transitions() -> dict:
    """Email every row whose status moved since the last email; mark it sent
    only when the send succeeded, so a Gmail hiccup retries next hour."""
    try:
        rows = airtable.get_qa_issues_to_notify()
    except Exception as e:
        print(f"[QA/Notify] fetch failed: {e!r}")
        return {"checked": 0, "sent": 0}
    sent = 0
    for r in rows:
        f = r["fields"]
        msg = compose(f)
        if msg is None:
            # Quiet transition: record it so the row stops matching, no email.
            try:
                airtable.update_qa_issue(r["id"], notified_status=f.get("status", ""))
            except Exception as e:
                print(f"[QA/Notify] mark failed for {_label(f)}: {e!r}")
            continue
        subject, body = msg
        try:
            from agents.weekly_report import _send_email
            ok = _send_email(subject, body, [])
        except Exception as e:
            print(f"[QA/Notify] email failed for {_label(f)}: {e!r}")
            ok = False
        if ok:
            sent += 1
            try:
                airtable.update_qa_issue(r["id"], notified_status=f.get("status", ""))
            except Exception as e:
                print(f"[QA/Notify] mark failed for {_label(f)}: {e!r}")
            print(f"[QA/Notify] emailed {subject}")
    return {"checked": len(rows), "sent": sent,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
