from __future__ import annotations
"""
Tron payouts — THE ONLY MODULE IN THIS REPO THAT CAN SPEND MONEY.

Everything else that touches crypto here is read-only by design: `core/crypto_verify.py`
opens with "Never holds keys, never moves funds — it only reads public ledgers, so it
cannot spend anything." That sentence stops being true for the repo as a whole on
2026-09-04, and this file is the entire reason. Keep it that way: if you need to send
funds from somewhere else, call this, do not sign a transaction anywhere else.

A Tron transfer is FINAL. There is no chargeback, no reversal, no support line. A wrong
address is gone; a wrong amount is gone. So this module refuses far more than it sends:

  • it will not send to an address that does not pass Tron's own checksum
  • it will not send to an address that is not the configured recipient
  • it will not send zero, a negative, or a NaN
  • it will not send more than PAYOUT_MAX_USD in one transaction
  • it will not send if the wallet's balance would not cover it
  • it does nothing at all while PAYOUT_DRY_RUN is on, which is the DEFAULT

WHY A DEDICATED WALLET. The key here should belong to a wallet that holds a float —
a few weeks of Jason's fees — and nothing else. Not the receiving address customers
pay into, and not a personal wallet. The blast radius of any bug in this file, or of
the Railway environment leaking, is exactly the float. Funding it is a manual act,
which is itself a rate limit no code can bypass.

ASSET. USDT-TRC20 only. The fee is quoted in dollars ($12 a box), and USDT on Tron
settles at exactly the number that was calculated.

Native TRX is deliberately NOT supported. Sending TRX would mean converting dollars at
a live rate inside the payment path, and the failure mode of getting that wrong is
silent: a $480 night sent as 480 TRX looks like a clean success and pays Jason about
$144. If TRX is ever wanted, it needs a real price feed with its own fail-closed
behaviour when the feed is unavailable — not a multiplication by one. Until that
exists, `PAYOUT_ASSET=TRX` is refused rather than approximated.

ENERGY. A TRC20 transfer burns energy/bandwidth, paid in TRX, from the SENDING wallet.
A wallet holding USDT and zero TRX cannot pay anybody. `preflight()` checks for it and
says so in words rather than letting a transfer fail at 3am with a stack trace.
"""
import time

_USDT_TRC20_MAINNET = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # 6 decimals


class PayoutError(Exception):
    """Any refusal or failure to pay. Always safe to treat as 'no funds moved',
    with ONE documented exception — see `Broadcast.uncertain`."""


class UncertainBroadcast(PayoutError):
    """The transaction MAY have gone out. Raised when a transfer was broadcast but
    the confirmation read failed (network dropped between send and receipt).

    This is the one state the caller must not retry blindly. It is deliberately a
    distinct exception so `agents/warehouse_payout.py` can leave the batch CLAIMED
    (nobody gets paid twice) and alert a human to look at the wallet, rather than
    rolling back and re-sending a transfer that may already be on-chain.
    """

    def __init__(self, message: str, tx_hash: str = ""):
        super().__init__(message)
        self.tx_hash = tx_hash


# ── Configuration, read late so tests and dry runs can move it ───────────────

def _cfg():
    from config import settings
    return settings


def is_enabled() -> bool:
    """True when this module is allowed to broadcast for real."""
    s = _cfg()
    return bool(s.payout_private_key and s.jason_tron_address and not s.payout_dry_run)


def _client(private_key):
    from tronpy import Tron
    from tronpy.providers import HTTPProvider
    s = _cfg()
    key = s.trongrid_api_key or None
    provider = HTTPProvider(api_key=key) if key else HTTPProvider()
    return Tron(provider)


def valid_address(addr: str) -> bool:
    """Tron base58check, checksum verified. `TXYZ...` typed by hand fails here."""
    try:
        from tronpy.keys import is_base58check_address
        return bool(addr) and is_base58check_address(addr.strip())
    except Exception:
        return False


