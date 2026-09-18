from __future__ import annotations
"""
Transcript reviewer — a QA supervisor for the sales agent.

Every REVIEW_INTERVAL_HOURS (default 6, from the in-process scheduler), it pulls
the Airtable Messages transcript, finds conversations with recent activity, and
has Claude review each thread against the business rules: did every customer
message get a reply, did Lily stay in persona, did she hold the fixed price, were
payment and orders handled correctly. Threads with problems are emailed to
REPORT_EMAIL with severity ratings; clean runs send nothing (log only).

Since HANDOFF §34 every flagged thread is ALSO queued in Airtable `QA Issues`
(one row per phone while it stays open), which the fixer loop on Jordan's Mac
(tools/qa_loop.py) works through: diagnose, fix on a branch, test, open a PR,
write the outcome back. The email is the alert; the row is the work item.

The rulebook below MUST track what Lily is actually told in messaging_agent.py.
When it drifted (it still described 5/10/15% discount authority two weeks after
§31 removed negotiation) three of four alerts in a row were false positives —
Daniel's at-cost line and the US shipping schedule. A judge with stale rules
feeds the fixer garbage, so the fixer is allowed to edit this file too.

Cost: one small Claude call per ACTIVE thread per run — pennies a day at
current volume. Warehouse/operator numbers are excluded.

Manual run: python -m agents.transcript_reviewer [lookback_hours]
"""
import json
import os
from datetime import datetime, timedelta, timezone

from config import settings
from core.airtable_client import airtable


def _at_cost_note(phone: str) -> str:
    """Tell the judge when a phone holds an internal at-cost code (core.deals).
    Without this, Daniel's USSTOCK26 orders read as '85% below list' and were
    flagged HIGH twice in one day (2026-09-15)."""
    from core.deals import AT_COST_CODES, phone_allowed
    holders = [spec for spec in AT_COST_CODES.values()
               if spec.get("phones") and phone_allowed(spec, phone)]
    if not holders:
        return ""
    labels = "; ".join(s.get("label", s["code"]) for s in holders)
    return (f"\nTHIS PHONE IS AN INTERNAL LINE ({labels}). It holds an at-cost price code: once "
            "the code appears in the thread, every line is priced at our internal COST — far "
            "below the list tables — and that is CORRECT, not underbilling. Shipping is still "
            "charged at the flat rates with no free threshold. Do not flag low prices or the "
            "code itself. Everything else (replies, persona, payment flow) is judged normally.\n")


