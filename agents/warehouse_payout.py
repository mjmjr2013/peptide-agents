from __future__ import annotations
"""
Nightly warehouse payout — pays Jason for the boxes he was asked to pack.

RUNS RIGHT AFTER THE DAILY MANIFEST, from the same in-process scheduler, so the
two land together: he gets the list of what to pack and the money for it in the
same few seconds. Jordan, 2026-09-04: *"we want payment to go at the end of the
day for all of the orders for that day. so payment plus manifest get sent out to
him daily."*

WHAT "THE ORDERS FOR THAT DAY" MEANS HERE, precisely. Not a date window — a
state: every paid, non-legacy order that has not yet been paid for. In steady
state that IS the day's new orders, because yesterday's were ticked off last
night. But it is also correct when Railway restarts mid-run, when a night is
missed, or when an order is paid at 23:59 — a date window would drop those on
the floor and nobody would notice, because nobody audits a payment that never
happened.

⚠️ PAYMENT IS TRIGGERED BY THE CUSTOMER PAYING, NOT BY THE BOX SHIPPING. That is
what Jordan asked for (money and manifest together, on the same nightly beat), and
it means Jason is paid up front for work he has not done yet. The exposure is real
and is deliberately not papered over: an order that is paid and then never ships —
the address is never supplied, the customer vanishes, the order is refunded — has
already been paid for, and there is no un-pay. Closing it means paying on
`tracking_sent` instead, which is one extra clause in
`airtable.get_orders_awaiting_warehouse_fee` — worth doing if refunds or
never-shipped orders turn out to be more than rare.

THERE IS NO APPROVAL STEP. Jordan asked for that explicitly, having been told a
Tron transfer is irreversible. So the protection is structural, not procedural:

  1. The batch is CLAIMED in Airtable before anything is broadcast, so a crash
     between the two underpays (recoverable) rather than double-pays (not).
  2. Each claim carries a timestamped RUN TOKEN. Before claiming, the order is
     re-read fresh and skipped if it is already claimed or already carries a real
     transaction hash; after claiming, the batch is re-read once more and only
     orders still holding THIS run's token are paid.

     ⚠️ THIS IS A MITIGATION, NOT A LOCK. Airtable has no compare-and-swap. The
     residual window is a second container whose queue read predates this run's
     claims by more than the settle delay — a Railway rolling deploy landing
     within seconds of DAILY_MANIFEST_HOUR, or replica count > 1. The direct
     per-record read before each claim is what makes that narrow, because a
     record fetch is far fresher than a formula-filtered list. KEEP RAILWAY AT
     ONE REPLICA; that, not this code, is what actually closes it.
  3. Orders whose weight cannot be resolved are excluded, never paid at a
     guessed box count.
  4. A single aggregate transfer per night — one transaction to check, one hash
     on every order in the batch.
  5. PAYOUT_MAX_USD refuses an absurd night. That is a bug catcher, not a
     permission gate.
  6. PAYOUT_DRY_RUN defaults ON, and a dry run CLAIMS NOTHING — it reads,
     prices, and reports. An earlier draft let the dry run tick every order as
     paid, which would have silently swallowed the entire go-live backlog.

WHY THIS IS NOT AN LLM AGENT, despite living in agents/. Every other module here
asks Claude to decide something. This one must not: the amount owed is arithmetic
on a box count, and a model that is right 99% of the time is a model that wires
the wrong number to an irreversible address once every hundred nights. The only
judgement in this file is 'is this order priceable', and the answer to 'no' is
always 'do not pay it and tell a human'.
"""
import json
import threading
import time
import traceback
import uuid
from datetime import datetime

from config import settings
from core.airtable_client import airtable, AlreadyPaid
from core import warehouse_fees, tron_payout

# How long to wait between claiming and re-reading, so a concurrent run's writes
# have landed before we decide what is ours. Airtable is read-your-writes for a
# single client but gives no ordering guarantee across two.
CLAIM_SETTLE_SECONDS = 10.0

