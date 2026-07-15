from __future__ import annotations
"""
Crypto payment verification — READ-ONLY.

Watches the public blockchain for an incoming payment to our receiving address
matching an order's expected amount. Never holds keys, never moves funds — it only
reads public ledgers, so it cannot spend anything.

- USDT (TRC20 / Tron): TronGrid public API. ~1-2 min to confirm.
- BTC: mempool.space public API. Requires >=1 confirmation (~10 min).

Matching is by amount: each order is given a unique amount (unique cents tail for
USDT; the exact locked BTC quote for BTC) so an incoming amount maps to one order.
"""
import json
import time
import urllib.request
import urllib.error

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # Tether USDT on Tron (6 decimals)
_UA = {"User-Agent": "northline-verify/1.0"}


def _get(url: str, timeout: int = 12) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        print(f"[crypto] fetch failed {url[:60]}…: {e}")
        return None


# ── USDT (TRC20) ─────────────────────────────────────────────────────────────

def verify_usdt(address: str, expected_usdt: float, since_unix: float,
                tolerance: float = 0.005) -> dict | None:
    """Look for an inbound USDT-TRC20 transfer to `address` of ~expected_usdt
    that arrived at/after since_unix. Returns {tx_hash, amount, ts} or None."""
    if not address:
        return None
    min_ms = int(since_unix * 1000)
    url = (f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
           f"?only_to=true&contract_address={USDT_TRC20_CONTRACT}&limit=50&min_timestamp={min_ms}")
    data = _get(url)
    if not data or "data" not in data:
        return None
    for tx in data["data"]:
        try:
            dec = int(tx.get("token_info", {}).get("decimals", 6))
            amt = int(tx["value"]) / (10 ** dec)
            ts = int(tx["block_timestamp"]) / 1000
        except (KeyError, ValueError, TypeError):
            continue
        if tx.get("to", "").strip() != address.strip():
            continue
        if ts < since_unix:
            continue
        if abs(amt - expected_usdt) <= tolerance:
            return {"tx_hash": tx.get("transaction_id"), "amount": round(amt, 2), "ts": ts, "coin": "USDT"}
    return None


# ── BTC ──────────────────────────────────────────────────────────────────────

def btc_price_usd() -> float | None:
    d = _get("https://mempool.space/api/v1/prices")
    if isinstance(d, dict) and d.get("USD"):
        return float(d["USD"])
    return None


def usd_to_btc(usd: float) -> float | None:
    """Convert a USD total to a BTC amount at the current rate (8 dp). Lock this
    at quote time and tell the customer the exact BTC figure."""
    p = btc_price_usd()
    if not p:
        return None
    return round(usd / p, 8)


def verify_btc(address: str, expected_btc: float, since_unix: float,
               tol_pct: float = 0.015, min_conf: int = 1, over_pct: float = 0.05,
               other_amounts: list[float] | None = None) -> dict | None:
    """Look for an inbound BTC tx to `address` of ~expected_btc, confirmed, since
    since_unix. tol_pct allows for tiny rate drift between quote and send.
    Overpayment up to over_pct is accepted (customers pad for fees / exchanges
    round UP); underpayment beyond tol_pct is never accepted. A tx whose amount
    matches some OTHER awaiting order's expected amount (other_amounts, within
    tol_pct) is skipped — it belongs to that order, not this one."""
    if not address or not expected_btc:
        return None
    txs = _get(f"https://mempool.space/api/address/{address}/txs")
    if not isinstance(txs, list):
        return None
    exp_sats = int(round(expected_btc * 1e8))
    tol = max(int(exp_sats * tol_pct), 1000)
    over = max(int(exp_sats * over_pct), tol)
    for tx in txs:
        st = tx.get("status", {})
        if not st.get("confirmed"):
            continue
        bt = st.get("block_time", 0)
        if bt and bt < since_unix - 3600:  # small grace for clock skew
            continue
        received = sum(v.get("value", 0) for v in tx.get("vout", [])
                       if v.get("scriptpubkey_address") == address)
        if not received or not (-tol <= received - exp_sats <= over):
            continue
        if any(abs(received - int(round(o * 1e8))) <= max(int(o * 1e8 * tol_pct), 1000)
               for o in (other_amounts or [])):
            continue  # exact match for another awaiting order — not ours
        return {"tx_hash": tx.get("txid"), "amount": round(received / 1e8, 8),
                "ts": bt or time.time(), "coin": "BTC"}
    return None


# ── USDT on Solana (SPL) ─────────────────────────────────────────────────────

SOLANA_RPC = "https://api.mainnet-beta.solana.com"
USDT_SOL_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"  # Tether USDT (SPL, 6 decimals)