def _build_review_prompt(phone: str = "") -> str:
    from core.pricing import get_catalog_text, WAREHOUSE_CHINA, WAREHOUSE_US
    from core.shipping import STANDARD_USD, EXPEDITED_USD, FREE_OVER_USD, US_FLAT_USD
    return f"""You are the QA supervisor for "Lily", an AI WhatsApp sales agent for Northline Group
(research peptides). You review one customer conversation transcript and flag REAL problems
a human manager must know about. Be pragmatic — do not nitpick wording or tone variation.
Your verdict feeds an automated fix pipeline: a false alarm costs real engineering time, so
only flag what you can point to in the transcript, and quote it.

BUSINESS RULES LILY MUST FOLLOW:
- Every customer message gets a reply. Silence / ignoring a customer is the worst failure.
  A customer left with no way forward — they asked how to pay, or for the address or
  amount, and never got it — is HIGH.
- She is a warm human saleswoman named Lily. She must NEVER admit to being an AI, bot,
  agent, or assistant. Frequent "dear", emojis and light ESL flavor are intentional and
  CORRECT — not issues.
- PRICES ARE FIXED. There is NO negotiation, NO discount, NO "best I can do", and NO
  escalation to a boss. Lily has zero discount authority and must not move a price by a
  dollar. Holding the price and pointing at the volume tier is CORRECT behaviour; she may
  say the breakpoints out loud. Quoting a price that is not in the tables below (or moving
  one) is HIGH.
  * CHINA warehouse: price per kit (10 vials) depends on the TOTAL kits on the whole order,
    added across products: 1-24 standard, 25-99 reseller, 100+ trading. Whole dollars.
  * US warehouse: one FLAT price per kit at any quantity. It is also the only warehouse
    that sells single vials.
- SHIPPING is added on top of the product total.
  * From CHINA: ${STANDARD_USD} standard (4 weeks or less), FREE standard when the product
    total is over ${FREE_OVER_USD}, or ${EXPEDITED_USD} expedited (10 days or less).
  * From the US: ${US_FLAT_USD} flat, overnight — no choice to offer and NO free threshold.
- PAYMENT: BTC or USDT (USDT on the Ethereum / ERC-20 network) only. Card, bank, PayPal,
  Venmo, Zelle, Cash App are politely declined — suggesting they buy BTC/USDT on an app is
  fine. Once items, shipping and coin are agreed the SYSTEM sends the exact amount (with
  cents — deliberate, it identifies the payment) and then the wallet address as its own
  message. Lily never invents an address or amount.
  * If the customer asks for the address or amount AGAIN, or wants to SWITCH coin, they
    must receive fresh, correct payment instructions for that coin. Answering that with
    "let me verify with the finance department" / "finance doesn't see it yet" is a HIGH
    failure — the customer has not paid, there is nothing to verify.
  * After the customer says they HAVE paid: "wait one moment while I verify with the
    finance department", then about a minute later "finance has confirmed" or "finance
    doesn't see it just yet, message me shortly" is the correct on-chain check — NOT a
    stall. Varied "still checking with finance, dear" lines while a check is in flight are
    normal.
  * After payment is confirmed she collects the shipping details (name, street, city,
    state, postal code, country). We ship after payment.
- Only products the customer actually asked for go on the order. Multi-product orders must
  be carried forward complete — dropping a line is a real issue.
- "prices" / "price list" / "catalog" → she sends the price-sheet FILE with no text. That
  shows in the transcript as a bare media line or as nothing from her — not an issue.
- NORMAL (not issues): a canned "it is very late here / very busy today, I reply soon" line
  (that is the daily message-cap guardrail); the greeting appearing once; proof videos or
  photos sent on request (logged as "[sent proof video/photo: ...]"); plain non-persona
  system texts like tracking numbers or vial photos; a customer going idle, walking away,
  or declining because they do not use crypto, when Lily made no mistake.
{_at_cost_note(phone)}
CATALOG — CHINA warehouse (list prices, whole dollars):
{get_catalog_text(WAREHOUSE_CHINA)}

CATALOG — US warehouse:
{get_catalog_text(WAREHOUSE_US)}

Review the transcript and output STRICT JSON only — no prose outside the JSON:
{{"ok": true/false, "summary": "<one sentence on the thread>", "suspected_cause": "<one line: which rule or flow step failed, or empty>", "issues": [{{"severity": "high"/"medium"/"low", "issue": "<specific problem, quoting the relevant message>"}}]}}
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


def format_transcript(msgs: list[dict]) -> str:
    """The exact text the judge saw — also what goes on the QA row, so the fixer
    reads the same evidence the verdict was based on."""
    lines = []
    for m in msgs:
        who = "CUSTOMER" if m["direction"] == "inbound" else "LILY"
        lines.append(f"[{m['sent_at']}] {who}: {m['body']}")
    return "\n".join(lines)


def review_thread(phone: str, msgs: list[dict]) -> dict:
    """One Claude QA pass over one conversation. Returns the parsed verdict."""
    from core.claude_client import claude
    transcript = format_transcript(msgs)
    response = claude.create(
        system=_build_review_prompt(phone),
        messages=[{"role": "user", "content": f"Transcript for {phone}:\n\n{transcript}"}],
        max_tokens=1500,
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    verdict = _parse_json(text) or {"ok": True, "summary": "(reviewer output unparseable)",
                                    "issues": []}
    verdict["phone"] = phone
    verdict["transcript"] = transcript
    return verdict


def top_severity(verdict: dict) -> str:
    sevs = {i.get("severity") for i in verdict.get("issues", [])}
    for s in ("high", "medium", "low"):
        if s in sevs:
            return s
    return "low"


def queue_flagged(flagged: list[dict]) -> list[dict]:
    """Write each flagged verdict to `QA Issues`. Returns the verdicts that were
    NEW rows (an open row for the same phone is refreshed silently instead —
    the 6-hourly pass re-flags the same thread until it is fixed, and Jordan
    should hear about a problem once). Never raises: the queue is a bonus on
    top of the email, not a reason to lose it."""
    new = []
    for v in flagged:
        try:
            rec, created = airtable.queue_qa_issue(
                v["phone"], top_severity(v), v.get("summary", ""), v.get("issues", []),
                v.get("transcript", ""), v.get("suspected_cause", ""))
            v["qa_id"] = rec["fields"].get("qa_id")
            v["queued"] = "new" if created else "updated"
            if created:
                new.append(v)
        except Exception as e:
            print(f"[Reviewer] queue write failed for {v['phone']}: {e!r}")
            v["queued"] = "failed"
            new.append(v)  # unqueued → still alert, so nothing is silently lost
    return new


def run_transcript_review(hours: float | None = None) -> dict:
    """Review all recently-active customer threads; queue + email problems."""
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
    to_alert = queue_flagged(flagged) if flagged else []
    for v in flagged:
        print(f"[Reviewer] queued {v['phone']} as QA-{v.get('qa_id', '?')} ({v.get('queued')})")
    if to_alert:
        lines = []
        for v in to_alert:
            tag = f"QA-{v['qa_id']}" if v.get("qa_id") else "(not queued)"
            lines.append(f"◆ {v['phone']} — {tag} — {v.get('summary','')}")
            for i in v.get("issues", []):
                lines.append(f"    [{i.get('severity','?').upper()}] {i.get('issue','')}")
            lines.append("")
        body = (f"The transcript reviewer checked {len(active)} conversation(s) from the last "
                f"{hours:g}h and flagged {len(to_alert)}:\n\n" + "\n".join(lines) +
                "\nWhat happens next: each one is queued in Airtable → QA Issues. The fixer "
                "session on Jordan's Mac picks it up within a few hours, works out the cause, "
                "and you get a follow-up email — either a fix ready to approve, a note that "
                "it was a false alarm, or a question only you can answer.\n"
                "\nFull transcripts: Airtable → Messages table (group by phone).")
        try:
            from agents.weekly_report import _send_email
            n_high = sum(1 for v in to_alert for i in v.get("issues", [])
                         if i.get("severity") == "high")
            subject = (f"Agent QA: {len(to_alert)} conversation(s) need attention"
                       + (f" ({n_high} HIGH)" if n_high else ""))
            _send_email(subject, body, [])
        except Exception as e:
            print(f"[Reviewer] report email failed: {e!r}")
    return {"threads": len(active), "flagged": len(flagged), "new": len(to_alert)}


if __name__ == "__main__":
    import sys
    hrs = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    print(json.dumps(run_transcript_review(hrs), indent=2))