# One payout at a time inside THIS process. Cheap, and it removes the easy half
# of the race: the scheduler tick overlapping a hand-run
# `python3 -m agents.warehouse_payout`. It does nothing across containers — see
# the concurrency note in the module docstring.
_RUN_LOCK = threading.Lock()


def _order_items(order: dict) -> tuple[list[dict], list[str]]:
    """Airtable order record -> the line-item shape core.shipping understands.

    Deliberately paranoid. `core/manifest.py` reads the same rows with
    `it.get("fields", {})` and tolerates junk; an earlier draft here used
    `it["fields"]` and `int(f["kits"])`, so a single malformed Order Item — a
    blank row, `kits` retyped to text, `supplier_sku` turned into a lookup that
    returns a list — raised out of the batch build and stopped ALL payouts, every
    night, with no alert. One bad row must cost one order, not the whole system.
    """
    out, problems = [], []
    try:
        rows = airtable.get_items_for_order(order) or []
    except Exception as e:
        return [], [f"could not read the order's items: {e}"]
    for it in rows:
        f = (it or {}).get("fields", {}) or {}
        sku = f.get("supplier_sku", "")
        if isinstance(sku, (list, tuple)):          # lookup/rollup field
            sku = sku[0] if sku else ""
        name = str(f.get("product", "") or "?")
        try:
            kits = int(float(f.get("kits") or 0))
        except (TypeError, ValueError):
            # An unreadable quantity must REFUSE the order, not vanish from it —
            # dropping the line would price the order at too few boxes and pay
            # that number confidently. Reported as an explicit problem rather
            # than as a mangled line: `catalog.find` matches on a substring, so a
            # line renamed "[unreadable] Retatrutide" still resolves and prices
            # as though nothing were wrong.
            problems.append(f"{name}: kits is not a number ({f.get('kits')!r})")
            continue
        out.append({"sku": str(sku or ""), "product": name,
                    "spec": str(f.get("spec", "") or ""), "kits": kits})
    return out, problems


def _rows_for(orders: list[dict]) -> list[dict]:
    rows = []
    for o in orders:
        try:
            items, problems = _order_items(o)
        except Exception as e:                       # never let one row stop the night
            items, problems = [], [f"could not be read: {e!r}"]
        rows.append({"id": o["id"],
                     "ref": str(o.get("fields", {}).get("order_ref") or o["id"]),
                     "items": items, "problems": problems})
    return rows


def _alert(subject: str, body: str) -> None:
    """Operator alert. Email, never WhatsApp: a business-initiated WhatsApp only
    delivers inside the recipient's 24h window (error 63016) and this is exactly
    the kind of message that must not be silently swallowed (CLAUDE.md)."""
    try:
        from agents.weekly_report import _send_email
        _send_email(subject, body, [], recipients=settings.operator_emails)
    except Exception as e:
        print(f"[Payout] alert email failed: {e!r}  subject={subject}")


def _send_statement(batch: dict, result: dict, label: str) -> bool:
    """Jason's statement. He should be able to check his own pay without asking."""
    try:
        text = warehouse_fees.statement_text(batch, label)
    except Exception as e:
        # Never let a formatting problem look like a payment problem — the money
        # has already moved by the time this is called.
        print(f"[Payout] statement render failed: {e!r}")
        text = (f"Northline — warehouse payment, {label}\n\n"
                f"{batch['boxes']} box(es) across {batch['orders']} order(s).")
    tx = result.get("tx_hash", "")
    url = tron_payout.explorer_url(tx)
    tail = [f"Paid: {result['amount']:.2f} {result['asset']} to {result['to']}"]
    if result.get("dry_run"):
        tail = ["*** TEST RUN — no payment was actually sent. ***"]
    elif tx:
        tail.append(f"Transaction: {tx}")
        if url:
            tail.append(url)
    tail += ["", "Questions about a box count: reply to Northline, not to this address."]
    try:
        from agents.weekly_report import _send_email
        return _send_email(
            f"Northline warehouse payment — ${batch['total_usd']:.2f} "
            f"({batch['boxes']} boxes, {label})",
            text + "\n" + "\n".join(tail), [],
            recipients=settings.payout_statement_emails, cc=settings.manifest_cc)
    except Exception as e:
        print(f"[Payout] statement email failed: {e!r}")
        return False


