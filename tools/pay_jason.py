#!/usr/bin/env python3
"""Pay Jason by hand from the payout wallet — the manual fallback for the nightly
automation (HANDOFF §32).

It calls the SAME audited path the nightly job uses (core.tron_payout.send), so it
inherits every guard already written and tested:
  * recipient is FIXED to Jason's address — you cannot fat-finger it here;
  * USDT-TRC20 only (a dollar amount can never go out as a TRX quantity);
  * a preflight balance / TRX check before anything is signed;
  * "uncertain broadcast" (the node answered slowly) is reported, NEVER retried.

The wallet's private key is read from PAYOUT_TRON_PRIVATE_KEY in .env and is never
printed. Nothing is sent without BOTH --live AND typing the amount back to confirm.

Usage (run from ~/peptide-agents):

    python3 -m tools.pay_jason 50            # preview: show wallet + what it would send
    python3 -m tools.pay_jason 50 --live     # actually send $50 USDT to Jason

Only whole-dollar or cents amounts, e.g. 50 or 215.17.
"""
from __future__ import annotations
import os
import sys

# Jason's USDT-TRC20 address, checksum- and ledger-verified (HANDOFF §33a). Used
# only if JASON_TRON_ADDRESS is not already in the environment, so the fallback
# still pays the right person on a machine whose .env never had it.
_JASON_FALLBACK = "TFLpPGV6BLNCvhoCPtsQEpNCMPLFdcfZFu"


def main(argv: list[str]) -> int:
    live = "--live" in argv
    nums = [a for a in argv if not a.startswith("-")]
    if len(nums) != 1:
        print("usage: python3 -m tools.pay_jason <usd_amount> [--live]")
        return 2
    try:
        amount = round(float(nums[0]), 2)
    except ValueError:
        print(f"not a dollar amount: {nums[0]!r}")
        return 2
    if amount <= 0:
        print("amount must be greater than zero")
        return 2

    # Set BEFORE importing config/tron_payout: config.settings runs load_dotenv()
    # at import, but only overrides keys that appear in .env — these two do not,
    # so they survive. PAYOUT_DRY_RUN=0 lets preflight actually read the wallet
    # balance; the real gate against an accidental send is --live + confirmation
    # below, and send() is only ever reached in the --live branch.
    os.environ.setdefault("JASON_TRON_ADDRESS", _JASON_FALLBACK)
    os.environ["PAYOUT_DRY_RUN"] = "0"

    from core import tron_payout

    pre = tron_payout.preflight(amount)
    print("Manual payout to Jason")
    print(f"  from wallet : {pre.get('from') or '(needs the private key in .env)'}")
    print(f"  to (Jason)  : {pre.get('to')}")
    print(f"  amount      : {amount:.2f} USDT (TRC20)")
    bal = pre.get("balance")
    trx = pre.get("trx")
    print(f"  wallet holds: {bal if bal is None else f'{bal:.2f}'} USDT   "
          f"{trx if trx is None else f'{trx:.2f}'} TRX")
    if pre.get("problems"):
        print("\nBLOCKED — nothing sent:")
        for p in pre["problems"]:
            print(f"  - {p}")
        return 1

    if not live:
        print("\nPreview only. Re-run with --live to actually send:")
        print(f"    python3 -m tools.pay_jason {amount:g} --live")
        return 0

    typed = input(f'\nType the amount "{amount:.2f}" to CONFIRM the send to Jason: ').strip()
    if typed != f"{amount:.2f}":
        print("Confirmation did not match — nothing sent.")
        return 1

    try:
        res = tron_payout.send(amount, memo="manual")
    except tron_payout.UncertainBroadcast as e:
        print(f"\nUNCERTAIN — do NOT re-run before checking the wallet:\n  {e}")
        print(f"  wallet: https://tronscan.org/#/address/{pre.get('from')}")
        return 3
    except Exception as e:
        print(f"\nFAILED — nothing was sent:\n  {e}")
        return 1

    tx = res.get("tx_hash", "")
    print(f"\nSENT {res['amount']:.2f} USDT to {res['to']}")
    print(f"  tx: {tx}")
    print(f"  https://tronscan.org/#/transaction/{tx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
