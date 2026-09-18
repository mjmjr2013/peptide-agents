"""One-time: create the `QA Issues` table in the Airtable base (HANDOFF §34).

The reviewer → fixer queue lives here. Idempotent: if the table exists it
prints its fields and exits, so re-running is safe. Uses the Meta API with the
same PAT the app uses (it can create tables/fields; it cannot add options to an
EXISTING single-select, which is why every option is declared up front).

    python3 tools/create_qa_table.py
"""
from __future__ import annotations
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import settings  # noqa: E402

TABLE = "QA Issues"

# Status is the state machine the whole loop runs on:
#   open           reviewer queued it, nobody has looked
#   in_progress    the fixer session claimed it (worktree + branch exist)
#   pr_open        a fix is pushed and waiting for approval (Jordan ticks `approve`
#                  or merges the PR; reviewer-only fixes self-approve)
#   fixed          merged, deployed, running commit confirmed
#   false_positive the reviewer was wrong; the reviewer rulebook was taught instead
#   needs_jordan   a decision only Jordan can make (price, policy, copy) — he answers
#                  in `jordan_notes` and the row goes back to open
#   wont_fix       looked at, deliberately left alone (notes say why)
#   deploy_failed  merged, but tests failed on main or Railway would not deploy —
#                  retried on every run; Jordan is emailed the reason
STATUSES = ["open", "in_progress", "pr_open", "fixed", "false_positive", "needs_jordan",
            "wont_fix", "deploy_failed"]

_DT = {"timeZone": "utc", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}

FIELDS = [
    {"name": "qa_id", "type": "autoNumber"},
    {"name": "phone", "type": "singleLineText"},
    {"name": "status", "type": "singleSelect",
     "options": {"choices": [{"name": s} for s in STATUSES]}},
    {"name": "severity", "type": "singleSelect",
     "options": {"choices": [{"name": s} for s in ("high", "medium", "low")]}},
    {"name": "summary", "type": "multilineText"},
    {"name": "suspected_cause", "type": "multilineText"},
    {"name": "issues", "type": "multilineText"},
    {"name": "transcript", "type": "multilineText"},
    {"name": "flagged_at", "type": "dateTime", "options": _DT},
    {"name": "last_seen_at", "type": "dateTime", "options": _DT},
    {"name": "runs", "type": "number", "options": {"precision": 0}},
    {"name": "fix_notes", "type": "multilineText"},
    {"name": "jordan_notes", "type": "multilineText"},
    {"name": "branch", "type": "singleLineText"},
    {"name": "pr_url", "type": "url"},
    {"name": "fix_commit", "type": "singleLineText"},
    {"name": "approve", "type": "checkbox", "options": {"icon": "check", "color": "greenBright"}},
    {"name": "notified_status", "type": "singleLineText"},
    {"name": "resolved_at", "type": "dateTime", "options": _DT},
]


def _req(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.airtable.com/v0/meta/bases/{settings.airtable_base_id}{path}",
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": f"Bearer {settings.airtable_api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    tables = _req("GET", "/tables")["tables"]
    for t in tables:
        if t["name"] == TABLE:
            print(f"{TABLE} already exists ({t['id']}) with fields:")
            for f in t["fields"]:
                print(f"  {f['name']:16s} {f['type']:14s} {f['id']}")
            return 0
    t = _req("POST", "/tables", {"name": TABLE, "fields": FIELDS,
                                 "description": "Transcript-reviewer → auto-fix queue (HANDOFF §34)"})
    print(f"created {TABLE} ({t['id']}):")
    for f in t["fields"]:
        print(f"  {f['name']:16s} {f['type']:14s} {f['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