def _preconditions() -> list[str]:
    """Everything that must be true before a single order is claimed."""
    problems = []
    if settings.warehouse_fee_per_box_usd <= 0:
        problems.append("WAREHOUSE_FEE_PER_BOX_USD is not a positive number")

    # A misformatted cutoff is worse than a missing one: '2026-9-5' sorts above
    # every real timestamp and pays nobody, forever, silently.
    try:
        airtable.parse_fee_cutoff(settings.warehouse_fee_start_date)
    except ValueError as e:
        problems.append(str(e) + ". Set it to the date this went live.")
    except Exception:
        problems.append(
            "WAREHOUSE_FEE_START_DATE is not set. Without a cutoff the first run "
            "would pay for every paid order already in the base. Set it to the "
            "date this went live (e.g. 2026-09-05).")

    # If nobody can be told, nothing should move. Every safety property in this
    # file downgrades to "an email gets sent"; with no recipient the whole
    # feature runs blind.
    if not settings.operator_emails:
        problems.append("no OPERATOR_EMAIL / REPORT_EMAIL — payout alerts would reach nobody")
    if not settings.payout_statement_emails:
        problems.append("no PAYOUT_STATEMENT_EMAIL / WAREHOUSE_EMAIL — Jason would get no statement")

    try:
        missing = airtable.payout_fields_missing()
    except Exception as e:
        return problems + [f"could not check the Orders table: {e}"]
    if missing:
        problems.append(
            "the Orders table is missing " + ", ".join(missing) +
            ". Add them in Airtable (warehouse_fee_paid = checkbox, "
            "warehouse_fee_usd = number 2dp, warehouse_boxes = number integer, "
            "warehouse_fee_tx = single line text). Until then no payment can be "
            "recorded, so none is sent.")
    return problems + tron_payout.preflight()["problems"]


def _sweep_stuck() -> list[dict]:
    """Orders ticked as paid whose transfer was never recorded.

    A container killed between the claim and the broadcast leaves an order marked
    paid, unpaid, and invisible: `warehouse_fee_paid` is true so no query ever
    returns it again. This is the ONLY thing that reads that state back, so it
    runs first, every night, and says so out loud.
    """
    try:
        stuck = airtable.get_stuck_warehouse_claims()
    except Exception as e:
        print(f"[Payout] stuck sweep failed: {e!r}")
        return []
    if not stuck:
        return []
    total = sum(float(r["fields"].get("warehouse_fee_usd") or 0) for r in stuck)
    _alert(f"⚠️ {len(stuck)} order(s) marked paid with no transaction — ${total:,.2f}",
           "These orders are ticked `warehouse_fee_paid` but carry no transaction "
           "hash, which means the run that claimed them died before (or during) the "
           "transfer. Jason has probably NOT been paid for them, and no query will "
           "ever pick them up again.\n\n"
           "Check the wallet on tronscan. If there is no matching transfer, untick "
           "`warehouse_fee_paid` on these in Airtable and the next run will pay them:\n\n"
           + "\n".join(
               f"  • {r['fields'].get('order_ref', r['id'])}  "
               f"${float(r['fields'].get('warehouse_fee_usd') or 0):.2f}  "
               f"({r['fields'].get('warehouse_fee_tx') or 'no tx'})" for r in stuck))
    return stuck


def run_daily_warehouse_payout() -> dict:
    """One nightly cycle. Returns a summary dict for the scheduler log.

    NEVER RAISES. It runs inside the scheduler thread that also drives the daily
    manifest, the health canary and the payment watcher; an exception escaping
    here would take all of them down until the next deploy.
    """
    try:
        return _run()
    except Exception as e:                                   # pragma: no cover
        print(f"[Payout] UNCAUGHT {e!r}\n{traceback.format_exc()}")
        _alert("⚠️ Warehouse payout crashed",
               f"{e!r}\n\nNo payment was sent.\n\n{traceback.format_exc()}")
        return {"ran": False, "sent": False, "orders": 0, "total_usd": 0.0,
                "error": repr(e)}