def _check_amount(usd: float) -> float:
    s = _cfg()
    try:
        amt = float(usd)
    except (TypeError, ValueError):
        raise PayoutError(f"amount is not a number: {usd!r}")
    if amt != amt or amt in (float("inf"), float("-inf")):
        raise PayoutError(f"amount is not finite: {usd!r}")
    if amt <= 0:
        raise PayoutError(f"refusing to send a non-positive amount ({amt})")
    cap = float(s.payout_max_usd or 0)
    # The circuit breaker is NOT an approval gate — Jordan asked for no approvals
    # (2026-09-04) and there is none. It is a bug catcher: a night that wants ten
    # times a normal payout is a miscount, not a big day.
    #
    # The message deliberately does NOT suggest raising the ceiling. The most
    # likely cause of a huge night is a bad WAREHOUSE_FEE_START_DATE sweeping in
    # the back catalogue, and an operator who is told "raise the limit and re-run"
    # will do exactly that and pay the whole history in one irreversible transfer.
    if cap and amt > cap:
        raise PayoutError(
            f"${amt:,.2f} is over the ${cap:,.2f} PAYOUT_MAX_USD ceiling, so NOTHING "
            f"was sent. This is almost always a miscount, not a big night — check "
            f"WAREHOUSE_FEE_START_DATE and run `python3 -m agents.warehouse_payout "
            f"preview` to see which orders are in the batch BEFORE changing the "
            f"ceiling. Only raise it once the order list looks right.")
    return round(amt, 2)


def _recipient() -> str:
    s = _cfg()
    to = (s.jason_tron_address or "").strip()
    if not to:
        raise PayoutError("JASON_TRON_ADDRESS is not set — nowhere to send.")
    if not valid_address(to):
        raise PayoutError(
            f"JASON_TRON_ADDRESS is not a valid Tron address: {to!r}. "
            f"A Tron address starts with T and is 34 characters; this failed the "
            f"checksum, which usually means a typo or a pasted line break.")
    return to


# ── Preflight ────────────────────────────────────────────────────────────────

def preflight(amount_usd: float | None = None) -> dict:
    """Everything that can be checked WITHOUT broadcasting.

    Returns {"ok", "problems", "asset", "to", "from", "balance", "trx"}. Called by
    the nightly job before it claims anything, so a misconfiguration costs an email
    rather than a half-finished payout, and callable by hand to answer "is this
    thing actually set up?".
    """
    s = _cfg()
    out = {"ok": False, "problems": [], "asset": (s.payout_asset or "USDT").upper(),
           "to": (s.jason_tron_address or ""), "from": "", "balance": None,
           "trx": None, "dry_run": bool(s.payout_dry_run)}

    if not s.jason_tron_address:
        out["problems"].append("JASON_TRON_ADDRESS is not set")
    elif not valid_address(s.jason_tron_address):
        out["problems"].append(f"JASON_TRON_ADDRESS fails Tron's checksum: {s.jason_tron_address!r}")
    if out["asset"] == "TRX":
        out["problems"].append(
            "PAYOUT_ASSET=TRX is not supported. Jason is owed a DOLLAR amount and "
            "there is no price feed here, so sending TRX would pay him the dollar "
            "figure as a TRX quantity — roughly a third of what he is owed. Use "
            "USDT (the default), which settles at the calculated number.")
    elif out["asset"] != "USDT":
        out["problems"].append(f"PAYOUT_ASSET must be USDT, got {out['asset']!r}")
    # The spending key is required only for a REAL send. A dry run must be able
    # to exercise the whole nightly path — batch, claim, statement, email — on a
    # machine that has no key on it at all, which is the only way the first live
    # night is not also the first end-to-end test.
    if not s.payout_dry_run and not s.payout_private_key:
        out["problems"].append("PAYOUT_TRON_PRIVATE_KEY is not set")
    if out["problems"]:
        return out

    if s.payout_dry_run or not s.payout_private_key:
        out["ok"] = True          # nothing will be broadcast, so no wallet to check
        return out

    try:
        from tronpy.keys import PrivateKey
        priv = PrivateKey(bytes.fromhex(s.payout_private_key.strip().removeprefix("0x")))
        sender = priv.public_key.to_base58check_address()
    except Exception as e:
        out["problems"].append(f"PAYOUT_TRON_PRIVATE_KEY is not a usable Tron private key ({e})")
        return out
    out["from"] = sender

    if sender == out["to"]:
        out["problems"].append("the sending wallet and JASON_TRON_ADDRESS are the same address")
        return out

    try:
        client = _client(priv)
        out["trx"] = float(client.get_account_balance(sender))
        contract = client.get_contract(s.usdt_trc20_contract or _USDT_TRC20_MAINNET)
        dec = contract.functions.decimals()
        out["balance"] = contract.functions.balanceOf(sender) / (10 ** dec)
    except Exception as e:
        out["problems"].append(f"could not read the wallet on-chain: {e}")
        return out

    # A TRC20 transfer is paid for in TRX (energy/bandwidth). USDT with no TRX is
    # a wallet that looks funded and cannot send.
    if out["trx"] < 15:
        out["problems"].append(
            f"the sending wallet holds only {out['trx']:.2f} TRX. A USDT-TRC20 transfer "
            f"burns TRX for energy — top it up to ~50 TRX or the send will fail.")
    if amount_usd is not None and out["balance"] is not None and out["balance"] < float(amount_usd):
        out["problems"].append(
            f"balance {out['balance']:.2f} {out['asset']} is short of the "
            f"{float(amount_usd):.2f} needed")
    out["ok"] = not out["problems"]
    return out


