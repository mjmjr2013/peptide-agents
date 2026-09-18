from __future__ import annotations
"""
Tron float watch — makes sure Jason's payout wallet can pay tonight (HANDOFF §35).

WHY. §33j: the nightly payout stalled because the float had been drained by a
manual payment, and the system only found out AFTER the run failed. A proactive
check was offered and not built then. Jordan asked for it on 2026-09-18, with a
twist: when the float is short, pull the money from the Phantom wallet on
Ethereum and bridge it to Tron with deBridge.

HOW MUCH OF THAT IS AUTOMATED — deliberately not all of it. Bridging from Phantom
means signing with Phantom's Ethereum key. That is the address every customer
pays into, and the safety model since §32c is that the only spending key on this
host controls a small float and nothing else. Offered both ways, Jordan chose
"prepare-and-tap": this agent does everything up to the signature —

  • reads the float wallet's USDT and TRX (no key needed — a balance is public)
  • prices what the next payout owes (the same arithmetic the payout uses)
  • adds the reserve, works out the shortfall, sizes a round top-up so that what
    ARRIVES covers it after deBridge's ~$10 cost
  • checks Phantom actually holds that much USDT and some ETH for gas
  • emails Jordan ONE message with a deBridge link that has the chains, tokens
    and recipient already filled in, and the amount to type

— and he taps once in Phantom. It moves nothing itself and cannot.

WHEN. Twice around the payout beat in main.py's scheduler: an hour BEFORE
(tonight's exact number, last call) and right AFTER (so a float the payout just
drained is flagged with a day's notice, not an hour's). Each emails only when
something is short — a healthy float is a log line.

FAIL LOUD. A wallet that cannot be READ is reported as exactly that, never as
"fine" and never as "empty": the payout's own preflight will refuse to send
regardless, so the only harm in a bad read here is a missing warning, and the
§33j lesson is that missing warnings are the expensive kind.

Manual:  python3 -m agents.treasury            (prints the check, sends nothing)
         python3 -m agents.treasury --send     (also emails if short)
"""
import json
import traceback
import urllib.request
from datetime import datetime, timezone

from config import settings
from core import debridge, tron_payout

USDT_ETH_DECIMALS = 1_000_000


# ── Reads ─────────────────────────────────────────────────────────────────────

