"""Tron float watch (agents/treasury.py) + deBridge sizing (core/debridge.py) — HANDOFF §35.

Nothing here touches a network: the quote is a fake, the wallet reads are
monkeypatched, and the email is captured. The point is the arithmetic (never
under-cover the shortfall), the fail-loud paths, and the wording Jordan reads.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import debridge  # noqa: E402
from agents import treasury  # noqa: E402

WALLET = "TNTZSTHJeLqvQs9dGvkg433hUph9tt7E7V"


def _fake_quote(usd_in: float) -> dict:
    """~$9.30 prepended operating cost, ~0.3% taker margin — shaped like the live API."""
    amt_in = usd_in + 9.3
    out = usd_in * 0.997
    return {"usd_in": round(amt_in, 2), "usd_out": round(out, 2),
            "usd_out_low": round(out - 9.3, 2), "cost_usd": round(amt_in - out, 2),
            "fix_fee_eth": 0.001, "eta_s": 3}


# ── deBridge sizing ──────────────────────────────────────────────────────────

def test_round_up():
    assert debridge.round_up(87.38) == 100.0
    assert debridge.round_up(100.0) == 100.0
    assert debridge.round_up(100.01) == 150.0


def test_size_topup_never_under_covers_and_respects_minimum():
    p = debridge.size_topup(87.38, min_usd=200, quote_fn=_fake_quote)
    assert p["send_usd"] == 200.0 and p["covers"] and p["usd_out_low"] >= 87.38
    # 195 short: 200 sent arrives ~190 conservatively → bumps to 250
    p = debridge.size_topup(195, min_usd=200, quote_fn=_fake_quote)
    assert p["send_usd"] == 250.0 and p["covers"]
    # gives up honestly after `attempts`
    p = debridge.size_topup(10_000, min_usd=200, quote_fn=_fake_quote, attempts=1)
    assert not p["covers"] and p["send_usd"] == 10_000.0 and p["usd_in"] == 10_009.3


def test_size_topup_propagates_quote_failure():
    def boom(x):
        raise debridge.QuoteError("down")
    with pytest.raises(debridge.QuoteError):
        debridge.size_topup(50, quote_fn=boom)


def test_app_link_pins_both_chain_ids_and_the_recipient():
    url = debridge.app_link(250, WALLET)
    assert url.startswith("https://app.debridge.com/deswap?")
    assert "inputChain=1" in url and "outputChain=728126428" in url
    assert "inputCurrency=0xdAC17F958D2ee523a2206206994597C13D831ec7" in url
    assert "outputCurrency=TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" in url
    assert f"address={WALLET}" in url and "amount=250" in url


def test_quote_parses_the_api_shape(monkeypatch):
    payload = {"estimation": {"srcChainTokenIn": {"amount": "209046468"},
                              "dstChainTokenOut": {"amount": "199426218",
                                                   "recommendedAmount": "199426218"}},
               "fixFee": "1000000000000000", "order": {"approximateFulfillmentDelay": 3}}
    monkeypatch.setattr(debridge, "_get", lambda url, timeout=40: payload)
    q = debridge.quote(200)
    assert q["usd_in"] == 209.05 and q["usd_out"] == 199.43
    assert q["usd_out_low"] == pytest.approx(199.43 - 9.05, abs=0.02)
    assert q["cost_usd"] == pytest.approx(9.62, abs=0.01)
    assert q["fix_fee_eth"] == 0.001 and q["eta_s"] == 3
    monkeypatch.setattr(debridge, "_get", lambda url, timeout=40: {"errorMessage": "nope"})
    with pytest.raises(debridge.QuoteError):
        debridge.quote(200)


# ── Assessment ───────────────────────────────────────────────────────────────

def _wallet(usdt=150.02, trx=371.45):
    return {"address": WALLET, "usdt": usdt, "trx": trx}


def _owed(total=0.0, orders=0, boxes=0):
    return {"total_usd": total, "orders": orders, "boxes": boxes}


def test_assess():
    v = treasury.assess(_wallet(), _owed(), 150, 30)
    assert v == {"need": 150.0, "shortfall": 0.0, "trx_low": False, "ok": True}
    v = treasury.assess(_wallet(usdt=150.02), _owed(87.40, 3, 7), 150, 30)
    assert v["need"] == 237.4 and v["shortfall"] == 87.38 and not v["ok"]
    v = treasury.assess(_wallet(trx=12), _owed(), 150, 30)
    assert v["shortfall"] == 0.0 and v["trx_low"] and not v["ok"]


# ── The email ────────────────────────────────────────────────────────────────

def test_compose_short_with_plan_and_phantom_warnings():
    wallet = _wallet(usdt=150.02)
    owed = _owed(87.40, 3, 7)
    verdict = treasury.assess(wallet, owed, 150, 30)
    plan = debridge.size_topup(verdict["shortfall"], min_usd=200, quote_fn=_fake_quote)
    phantom = {"address": "0xD1A3", "usdt": 5.63, "eth": 0.00287}
    subj, body = treasury.compose("pre", wallet, owed, verdict, plan, phantom, 150, 30)
    assert subj == "Tron float short: bridge $200.00 before tonight's payout"
    assert "150.02 USDT" in body and "$87.40" in body and "3 order(s), 7 box(es)" in body
    assert "Shortfall:            $87.38" in body
    assert debridge.app_link(200, WALLET) in body
    assert "type 200 as the amount you pay" in body
    assert "⚠ Not enough USDT there" in body and "at least $200.00 USDT" in body
    assert "⚠ Low ETH" in body
    assert "nothing is lost" in body
    assert "TRX" not in subj


def test_compose_post_label_and_trx_only():
    wallet = _wallet(usdt=400, trx=12)
    owed = _owed()
    verdict = treasury.assess(wallet, owed, 150, 30)
    subj, body = treasury.compose("post", wallet, owed, verdict, None, None, 150, 30)
    assert subj.startswith("Tron wallet needs TRX")
    assert "Send ~50 TRX to " + WALLET in body
    assert "WHAT TO DO" not in body  # no USDT shortfall → no bridge instructions


def test_compose_without_a_quote_still_gives_a_link():
    wallet = _wallet(usdt=10)
    owed = _owed(40)
    verdict = treasury.assess(wallet, owed, 150, 30)
    subj, body = treasury.compose("pre", wallet, owed, verdict, None, None, 150, 30,
                                  plan_error="QuoteError('down')")
    assert subj == "Tron float short by $180.00 before tonight's payout"
    assert "Could not get a live quote" in body and "app.debridge.com" in body


# ── check_float wiring ───────────────────────────────────────────────────────

@pytest.fixture
def wired(monkeypatch):
    sent = []
    monkeypatch.setattr(treasury, "_alert", lambda s, b: sent.append((s, b)) or True)
    monkeypatch.setattr(treasury.tron_payout, "read_balances", lambda address=None: _wallet())
    import agents.warehouse_payout as wp
    monkeypatch.setattr(wp, "owed_now", lambda: _owed())
    real_size = debridge.size_topup
    monkeypatch.setattr(treasury.debridge, "size_topup",
                        lambda short, min_usd=200: real_size(short, min_usd, quote_fn=_fake_quote))
    monkeypatch.setattr(treasury, "phantom_balances",
                        lambda address=None: {"address": "0x", "usdt": 900, "eth": 0.05})
    monkeypatch.setattr(treasury.settings, "tron_float_reserve_usd", 150.0)
    monkeypatch.setattr(treasury.settings, "tron_trx_floor", 30.0)
    monkeypatch.setattr(treasury.settings, "topup_min_usd", 200.0)
    return sent


def test_healthy_float_sends_nothing(wired):
    out = treasury.check_float("pre")
    assert out["ok"] and not out["emailed"] and not wired


def test_short_float_emails_once_with_a_plan(wired, monkeypatch):
    import agents.warehouse_payout as wp
    monkeypatch.setattr(wp, "owed_now", lambda: _owed(87.40, 3, 7))
    out = treasury.check_float("pre")
    assert not out["ok"] and out["emailed"] and out["plan"]["send_usd"] == 200.0
    assert len(wired) == 1 and wired[0][0].startswith("Tron float short: bridge $200.00")
    assert "Phantom right now: 900.00 USDT" in wired[0][1]
    assert "⚠" not in wired[0][1]  # Phantom is funded → no warnings


def test_unreadable_wallet_is_reported_not_assumed(wired, monkeypatch):
    def boom(address=None):
        raise RuntimeError("trongrid 429")
    monkeypatch.setattr(treasury.tron_payout, "read_balances", boom)
    out = treasury.check_float("pre")
    assert not out["ok"] and out["emailed"] and "trongrid 429" in out["error"]
    assert wired[0][0] == "Tron float check FAILED — wallet unreadable"


def test_send_false_is_a_preview(wired, monkeypatch):
    import agents.warehouse_payout as wp
    monkeypatch.setattr(wp, "owed_now", lambda: _owed(500))
    out = treasury.check_float("pre", send=False)
    assert out["shortfall"] == 499.98 and not out["emailed"] and not wired
    assert out["subject"].startswith("Tron float short")


def test_run_float_check_never_raises(monkeypatch):
    def boom(label, send=True):
        raise RuntimeError("x")
    monkeypatch.setattr(treasury, "check_float", boom)
    assert treasury.run_float_check("post")["ok"] is False
