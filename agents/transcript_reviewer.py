from __future__ import annotations
"""
Transcript reviewer — a QA supervisor for the sales agent.

Every REVIEW_INTERVAL_HOURS (default 6, from the in-process scheduler), it pulls
the Airtable Messages transcript, finds conversations with recent activity, and
has Claude review each thread against the business rules: did every customer
message get a reply, did Lily stay in persona, were prices/discounts within
authority, were orders handled correctly. Threads with problems are emailed to
REPORT_EMAIL with severity ratings; clean runs send nothing (log only).

Cost: one small Claude call per ACTIVE thread per run — pennies a day at
current volume. Warehouse/operator numbers are excluded.

Manual run: python -m agents.transcript_reviewer [lookback_hours]
"""
import json
import os
from datetime import datetime, timedelta, timezone

from config import settings
from core.airtable_client import airtable


def _build_review_prompt() -> str:
    from core.pricing import get_catalog_text
    return f"""You are the QA supervisor for "Lily", an AI WhatsApp sales agent for Northline Group
(research peptides). You review one customer conversation transcript and flag REAL problems
a human manager must know about. Be pragmatic — do not nitpick wording or tone variation.

BUSINESS RULES LILY MUST FOLLOW:
- Every customer message gets a reply. Silence / ignoring a customer is the worst failure.
- She is a warm human saleswoman named Lily. She must NEVER admit to being an AI, bot, agent,
  or assistant. Frequent "dear" and light ESL flavor are intentional and CORRECT — not issues.
- Pricing comes from the catalog below. Discount authority: under 25 kits max 5% off list,
  25-49 kits max 10%, 50+ kits max 15%. Quoting below that (or inventing products/prices not
  in the catalog) is a HIGH severity issue. Shipping: $95 standard / free over $1000 /
  $235 expedited.
- Orders over 100 kits where the buyer demands MORE than her discount authority should be
  escalated to the boss ("let me check with my boss"), not discounted beyond the cap.
- She must only put products on an order that the customer actually asked for.
- After an order is agreed, payment instructions (exact crypto amount + wallet) are sent by
  the system; after payment is verified she collects the shipping address.
- NORMAL (not issues): varied "checking on your payment, dear" reassurances while waiting;
  a canned "it is very late here / very busy today, I reply soon" line (that is the daily
  message-cap guardrail); the greeting appearing once; proof videos/photos sent on request
  (logged as "[sent proof video/photo: ...]"); plain non-persona system texts like tracking
  numbers or vial photos.

CATALOG (list prices):
{get_catalog_text()}

Review the transcript and output STRICT JSON only — no prose outside the JSON:
{{"ok": true/false, "summary": "<one sentence on the thread>", "issues": [{{"severity": "high"/"medium"/"low", "issue": "<specific problem, quoting the relevant message>"}}]}}
"ok" is false only if there is at least one high or medium issue. An abandoned/idle
negotiation with no agent mistake is ok=true (mention it in summary; low issue at most)."""


def _parse_json(text: str) -> dict | None:
    """First balanced JSON object in text (reviewer output)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def _excluded(phone: str) -> bool:
    """Warehouse / operator / supplier numbers are not customer threads."""
    import re
    d = re.sub(r"\D", "", phone or "")[-10:]
    others = [settings.warehouse_whatsapp, settings.supplier_whatsapp] + settings.operator_numbers
    return any(d and re.sub(r"\D", "", n or "")[-10:] == d for n in others)


def _threads_with_recent_activity(hours: float) -> dict[str, list[dict]]:
    """phone -> chronological [{'direction','body','sent_at'}] for threads with a
    message inside the lookback window (full recent thread context included)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = airtable.messages.all()
    threads: dict[str, list[dict]] = {}
    for r in rows:
        f = r["fields"]
        phone = f.get("phone", "")
        if not phone or _excluded(phone):
            continue
        threads.setdefault(phone, []).append(
            {"direction": f.get("direction", ""), "body": f.get("body", ""),
             "sent_at": f.get("sent_at", "")})
    active = {}
    for phone, msgs in threads.items():
        msgs.sort(key=lambda m: m["sent_at"])
        if msgs and msgs[-1]["sent_at"] >= cutoff:
            active[phone] = msgs[-60:]  # bounded context per thread
    return active


def review_thread(phone: str, msgs: list[dict]) -> dict:
    """One Claude QA pass over one conversation. Returns the parsed verdict."""
    from core.claude_client import claude
    lines = []
    for m in msgs:
        who = "CUSTOMER" if m["direction"] == "inbound" else "LILY"
        lines.append(f"[{m['sent_at']}] {who}: {m['body']}")
    transcript = "\n".join(lines)
    response = claude.create(
        system=_build_review_prompt(),
        messages=[{"role": "user", "content": f"Transcript for {phone}:\n\n{transcript}"}],
        max_tokens=1500,
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    verdict = _parse_json(text) or {"ok": True, "summary": "(reviewer output unparseable)",
                                    "issues": []}
    verdict["phone"] = phone
    return verdict


def run_transcript_review(hours: float | None = None) -> dict:
    """Review all recently-active customer threads; email a report if problems found."""
    hours = hours or float(os.environ.get("REVIEW_INTERVAL_HOURS", "6"))
    try:
        active = _threads_with_recent_activity(hours)
    except Exception as e:
        print(f"[Reviewer] transcript fetch failed: {e!r}")
        return {"threads": 0, "flagged": 0}
    print(f"[Reviewer] {len(active)} thread(s) active in the last {hours:g}h")
    flagged = []
    for phone, msgs in active.items():
        try:
            v = review_thread(phone, msgs)
        except Exception as e:
            print(f"[Reviewer] review of {phone} failed: {e!r}")
            continue
        sev = ", ".join(i["severity"] for i in v.get("issues", [])) or "none"
        print(f"[Reviewer] {phone}: ok={v.get('ok')} issues={sev} — {v.get('summary','')}")
        if not v.get("ok", True):
            flagged.append(v)
    if flagged:
        lines = []
        for v in flagged:
            lines.append(f"◆ {v['phone']} — {v.get('summary','')}")
            for i in v.get("issues", []):
                lines.append(f"    [{i.get('severity','?').upper()}] {i.get('issue','')}")
            lines.append("")
        body = (f"The transcript reviewer checked {len(active)} conversation(s) from the last "
                f"{hours:g}h and flagged {len(flagged)}:\n\n" + "\n".join(lines) +
                "\nFull transcripts: Airtable → Messages table (group by phone).")
        try:
            from agents.weekly_report import _send_email
            n_high = sum(1 for v in flagged for i in v.get("issues", [])
                         if i.get("severity") == "high")
            subject = (f"Agent QA: {len(flagged)} conversation(s) need attention"
                       + (f" ({n_high} HIGH)" if n_high else ""))
            _send_email(subject, body, [])
        except Exception as e:
            print(f"[Reviewer] report email failed: {e!r}")
    return {"threads": len(active), "flagged": len(flagged)}


if __name__ == "__main__":
    import sys
    hrs = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    print(json.dumps(run_transcript_review(hrs), indent=2))