# ── The send ─────────────────────────────────────────────────────────────────

def send(amount_usd: float, memo: str = "") -> dict:
    """Send `amount_usd` to the configured recipient. Returns
    {"tx_hash", "amount", "asset", "to", "dry_run", "ts"}.

    Raises PayoutError if anything is wrong and NOTHING was sent.
    Raises UncertainBroadcast if it may have gone out — do not retry on that.
    """
    s = _cfg()
    amt = _check_amount(amount_usd)
    to = _recipient()
    asset = (s.payout_asset or "USDT").upper()
    if asset != "USDT":
        raise PayoutError(
            f"PAYOUT_ASSET={asset} is not supported — only USDT-TRC20 is. See the "
            f"module docstring: paying a dollar figure in a floating asset without a "
            f"price feed underpays silently.")

    if s.payout_dry_run or not s.payout_private_key:
        # Deliberately looks exactly like a real result, minus the money. The
        # nightly job's whole path — claim, send, record, email — runs in dry
        # run, so the first live night is not also the first end-to-end test.
        why = "PAYOUT_DRY_RUN is on" if s.payout_dry_run else "no private key is configured"
        print(f"[payout] DRY RUN ({why}) — would send {amt} {asset} to {to}: {memo}")
        return {"tx_hash": f"DRYRUN-{int(time.time())}", "amount": amt, "asset": asset,
                "to": to, "dry_run": True, "ts": time.time()}

    pre = preflight(amt)
    if not pre["ok"]:
        raise PayoutError("; ".join(pre["problems"]))

    from tronpy.keys import PrivateKey
    priv = PrivateKey(bytes.fromhex(s.payout_private_key.strip().removeprefix("0x")))
    sender = priv.public_key.to_base58check_address()
    client = _client(priv)

    try:
        contract = client.get_contract(s.usdt_trc20_contract or _USDT_TRC20_MAINNET)
        dec = contract.functions.decimals()
        units = int(round(amt * (10 ** dec)))
        txn = (contract.functions.transfer(to, units)
               .with_owner(sender)
               .fee_limit(int(s.payout_fee_limit_sun))
               .build().sign(priv))
    except Exception as e:
        # Build/sign failures happen BEFORE anything reaches the network.
        raise PayoutError(f"could not build the transfer: {e}") from e

    # WHY THE BROADCAST HANDLER IS SPLIT IN TWO. A broadcast that raises is NOT
    # automatically a broadcast that did not happen. tronpy posts to the node with
    # a 10-second timeout; a ReadTimeout or a TronGrid 502/504 means the request
    # reached the node, which may well have accepted and gossiped the transaction,
    # and only the RESPONSE was lost. Treating that as "nothing was sent" makes the
    # caller release its claims and pay the whole night again tomorrow.
    #
    # So only errors tronpy raises for a transaction the network REJECTED are
    # PayoutError (definitely not sent). Every transport-level failure is
    # UncertainBroadcast, which the caller must never roll back.
    #
    # The boundary is drawn at "did the node answer", not at a hand-picked list of
    # exception classes: naming three of tronpy's exceptions left TaposError,
    # SERVER_BUSY and the ApiError catch-all — the routinely retryable rejections —
    # classified as uncertain, which holds the claims and makes a human untick a
    # night's orders by hand every time the node is busy.
    try:
        result = txn.broadcast()
    except _node_answered() as e:
        raise PayoutError(f"the network rejected the transfer, nothing was sent: {e}") from e
    except Exception as e:
        raise UncertainBroadcast(
            f"the transfer was sent to the network but no reply came back ({e}). "
            f"It may or may not be on-chain. CHECK THE WALLET on tronscan before "
            f"anything else — nothing has been re-sent, and the orders in this batch "
            f"are left marked as paid so they cannot go out twice.") from e

    # A "successful" broadcast with no transaction id cannot be recorded, cannot
    # be checked on tronscan, and would be swept up as an abandoned claim and
    # paid again. It is an uncertain broadcast, not a success.
    try:
        tx_hash = result.txid
    except Exception as e:
        raise UncertainBroadcast(
            f"the transfer was broadcast but no transaction id came back ({e}). "
            f"CHECK THE WALLET on tronscan — the orders in this batch are left "
            f"marked as paid so they cannot go out twice.") from e
    if not tx_hash:
        raise UncertainBroadcast(
            "the transfer was broadcast but the transaction id was empty. CHECK "
            "THE WALLET on tronscan — the orders in this batch are left marked "
            "as paid so they cannot go out twice.")
    try:
        receipt = result.wait(timeout=60)
        ok = (receipt or {}).get("receipt", {}).get("result", "SUCCESS")
        if ok not in ("SUCCESS", None):
            raise UncertainBroadcast(
                f"transfer {tx_hash} was broadcast but the network reported {ok!r}. "
                f"CHECK THE WALLET before re-sending.", tx_hash=tx_hash)
    except UncertainBroadcast:
        raise
    except Exception as e:
        raise UncertainBroadcast(
            f"transfer {tx_hash or '(hash unknown)'} was broadcast but its confirmation "
            f"could not be read ({e}). It may or may not have gone through — CHECK THE "
            f"WALLET on tronscan before doing anything else. Nothing has been re-sent.",
            tx_hash=tx_hash) from e

    print(f"[payout] sent {amt} {asset} to {to} tx={tx_hash}")
    return {"tx_hash": tx_hash, "amount": amt, "asset": asset, "to": to,
            "dry_run": False, "ts": time.time()}


