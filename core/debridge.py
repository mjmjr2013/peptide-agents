from __future__ import annotations
"""
deBridge (DLN) — quotes and a pre-filled app link for topping up the Tron float.

READ-ONLY BY DESIGN. This module never signs anything. `core/tron_payout.py` is
the only module in this repo that can spend, and Jordan decided (2026-09-18,
HANDOFF §35) that the Phantom wallet's key stays OFF this host: when the float
is short, he gets an email with a link that opens deBridge with the chains, the
tokens and the recipient already filled in, and taps once in Phantom.

What it does know how to do:
  • quote(usd_in)       ask DLN what `usd_in` USDT on Ethereum becomes as USDT on Tron
  • size_topup(short)   pick the smallest sensible amount to send so that what ARRIVES
                        covers the shortfall — bridge costs are ~$10 flat, so a $30
                        top-up is silly and a $87.38 shortfall needs ~$100 sent
  • app_link(...)       the deep link (params per deBridge's "Custom Linking" docs)

Chain ids: DLN's API uses 100000026 for Tron; the web app's URL uses Tron's own
728126428. Both are pinned here because getting one wrong silently pre-fills the
wrong destination.
"""
import json
import math
import urllib.parse
import urllib.request

DLN_API = "https://dln.debridge.finance/v1.0"
APP_URL = "https://app.debridge.com/deswap"

ETH_CHAIN = 1
TRON_DLN_CHAIN = 100000026      # what the DLN API calls Tron
TRON_APP_CHAIN = 728126428      # what app.debridge.com's URL calls Tron
USDT_ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"   # 6 decimals
USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"          # 6 decimals

_UA = "northline-treasury/1.0"


class QuoteError(RuntimeError):
    pass


def _get(url: str, timeout: int = 40) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def quote(usd_in: float) -> dict:
    """What `usd_in` USDT sent from Ethereum arrives as on Tron.

    Returns {"usd_in", "usd_out", "cost_usd", "fix_fee_eth", "eta_s"}.
    `usd_in` in the result is the amount the user must actually approve — DLN
    prepends its operating expenses, so it is a little more than asked."""
    if usd_in <= 0:
        raise QuoteError("amount must be positive")
    q = urllib.parse.urlencode({
        "srcChainId": ETH_CHAIN, "srcChainTokenIn": USDT_ETH,
        "srcChainTokenInAmount": int(round(usd_in * 1_000_000)),
        "dstChainId": TRON_DLN_CHAIN, "dstChainTokenOut": USDT_TRON,
        "dstChainTokenOutAmount": "auto", "prependOperatingExpenses": "true"})
    try:
        d = _get(f"{DLN_API}/dln/order/create-tx?{q}")
    except Exception as e:
        raise QuoteError(f"deBridge quote failed: {e!r}")
    if "estimation" not in d:
        raise QuoteError(f"deBridge quote refused: {d.get('errorMessage') or d}"[:300])
    est = d["estimation"]
    amt_in = int(est["srcChainTokenIn"]["amount"]) / 1_000_000
    amt_out = int(est["dstChainTokenOut"].get("recommendedAmount")
                  or est["dstChainTokenOut"]["amount"]) / 1_000_000
    # The API prepends its operating expenses to what was asked (amt_in > usd_in).
    # In the app, the number Jordan TYPES is the total that leaves Phantom, so the
    # conservative arrival for "type usd_in" is the quoted arrival minus that
    # prepended part. Sizing uses the conservative figure; never under-cover.
    prepended = max(amt_in - usd_in, 0.0)
    return {"usd_in": round(amt_in, 2), "usd_out": round(amt_out, 2),
            "usd_out_low": round(amt_out - prepended, 2),
            "cost_usd": round(amt_in - amt_out, 2),
            "fix_fee_eth": int(d.get("fixFee") or 0) / 1e18,
            "eta_s": int((d.get("order") or {}).get("approximateFulfillmentDelay") or 0)}


def round_up(usd: float, step: float = 50.0) -> float:
    return float(math.ceil(usd / step) * step)


def size_topup(shortfall_usd: float, min_usd: float = 200.0, step: float = 50.0,
               quote_fn=quote, attempts: int = 4) -> dict:
    """The smallest round amount to SEND so that what ARRIVES covers the shortfall.

    Starts at max(min_usd, shortfall rounded up), quotes it, and bumps by `step`
    until the CONSERVATIVE arrival (`usd_out_low`) covers the shortfall. Returns the final quote plus
    {"send_usd": <the round number Jordan types>}. Raises QuoteError if the
    bridge cannot be quoted at all — the caller then emails the shortfall without
    a size, never a guessed one."""
    send = max(float(min_usd), round_up(max(shortfall_usd, 0.0), step))
    last, quoted = None, send
    for _ in range(attempts):
        last, quoted = quote_fn(send), send
        if last["usd_out_low"] >= shortfall_usd:
            break
        send += step
    out = dict(last or {})
    out["send_usd"] = quoted          # the amount the quote actually describes
    out["covers"] = bool(last and last["usd_out_low"] >= shortfall_usd)
    return out


def app_link(send_usd: float, tron_address: str) -> str:
    """Opens app.debridge.com with Ethereum USDT → Tron USDT to `tron_address`
    pre-filled. The amount is included too, though the app only keeps it once a
    wallet is connected — the email states it in words for that reason."""
    q = urllib.parse.urlencode({
        "inputChain": ETH_CHAIN, "inputCurrency": USDT_ETH,
        "outputChain": TRON_APP_CHAIN, "outputCurrency": USDT_TRON,
        "address": tron_address, "amount": f"{send_usd:g}"})
    return f"{APP_URL}?{q}"