def _run() -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        print("[Payout] another payout is already running in this process — skipping")
        return {"ran": False, "sent": False, "orders": 0, "total_usd": 0.0,
                "skipped_reason": "already running"}
    try:
        return _run_locked()
    finally:
        _RUN_LOCK.release()


def _run_locked() -> dict:
    label = datetime.now().strftime("%Y-%m-%d")
    problems = _preconditions()

    if problems:
        msg = ("The nightly warehouse payout did NOT run:\n\n" +
               "\n".join(f"  • {p}" for p in problems) +
               "\n\nNothing was sent and nothing was marked. Jason's orders stay "
               "in the queue and will be paid on the next run once this is fixed.")
        print(f"[Payout] blocked: {problems}")
        try:
            pending = airtable.get_orders_awaiting_warehouse_fee(
                settings.warehouse_fee_start_date)
        except Exception:
            pending = []
        # Only shout when money is actually waiting — otherwise a half-configured
        # system emails every night forever and the alert stops being read.
        if pending:
            _alert(f"⚠️ Warehouse payout blocked — {len(pending)} order(s) waiting", msg)
        return {"ran": False, "sent": False, "orders": 0, "total_usd": 0.0,
                "blocked": problems, "pending": len(pending)}

    _sweep_stuck()

    try:
        orders = airtable.get_orders_awaiting_warehouse_fee(
            settings.warehouse_fee_start_date)
    except Exception as e:
        _alert("⚠️ Warehouse payout — could not read orders", f"{e!r}")
        return {"ran": False, "sent": False, "orders": 0, "total_usd": 0.0,
                "error": repr(e)}

    if not orders:
        print("[Payout] nothing to pay")
        return {"ran": True, "orders": 0, "total_usd": 0.0, "sent": False}

    batch = warehouse_fees.fee_for_batch(_rows_for(orders))

    if batch["skipped"]:
        # Loud, and every time, until someone fixes the catalog entry. These are
        # orders Jason DID pack and is NOT being paid for.
        _alert(f"⚠️ {len(batch['skipped'])} order(s) left out of tonight's warehouse payment",
               "These orders could not be priced, so Jason was not paid for them. "
               "They stay in the queue and will be paid automatically once every "
               "line resolves in the catalog:\n\n" +
               "\n".join(f"  • {r['ref']}: {r['reason']}" for r in batch["skipped"]))

    if not batch["rows"] or batch["total_usd"] <= 0:
        print(f"[Payout] nothing payable ({len(batch['skipped'])} skipped)")
        return {"ran": True, "orders": 0, "skipped": len(batch["skipped"]),
                "total_usd": 0.0, "sent": False}

    # ── DRY RUN: read, price, report. Claim NOTHING. ─────────────────────────
    # A dry run that ticked orders would mark the entire go-live backlog as paid
    # with a fake transaction hash, and no query would ever return them again.
    if settings.payout_dry_run or not settings.payout_private_key:
        why = "PAYOUT_DRY_RUN is on" if settings.payout_dry_run else "no key is configured"
        print(f"[Payout] DRY RUN ({why}): would pay ${batch['total_usd']:.2f} "
              f"for {batch['boxes']} box(es) across {batch['orders']} order(s)")
        _alert(f"Warehouse payout TEST RUN — would have paid ${batch['total_usd']:.2f}",
               f"Nothing was sent and nothing was marked ({why}).\n\n" +
               warehouse_fees.statement_text(batch, label) +
               "\n\nWhen this looks right, set PAYOUT_DRY_RUN=0 to go live.")
        return {"ran": True, "orders": batch["orders"], "boxes": batch["boxes"],
                "total_usd": batch["total_usd"], "sent": False, "dry_run": True,
                "skipped": len(batch["skipped"])}

    # ── CLAIM FIRST ──────────────────────────────────────────────────────────
    # From here on, an order that is ticked is an order nobody will pay again.
    run_token = uuid.uuid4().hex[:12]
    by_id = {r["id"]: r for r in batch["rows"]}
    claimed, claim_failed, already = [], [], []
    for r in batch["rows"]:
        try:
            airtable.claim_warehouse_fee(r["id"], r["boxes"], r["fee_usd"],
                                         run_token=run_token)
            claimed.append(r)
        except AlreadyPaid as e:
            # Another run got there first and the order carries a real hash.
            # Not an error — just not ours to pay.
            print(f"[Payout] {r['ref']} already paid: {e}")
            already.append(r)
        except Exception as e:
            print(f"[Payout] claim failed {r['ref']}: {e!r}")
            claim_failed.append((r, repr(e)))

    if claim_failed:
        # Do NOT pay for orders that could not be claimed — they would be paid
        # again tomorrow. They simply roll over.
        _alert(f"⚠️ {len(claim_failed)} order(s) could not be marked for payment",
               "Airtable rejected the write, so these were LEFT OUT of tonight's "
               "transfer to avoid paying them twice later. They will be picked up "
               "on the next run:\n\n" +
               "\n".join(f"  • {r['ref']}: {err}" for r, err in claim_failed))

    # ── SETTLE, THEN VERIFY OWNERSHIP ────────────────────────────────────────
    # If a second scheduler is running the same batch (rolling deploy, replica
    # count > 1, someone running this by hand), both claimed. Whoever wrote last
    # owns the order; each run pays only what it still owns, so the night is
    # split rather than doubled.
    if claimed:
        time.sleep(CLAIM_SETTLE_SECONDS)
        v = airtable.verify_claims([r["id"] for r in claimed], run_token)
        mine, lost, unreadable = set(v["mine"]), set(v["lost"]), set(v["unreadable"])
        if lost:
            print(f"[Payout] {len(lost)} order(s) claimed by another run — not paying them here")
        if unreadable:
            # NOT the same as losing a race. An Airtable 429 here (N reads straight
            # after N writes, against a 5 req/s limit) would otherwise silently
            # underpay with nobody told until the next night's sweep.
            _alert(f"⚠️ {len(unreadable)} order(s) could not be re-read before payment",
                   "These were marked for payment but could not be verified, so they "
                   "were LEFT OUT of tonight's transfer and are still ticked "
                   "`warehouse_fee_paid`. They will show up in tomorrow's stuck-claim "
                   "sweep; untick them once you have confirmed no transfer went out:\n\n"
                   + "\n".join(f"  • {r['ref']}  ${r['fee_usd']:.2f}"
                                for r in claimed if r["id"] in unreadable))
        claimed = [r for r in claimed if r["id"] in mine]

    if not claimed:
        return {"ran": True, "orders": 0, "total_usd": 0.0, "sent": False,
                "error": "no orders could be claimed"}

    total = round(sum(r["fee_usd"] for r in claimed), 2)
    boxes = sum(r["boxes"] for r in claimed)

    # ── THEN SEND ────────────────────────────────────────────────────────────
    try:
        result = tron_payout.send(total, memo=f"Northline {label} {boxes}box")
    except tron_payout.UncertainBroadcast as e:
        # The money MAY be gone. Never roll back here.
        for r in claimed:
            try:
                airtable.confirm_warehouse_fee(r["id"], e.tx_hash or "UNCERTAIN")
            except Exception:
                pass
        _alert("🚨 Warehouse payout — transfer status UNKNOWN, check the wallet",
               f"{e}\n\nOrders are left MARKED AS PAID so nothing is sent twice.\n"
               f"Amount: ${total:.2f} · Boxes: {boxes} · Orders: {len(claimed)}\n"
               f"Tx: {e.tx_hash or '(none returned)'}\n\n"
               f"If the transfer did NOT go through, untick warehouse_fee_paid on "
               f"these orders in Airtable and they will be paid on the next run:\n" +
               "\n".join(f"  • {r['ref']}  ${r['fee_usd']:.2f}" for r in claimed))
        return {"ran": True, "orders": len(claimed), "boxes": boxes,
                "total_usd": total, "sent": "uncertain", "tx": e.tx_hash}
    except Exception as e:
        # Definitely not sent — release the claims so the next run retries.
        released, stuck = 0, []
        for r in claimed:
            try:
                airtable.release_warehouse_fee(r["id"])
                released += 1
            except Exception as re_:
                stuck.append(f"{r['ref']} ({re_!r})")
        body = (f"The transfer failed and NO money moved:\n\n  {e}\n\n"
                f"Amount: ${total:.2f} · Boxes: {boxes} · Orders: {len(claimed)}\n"
                f"{released} order(s) were returned to the queue and will be paid "
                f"on the next run.\n")
        if stuck:
            body += ("\n⚠️ These could NOT be returned to the queue and are still "
                     "marked paid — untick warehouse_fee_paid on them in Airtable "
                     "or Jason will never be paid for them:\n" +
                     "\n".join(f"  • {s}" for s in stuck))
        _alert("⚠️ Warehouse payout failed — nothing sent", body)
        print(f"[Payout] send failed: {e!r}")
        return {"ran": True, "orders": len(claimed), "boxes": boxes,
                "total_usd": total, "sent": False, "error": str(e)}

    # ── RECORD ───────────────────────────────────────────────────────────────
    tx = result.get("tx_hash", "")
    unrecorded = []
    for r in claimed:
        try:
            airtable.confirm_warehouse_fee(r["id"], tx)
        except Exception as e:
            unrecorded.append(f"{r['ref']} ({e!r})")
    if unrecorded:
        # Harmless for exactly-once (the claim is what prevents a second payment)
        # but it breaks the audit trail AND leaves the order looking stuck to the
        # nightly sweep, so say so now.
        _alert("Warehouse payout sent, but some orders kept their claim marker",
               f"The payment went out (tx {tx}) and these orders are correctly "
               f"marked paid, but the hash could not be written to them. Paste it "
               f"into warehouse_fee_tx by hand:\n" +
               "\n".join(f"  • {u}" for u in unrecorded))

    paid_batch = {**batch, "rows": claimed, "orders": len(claimed),
                  "boxes": boxes, "total_usd": total}
    _send_statement(paid_batch, result, label)
    print(f"[Payout] {len(claimed)} order(s), {boxes} box(es), ${total:.2f}, tx={tx}")
    return {"ran": True, "orders": len(claimed), "boxes": boxes,
            "total_usd": total, "sent": True, "dry_run": False, "tx": tx,
            "skipped": len(batch["skipped"])}