def _node_answered() -> tuple:
    """Exception classes that mean the node REPLIED, rejecting the transaction.

    Everything tronpy raises from `tronpy.exceptions` is a parsed node response —
    TaposError, ValidationError, TransactionError, DoubleSpending, BadSignature,
    UnknownError (SERVER_BUSY, BANDWITH_ERROR, ...) and ApiError all mean the
    request completed and the transfer did NOT go out. Taking the module wholesale
    keeps that true as tronpy adds classes.

    A connect-phase failure is also safe: nothing reached the node. A ReadTimeout
    is NOT — the request was delivered and only the reply was lost — so it is
    deliberately excluded and falls through to UncertainBroadcast.
    """
    out = []
    try:
        import tronpy.exceptions as te
        out += [c for c in vars(te).values()
                if isinstance(c, type) and issubclass(c, Exception)]
    except Exception:                                     # pragma: no cover
        pass
    try:
        from requests.exceptions import ConnectTimeout, ProxyError, SSLError
        out += [ConnectTimeout, ProxyError, SSLError]
    except Exception:                                     # pragma: no cover
        pass
    return tuple(out)


def explorer_url(tx_hash: str) -> str:
    if not tx_hash or tx_hash.startswith("DRYRUN"):
        return ""
    return f"https://tronscan.org/#/transaction/{tx_hash}"