def _eth_rpc(method: str, params: list) -> str:
    req = urllib.request.Request(
        settings.eth_rpc_url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "northline-treasury/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        out = json.load(r)
    if "result" not in out:
        raise RuntimeError(f"eth rpc {method}: {out.get('error')}")
    return out["result"]


def phantom_balances(address: str | None = None) -> dict:
    """{'address','usdt','eth'} for the Phantom Ethereum account (settings.eth_address).
    Public reads only. Raises on failure."""
    address = (address or settings.eth_address or "").strip()
    if not address.startswith("0x") or len(address) != 42:
        raise RuntimeError(f"ETH_ADDRESS is not set or not an Ethereum address ({address!r})")
    eth = int(_eth_rpc("eth_getBalance", [address, "latest"]), 16) / 1e18
    data = "0x70a08231" + address[2:].lower().rjust(64, "0")      # balanceOf(address)
    usdt = int(_eth_rpc("eth_call", [{"to": debridge.USDT_ETH, "data": data}, "latest"]), 16)
    return {"address": address, "usdt": usdt / USDT_ETH_DECIMALS, "eth": eth}


# ── The check ─────────────────────────────────────────────────────────────────

def assess(wallet: dict, owed: dict, reserve_usd: float, trx_floor: float) -> dict:
    """Pure arithmetic: is the float enough for `owed` plus the reserve?
    Returns {"need","shortfall","trx_low","ok"}."""
    need = float(owed.get("total_usd") or 0.0) + float(reserve_usd)
    shortfall = max(0.0, round(need - float(wallet["usdt"]), 2))
    trx_low = float(wallet["trx"]) < float(trx_floor)
    return {"need": round(need, 2), "shortfall": shortfall, "trx_low": trx_low,
            "ok": shortfall == 0 and not trx_low}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def compose(label: str, wallet: dict, owed: dict, verdict: dict, plan: dict | None,
            phantom: dict | None, reserve_usd: float, trx_floor: float,
            plan_error: str = "", phantom_error: str = "") -> tuple[str, str]:
    """(subject, body) of the email. Pure — tested from literals."""
    short = verdict["shortfall"]
    addr = wallet["address"]
    tail = addr[-6:]
    when = "before tonight's payout" if label == "pre" else "by tomorrow night"
    if short:
        send = plan["send_usd"] if plan else None
        subject = (f"Tron float short: bridge {_money(send)} {when}" if send
                   else f"Tron float short by {_money(short)} {when}")
    else:
        subject = f"Tron wallet needs TRX (only {wallet['trx']:.0f} TRX left)"

    lines = ["Jason's payout wallet check:", "",
             f"  Wallet ({addr}):   {wallet['usdt']:,.2f} USDT   {wallet['trx']:,.2f} TRX",
             f"  Owed by next payout:  {_money(float(owed.get('total_usd') or 0))}"
             f"  ({owed.get('orders', 0)} order(s), {owed.get('boxes', 0)} box(es))",
             f"  Reserve to keep:      {_money(reserve_usd)}",
             f"  Shortfall:            {_money(short)}", ""]
    if short:
        if plan:
            lines += [f"WHAT TO DO — one tap in Phantom, about 2 minutes:",
                      f"  1. Open this link. It pre-fills USDT on Ethereum → USDT on Tron, "
                      f"sent to the payout wallet …{tail}:",
                      f"     {debridge.app_link(plan['send_usd'], addr)}",
                      f"  2. Connect Phantom (Ethereum) and type {plan['send_usd']:g} as the amount you pay.",
                      f"  3. Check \"You receive\" shows about {plan['usd_out_low']:,.0f}–{plan['usd_out']:,.0f} USDT "
                      f"on Tron going to …{tail}, then confirm.", "",
                      f"Sending {plan['send_usd']:g} USDT costs about {_money(plan['cost_usd'])} in "
                      f"bridge fees plus a little ETH for gas (deBridge's fixed fee is "
                      f"{plan['fix_fee_eth']:g} ETH). It lands on Tron within a couple of minutes.", ""]
        else:
            lines += ["WHAT TO DO — bridge USDT from Phantom to the payout wallet with deBridge:",
                      f"     {debridge.app_link(debridge.round_up(short + 20), addr)}",
                      f"  (Could not get a live quote to size it: {plan_error}. Send the shortfall "
                      f"plus about $15 for fees.)", ""]
        if phantom:
            need_usdt = plan["send_usd"] if plan else short + 15
            lines.append(f"Phantom right now: {phantom['usdt']:,.2f} USDT, {phantom['eth']:.4f} ETH.")
            if phantom["usdt"] < need_usdt:
                lines.append(f"  ⚠ Not enough USDT there — move at least {_money(need_usdt)} USDT (ERC-20) "
                             f"into Phantom first.")
            if phantom["eth"] < 0.004:
                lines.append("  ⚠ Low ETH — the bridge needs roughly 0.003–0.005 ETH for gas and its fee; "
                             "top up ETH too.")
            lines.append("")
        elif phantom_error:
            lines += [f"(Could not read the Phantom balance: {phantom_error})", ""]
    if verdict["trx_low"]:
        lines += [f"ALSO: the wallet holds only {wallet['trx']:.2f} TRX and a USDT transfer burns TRX "
                  f"for energy (floor {trx_floor:g}). Send ~50 TRX to {addr} or the payout will fail "
                  f"even with USDT in it.", ""]
    lines += ["If nothing is done, the payout refuses to send (nothing is lost, nothing is sent) "
              "and the orders roll into the next night — the same behaviour as §33j."]
    return subject, "\n".join(lines)


def _alert(subject: str, body: str) -> bool:
    try:
        from agents.weekly_report import _send_email
        return bool(_send_email(subject, body, [], recipients=settings.operator_emails))
    except Exception as e:
        print(f"[Treasury] alert email failed: {e!r}  subject={subject}")
        return False


def check_float(label: str = "pre", send: bool = True) -> dict:
    """One check. `label` is 'pre' (an hour before the payout) or 'post' (right
    after). NEVER RAISES — it runs in the scheduler thread with everything else."""
    out = {"label": label, "ok": False, "emailed": False,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        wallet = tron_payout.read_balances()
    except Exception as e:
        msg = f"could not read the Tron float wallet: {e!r}"
        print(f"[Treasury] {msg}")
        out["error"] = msg
        if send:
            out["emailed"] = _alert("Tron float check FAILED — wallet unreadable",
                                    f"{msg}\n\nThe payout's own preflight will refuse to send if "
                                    f"this persists. Check TRONGRID_API_KEY / PAYOUT_TRON_ADDRESS.")
        return out
    try:
        from agents.warehouse_payout import owed_now
        owed = owed_now()
    except Exception as e:
        msg = f"could not price what is owed: {e!r}"
        print(f"[Treasury] {msg}")
        out["error"] = msg
        if send:
            out["emailed"] = _alert("Tron float check FAILED — could not price the payout", msg)
        return out
    verdict = assess(wallet, owed, settings.tron_float_reserve_usd, settings.tron_trx_floor)
    out.update({"wallet": wallet, "owed_usd": owed.get("total_usd"), **verdict})
    print(f"[Treasury/{label}] usdt={wallet['usdt']:.2f} trx={wallet['trx']:.2f} "
          f"owed={float(owed.get('total_usd') or 0):.2f} need={verdict['need']:.2f} "
          f"short={verdict['shortfall']:.2f} trx_low={verdict['trx_low']}")
    if verdict["ok"]:
        out["ok"] = True
        return out
    plan = phantom = None
    plan_error = phantom_error = ""
    if verdict["shortfall"]:
        try:
            plan = debridge.size_topup(verdict["shortfall"], settings.topup_min_usd)
        except Exception as e:
            plan_error = repr(e)
        try:
            phantom = phantom_balances()
        except Exception as e:
            phantom_error = repr(e)
    subject, body = compose(label, wallet, owed, verdict, plan, phantom,
                            settings.tron_float_reserve_usd, settings.tron_trx_floor,
                            plan_error, phantom_error)
    out.update({"plan": plan, "phantom": phantom, "subject": subject, "body": body})
    if send:
        out["emailed"] = _alert(subject, body)
    return out


def run_float_check(label: str = "pre") -> dict:
    """Scheduler entry point. Swallows everything, like the payout does."""
    try:
        return check_float(label, send=True)
    except Exception as e:                                   # pragma: no cover
        print(f"[Treasury] UNCAUGHT {e!r}\n{traceback.format_exc()}")
        return {"label": label, "ok": False, "error": repr(e)}


if __name__ == "__main__":
    import sys
    res = check_float("pre", send="--send" in sys.argv)
    if res.get("body"):
        print(res["subject"] + "\n\n" + res["body"] + "\n")
    print(json.dumps({k: v for k, v in res.items() if k not in ("body",)}, indent=2, default=str))
