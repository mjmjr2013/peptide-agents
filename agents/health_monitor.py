from __future__ import annotations
"""
Health monitor — makes billing/credit exhaustion loud instead of silent.

  • Claude canary — HOURLY. A tiny real API call through the same client the
    sales agent uses. Retried up to 4× over ~45s so a transient 529 'Overloaded'
    (common right at a restart) does NOT page ops; only a SUSTAINED failure
    (credits exhausted, key revoked, real outage) alerts, then re-alerts every 6h
    while failing and emails once on recovery. Catches the 2026-07-08 failure mode
    (Anthropic credits ran dry, prospects got silence) BEFORE a customer hits it.
  • Twilio balance — DAILY. Emails ops when the balance drops below
    TWILIO_BALANCE_ALERT_USD (default $25) so outbound WhatsApp never dies
    mid-conversation. (Auto-recharge in the Twilio console is the real fix;
    this is the backstop.)

Alerts go to REPORT_EMAIL via the existing Gmail SMTP path.
"""
import os
import time

from config import settings

_REALERT_SECS = 6 * 3600

_state = {
    "claude_failing": False,
    "last_claude_alert": 0.0,
    "last_twilio_alert": 0.0,
}


def _email(subject: str, body: str) -> None:
    try:
        from agents.weekly_report import _send_email
        _send_email(subject, body, [])
    except Exception as e:
        print(f"[Health] alert email failed: {e!r}")


def _canary_once() -> tuple[bool, str]:
    """One tiny real Claude call (~$0.001). Returns (ok, error_repr)."""
    from core.claude_client import claude
    try:
        claude.create(system="You are a health check.",
                      messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                      max_tokens=64)
        return True, ""
    except Exception as e:
        return False, repr(e)


def check_claude() -> bool:
    """Tiny real Claude call, RETRIED up to 4× over ~45s before alarming. Returns True if
    the brain is up. This tolerates transient blips — e.g. a 529 'Overloaded' (common right
    at a restart) — and only pages ops on a SUSTAINED failure (dead key / exhausted credits /
    real outage), not a one-second hiccup."""
    last_err = ""
    for attempt in range(4):  # attempts at ~0s, 15s, 30s, 45s
        ok, last_err = _canary_once()
        if ok:
            if _state["claude_failing"]:
                _email("RESOLVED: Northline agent brain is back up",
                       "The Claude canary call succeeded again. Lily is answering normally.")
            _state["claude_failing"] = False
            return True
        if attempt < 3:
            time.sleep(15)
    # All 4 attempts over ~45s failed → treat as a real, sustained outage.
    print(f"[Health] Claude canary FAILED 4× over ~45s: {last_err}")
    now = time.time()
    if not _state["claude_failing"] or now - _state["last_claude_alert"] > _REALERT_SECS:
        _state["last_claude_alert"] = now
        _email("ALERT: Northline agent brain is DOWN (Claude API failing)",
               f"The Claude canary failed 4 times over ~45s (sustained — not a transient blip):\n\n{last_err}\n\n"
               f"Customers messaging the WhatsApp number are getting the canned holding "
               f"reply, not Lily. If the error mentions credit balance, top up at "
               f"console.anthropic.com → Plans & Billing (and turn on auto-reload).")
    _state["claude_failing"] = True
    return False


def check_airtable() -> bool:
    """Daily probe of the data layer. Airtable's monthly API quota fails HARD when
    exhausted (every order/logging call 429s) — alert ops the day it happens, not
    when a customer order silently fails to record. (No usage-percent API exists,
    so this is a hard-failure alarm, not an early warning.)"""
    import requests as _rq
    try:
        r = _rq.get(f"https://api.airtable.com/v0/{settings.airtable_base_id}/Orders",
                    params={"maxRecords": 1},
                    headers={"Authorization": f"Bearer {settings.airtable_api_key}"},
                    timeout=20)
        if r.status_code == 200:
            return True
        body = r.text[:300]
        print(f"[Health] Airtable probe: HTTP {r.status_code} {body}")
        _email("ALERT: Airtable data layer failing",
               f"The daily Airtable probe got HTTP {r.status_code}:\n\n{body}\n\n"
               f"If this mentions BILLING_LIMIT, the monthly API quota is exhausted — "
               f"orders CANNOT be recorded until the plan is upgraded or usage resets. "
               f"Check usage: airtable.com → workspace settings.")
        return False
    except Exception as e:
        print(f"[Health] Airtable probe failed: {e!r}")
        return False


def check_twilio_balance() -> float | None:
    """Daily WhatsApp-money check. Returns the balance, or None on failure."""
    threshold = float(os.environ.get("TWILIO_BALANCE_ALERT_USD", "25"))
    try:
        from agents.messaging_agent import twilio_client
        bal = twilio_client.balance.fetch()
        balance = float(bal.balance)
        print(f"[Health] Twilio balance: {balance:.2f} {bal.currency}")
    except Exception as e:
        print(f"[Health] Twilio balance check failed: {e!r}")
        return None
    now = time.time()
    if balance < threshold and now - _state["last_twilio_alert"] > 20 * 3600:
        _state["last_twilio_alert"] = now
        _email(f"ALERT: Twilio balance low (${balance:.2f})",
               f"The Twilio balance is ${balance:.2f} (alert threshold ${threshold:.2f}).\n"
               f"When it hits $0, ALL WhatsApp sending and receiving stops.\n\n"
               f"Top up / enable auto-recharge: console.twilio.com → Billing → "
               f"Manage billing → Auto recharge.")
    return balance