def _sol_rpc(method: str, params: list, timeout: int = 12):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        req = urllib.request.Request(SOLANA_RPC, data=json.dumps(payload).encode(),
                                     headers={**_UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("result")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        print(f"[crypto] solana rpc {method} failed: {e}")
        return None


def verify_usdt_solana(owner: str, expected_usdt: float, since_unix: float,
                       tolerance: float = 0.005) -> dict | None:
    """Look for an inbound USDT-SPL transfer to `owner`'s token account of ~expected_usdt
    since since_unix. Reads token-balance deltas from the transaction. Returns dict or None."""
    if not owner:
        return None
    res = _sol_rpc("getTokenAccountsByOwner", [owner, {"mint": USDT_SOL_MINT}, {"encoding": "jsonParsed"}])
    accts = [a["pubkey"] for a in ((res or {}).get("value") or [])]
    if not accts:
        return None  # no USDT account yet (created on first receipt)
    for ata in accts:
        sigs = _sol_rpc("getSignaturesForAddress", [ata, {"limit": 15}]) or []
        for s in sigs:
            bt = s.get("blockTime") or 0
            if s.get("err") or (bt and bt < since_unix - 3600):
                continue
            tx = _sol_rpc("getTransaction", [s["signature"],
                          {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            meta = (tx or {}).get("meta") or {}
            pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances", [])
                   if b.get("mint") == USDT_SOL_MINT and b.get("owner") == owner}
            post = {b["accountIndex"]: b for b in meta.get("postTokenBalances", [])
                    if b.get("mint") == USDT_SOL_MINT and b.get("owner") == owner}
            for idx, pb in post.items():
                pre_amt = float((pre.get(idx, {}).get("uiTokenAmount", {}) or {}).get("uiAmount") or 0)
                post_amt = float((pb.get("uiTokenAmount", {}) or {}).get("uiAmount") or 0)
                if abs((post_amt - pre_amt) - expected_usdt) <= tolerance:
                    return {"tx_hash": s["signature"], "amount": round(post_amt - pre_amt, 2),
                            "ts": bt or time.time(), "coin": "USDT"}
    return None


# ── USDT on Ethereum (ERC-20) ────────────────────────────────────────────────

USDT_ERC20 = "0xdac17f958d2ee523a2206206994597c13d831ec7"  # Tether USDT on Ethereum (6 decimals)


def verify_usdt_eth(address: str, expected_usdt: float, since_unix: float,
                    tolerance: float = 0.005, max_over: float = 5.0,
                    other_amounts: list[float] | None = None) -> dict | None:
    """Look for an inbound USDT-ERC20 transfer to `address` of ~expected_usdt, since
    since_unix, via the Etherscan API (needs settings.etherscan_api_key).
    Overpayment up to max_over USDT is accepted (real senders pad for fees or get
    rounded UP by exchanges — an exact-only match strands their order); underpayment
    is never accepted. A tx that exactly matches some OTHER awaiting order's unique
    amount (other_amounts) is skipped — it belongs to that order."""
    if not address:
        return None
    from config import settings
    key = settings.etherscan_api_key
    if not key:
        print("[crypto] ETHERSCAN_API_KEY not set — cannot verify ERC-20 USDT")
        return None
    url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account&action=tokentx"
           f"&contractaddress={USDT_ERC20}&address={address}&page=1&offset=25&sort=desc&apikey={key}")
    d = _get(url)
    if not isinstance(d, dict) or not isinstance(d.get("result"), list):
        return None
    for tx in d["result"]:
        try:
            if tx.get("to", "").lower() != address.lower():
                continue
            dec = int(tx.get("tokenDecimal", 6))
            amt = int(tx["value"]) / (10 ** dec)
            ts = int(tx.get("timeStamp", 0))
        except (KeyError, ValueError, TypeError):
            continue
        if ts < since_unix - 3600:
            continue
        if not (-tolerance <= amt - expected_usdt <= max_over):
            continue
        if any(abs(amt - o) <= tolerance for o in (other_amounts or [])):
            continue  # exact match for another awaiting order — not ours
        return {"tx_hash": tx.get("hash"), "amount": round(amt, 2), "ts": ts, "coin": "USDT"}
    return None


def verify_payment(coin: str, address: str, expected_amount: float, since_unix: float,
                   other_amounts: list[float] | None = None) -> dict | None:
    """Dispatch to the right chain. expected_amount is USDT units for USDT, BTC for BTC.
    USDT is verified on Ethereum (ERC-20). other_amounts = expected amounts of OTHER
    awaiting orders on the same coin, so an overpaid tx can't be claimed by the wrong
    order."""
    coin = (coin or "").upper()
    if coin == "USDT":
        return verify_usdt_eth(address, expected_amount, since_unix, other_amounts=other_amounts)
    if coin == "BTC":
        return verify_btc(address, expected_amount, since_unix, other_amounts=other_amounts)
    return None