def preview() -> dict:
    """What tonight WOULD pay. Reads only — claims nothing, sends nothing.

    Run this before flipping PAYOUT_DRY_RUN off:
        python3 -m agents.warehouse_payout preview
    """
    orders = airtable.get_orders_awaiting_warehouse_fee(settings.warehouse_fee_start_date)
    batch = warehouse_fees.fee_for_batch(_rows_for(orders))
    try:
        stuck = [{"ref": r["fields"].get("order_ref", r["id"]),
                  "usd": r["fields"].get("warehouse_fee_usd")}
                 for r in airtable.get_stuck_warehouse_claims()]
    except Exception as e:
        stuck = [{"ref": f"(sweep failed: {e})", "usd": None}]
    return {"config": tron_payout.preflight(batch["total_usd"]),
            "statement": warehouse_fees.statement_text(batch, "preview"),
            "orders": batch["orders"], "boxes": batch["boxes"],
            "total_usd": batch["total_usd"],
            "marked_paid_but_no_tx": stuck,
            "skipped": [{"ref": r["ref"], "reason": r["reason"]} for r in batch["skipped"]]}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "preview":
        p = preview()
        print(p["statement"])
        print(json.dumps({k: v for k, v in p.items() if k != "statement"}, indent=2))
    else:
        print(json.dumps(run_daily_warehouse_payout(), indent=2))
