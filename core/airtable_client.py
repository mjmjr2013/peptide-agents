from __future__ import annotations
from pyairtable import Api
from config import settings


class AirtableClient:
    """Thin wrapper around pyairtable with table references."""

    TABLE_LEADS = "Leads"
    TABLE_ORDERS = "Orders"
    TABLE_ORDER_ITEMS = "Order Items"
    TABLE_LABS = "Labs"
    TABLE_CAMPAIGNS = "Campaigns"
    TABLE_MESSAGES = "Messages"

    def __init__(self):
        self.api = Api(settings.airtable_api_key)
        self.base_id = settings.airtable_base_id

    def table(self, name: str):
        return self.api.table(self.base_id, name)

    @property
    def leads(self):
        return self.table(self.TABLE_LEADS)

    @property
    def orders(self):
        return self.table(self.TABLE_ORDERS)

    @property
    def labs(self):
        return self.table(self.TABLE_LABS)

    @property
    def campaigns(self):
        return self.table(self.TABLE_CAMPAIGNS)

    @property
    def order_items(self):
        return self.table(self.TABLE_ORDER_ITEMS)

    @property
    def messages(self):
        return self.table(self.TABLE_MESSAGES)

    # ── Messages (conversation transcript log) ──────────────────────────────

    def log_message(self, phone: str, direction: str, body: str,
                    lead_id: str | None = None) -> None:
        """Append one WhatsApp message to the Messages table so the team can read
        full prospect transcripts in Airtable. Best-effort: never raises into the
        message-handling path (a logging failure must not drop a customer reply)."""
        try:
            fields: dict = {
                "phone": (phone or "").replace("whatsapp:", ""),
                "direction": direction,
                "body": body or "",
                "sent_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
            }
            if lead_id:
                fields["Lead"] = [lead_id]
            self.messages.create(fields)
        except Exception as e:
            print(f"[airtable] log_message failed: {e!r}")

    # ── Leads ──────────────────────────────────────────────────────────────

    def create_lead(self, name: str, email: str, phone: str, buyer_type: str,
                    source: str, campaign_id: str | None = None, notes: str = "") -> dict:
        fields: dict = {
            "Name": name,
            "email": email,
            "phone": phone,
            "buyer_type": buyer_type,
            "source": source,
            "status": "New",
            "notes": notes,
        }
        if campaign_id:
            fields["campaign_id"] = [campaign_id]
        return self.leads.create(fields)

    def update_lead_status(self, record_id: str, status: str, notes: str | None = None) -> dict:
        fields = {"status": status}
        if notes:
            fields["notes"] = notes
        return self.leads.update(record_id, fields)

    def get_lead(self, record_id: str) -> dict:
        return self.leads.get(record_id)

    def find_lead_by_phone(self, phone: str) -> dict | None:
        results = self.leads.all(formula=f"{{phone}}='{phone}'")
        return results[0] if results else None

    # ── Orders ─────────────────────────────────────────────────────────────

    def get_order(self, record_id: str) -> dict:
        return self.orders.get(record_id)

    # ── Fulfillment orders (multi-item, crypto-verified, weekly-batched) ─────

    @staticmethod
    def week_tag(dt=None) -> str:
        """Tag for the current Sun–Sat week, as the ending-Saturday date (report TZ)."""
        from datetime import datetime, timedelta
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.report_timezone)
        except Exception:
            tz = None
        now = dt or datetime.now(tz)
        offset = (5 - now.weekday()) % 7  # weekday: Mon=0..Sun=6; Sat=5
        return (now + timedelta(days=offset)).strftime("%Y-%m-%d")

    def allocate_unique_amount(self, base_usd: float, coin: str,
                               exact: bool = False) -> tuple[float, float]:
        """Return (usd_charge, expected_amount). usd_charge = an amount not currently in
        use among awaiting orders, so each payment maps to one order.
        expected_amount is in the coin's units (USDT≈USD; BTC via live rate).

        exact=False (normal negotiated orders, always whole dollars): append a unique
        cents tail. The dollar base is CEILED so a fractional base can never round the
        customer DOWN — int() used to truncate, which would have quoted $2693.01 on a
        $2693.50 order.
        exact=True (pre-approved fixed-price deals): honour the agreed total to the cent
        and only nudge upward on an actual collision — a price Daniel quoted the customer
        should be the price they are asked to send."""
        import math
        used = {round(float(o["fields"].get("total_price") or 0), 2)
                for o in self.get_awaiting_orders()}
        # Keep every concurrent awaiting charge ≥ $0.10 apart (not just distinct), so an
        # exact payer is always an UNAMBIGUOUS match even when two orders share a base price
        # (the matcher's exact tolerance is $0.05).
        def _clear(c: float) -> bool:
            return all(abs(c - u) >= 0.10 for u in used)
        charge = round(base_usd, 2)
        if exact:
            while not _clear(charge):
                charge = round(charge + 0.01, 2)
        else:
            base_dollars = float(math.ceil(charge))
            charge = round(base_dollars + 0.01, 2)
            for cents in range(1, 100):
                cand = round(base_dollars + cents / 100, 2)
                if _clear(cand):
                    charge = cand
                    break
        if coin.upper() == "BTC":
            from core.crypto_verify import usd_to_btc
            expected = usd_to_btc(charge) or 0.0
        else:
            expected = charge
        return charge, expected

    def create_pending_order(self, lead_id: str, ship_phone: str, items: list[dict],
                             total_usd: float, coin: str, expected_amount: float,
                             order_ref: str, week: str) -> dict:
        """items: [{product, spec, kits, line_total, sku}]. Creates the Order (awaiting
        payment) plus one Order Item row per product."""
        summary = ", ".join(f"{int(i['kits'])}x {i['product']} {i['spec']}".strip() for i in items)
        order = self.orders.create({
            "order_ref": order_ref,
            "lead_id": [lead_id],
            "product": summary,
            "total_price": total_usd,
            "coin": coin.upper(),
            "expected_amount": expected_amount,
            "payment_status": "awaiting",
            "fulfillment_status": "recorded",
            "week_tag": week,
            "ship_phone": ship_phone,
        })
        for it in items:
            self.order_items.create({
                "item": f"{order_ref} · {it['product']} {it['spec']}".strip(),
                "Order": [order["id"]],
                "product": it["product"],
                "spec": it.get("spec", ""),
                "kits": int(it["kits"]),
                "supplier_sku": it.get("sku") or "",
                "line_total": it.get("line_total") or 0,
            })
        return order

    def mark_order_paid(self, order_id: str, tx_hash: str, paid_at_iso: str) -> dict:
        return self.orders.update(order_id, {
            "payment_status": "paid", "tx_hash": tx_hash, "paid_at": paid_at_iso,
        })

    def set_order_shipping(self, order_id: str, **addr) -> dict:
        allowed = {"ship_name", "address_line1", "address_line2", "city",
                   "state_province", "postal_code", "country"}
        return self.orders.update(order_id, {k: v for k, v in addr.items() if k in allowed and v})

    def get_awaiting_orders(self) -> list[dict]:
        return self.orders.all(formula="{payment_status}='awaiting'")

    def get_awaiting_order_for_phone(self, phone: str) -> dict | None:
        """The (most recent) awaiting-payment order for the customer at this phone.
        Used to rebuild in-flight payment state after a redeploy wipes memory."""
        lead = self.find_lead_by_phone(phone)
        if not lead:
            return None
        lid = lead["id"]
        matches = [o for o in self.get_awaiting_orders() if lid in (o["fields"].get("lead_id") or [])]
        matches.sort(key=self._newest_first, reverse=True)
        return matches[0] if matches else None

    @staticmethod
    def _newest_first(record: dict) -> str:
        """Sort key for "the most recent of these orders".

        `created_at` is in the Airtable schema but NOTHING EVER WRITES IT — the
        field is empty on every order, so the two sorts below were sorting on ""
        and returning whatever Airtable happened to list first rather than the
        newest. That matters here: both callers pick an order to recover payment
        state onto or to supersede, and picking the wrong one of a customer's two
        awaiting orders is the kind of mistake §5/§27 exist to prevent.

        Airtable stamps every record with `createdTime` at the top level, so it is
        always available and needs no schema change. `paid_at` wins where present
        because that is the business event.
        """
        f = record.get("fields", {})
        return f.get("created_at") or f.get("paid_at") or record.get("createdTime") or ""

    def get_paid_orders_for_week(self, week: str) -> list[dict]:
        return self.orders.all(formula=f"AND({{payment_status}}='paid',{{week_tag}}='{week}')")

    def get_unbulked_paid_orders(self) -> list[dict]:
        """Paid orders not yet rolled into a supplier bulk order (weekly cadence)."""
        return self.orders.all(formula="AND({payment_status}='paid',NOT({bulk_ordered}))")

    def get_unmanifested_paid_orders(self) -> list[dict]:
        """Paid orders not yet sent to the warehouse on a manifest (daily cadence)."""
        return self.orders.all(formula="AND({payment_status}='paid',NOT({manifested}))")

    # Orders flagged `legacy_warehouse` predate the Jason handoff and are being
    # finished by the previous rep off-system, so they stay off his manifest and
    # daily email. Pass include_legacy=True to see them anyway (see /manifest?legacy=1).
    _NOT_LEGACY = "NOT({legacy_warehouse})"

    def is_promo_redeemed(self, code: str) -> bool:
        """True if a one-time deal code has already been used up. Redemption is derived
        from Airtable rather than tracked in memory — a code counts as spent once an
        order carrying it reaches 'paid' — so it survives redeploys and can't be
        resurrected by the awaiting-order recovery path."""
        code = (code or "").strip().upper()
        if not code:
            return False
        rows = self.orders.all(
            formula=f"AND({{promo_code}}='{code}',{{payment_status}}='paid')")
        return bool(rows)

    def get_open_promo_order(self, code: str) -> dict | None:
        """An awaiting (unpaid) order already placed under this code, if any. Used to
        supersede a stale order when the customer re-places — same guard the
        renegotiation path uses so old unique amounts can't cross-match."""
        code = (code or "").strip().upper()
        rows = self.orders.all(
            formula=f"AND({{promo_code}}='{code}',{{payment_status}}='awaiting')")
        rows.sort(key=self._newest_first, reverse=True)
        return rows[0] if rows else None

    def get_orders_needing_tracking(self, include_legacy: bool = False) -> list[dict]:
        """Paid orders the warehouse still has to enter a tracking number for."""
        clauses = ["{payment_status}='paid'", "NOT({tracking_sent})"]
        if not include_legacy:
            clauses.append(self._NOT_LEGACY)
        return self.orders.all(formula=f"AND({','.join(clauses)})")

    # Fulfillment lifecycle: recorded → in_bulk_order → labeled → shipped.
    # Stages can interleave (tracking may precede the weekly bulk; the vial photo
    # may precede tracking), so status only ever advances — never regresses.
    _FULFILLMENT_RANK = {"recorded": 0, "in_bulk_order": 1, "labeled": 2, "shipped": 3}

    def _advance_fulfillment(self, order_id: str, new_status: str, extra: dict) -> dict:
        fields = dict(extra)
        try:
            cur = self.get_order(order_id)["fields"].get("fulfillment_status", "recorded")
            if self._FULFILLMENT_RANK.get(new_status, 0) > self._FULFILLMENT_RANK.get(cur, 0):
                fields["fulfillment_status"] = new_status
        except Exception:
            fields["fulfillment_status"] = new_status  # best effort
        return self.orders.update(order_id, fields)

    def set_order_tracking(self, order_id: str, tracking_number: str,
                           complete: bool = True) -> dict:
        """Record a tracking number → status 'labeled'. Per the business flow the
        warehouse creates the label FAST (possibly before inventory arrives) as a
        trust signal; actual dispatch is the vial-photo stage ('shipped').
        NOTE: fulfillment_status is a single-select — only pass existing options
        (Airtable rejects the whole update on an unknown option value).

        `complete=False` records progress on a MULTI-PACKAGE order without
        finishing it: the numbers so far are saved, but `tracking_sent` stays
        false so the order remains on the manifest and the customer is not told
        the shipment is booked. Defaults to True, so every existing caller and
        every single-parcel order behaves exactly as before."""
        value = (tracking_number or "").strip()
        if not complete:
            return self.orders.update(order_id, {"tracking_number": value})
        return self._advance_fulfillment(order_id, "labeled", {
            "tracking_number": value,
            "tracking_sent": True,
        })

    def get_orders_needing_fulfillment(self, include_legacy: bool = False) -> list[dict]:
        """Paid orders with warehouse work outstanding: tracking number not yet
        entered OR vial photo not yet sent. Drives the /manifest page + daily email."""
        clauses = ["{payment_status}='paid'",
                   "OR(NOT({tracking_sent}),NOT({vial_photo_sent}))"]
        if not include_legacy:
            clauses.append(self._NOT_LEGACY)
        return self.orders.all(formula=f"AND({','.join(clauses)})")

    def attach_vial_photo(self, order_id: str, content: bytes, filename: str,
                          content_type: str = "image/jpeg") -> str:
        """Upload a vial photo to the order's `vial_photo` attachment field (permanent
        record) and return the Airtable-hosted URL — public long enough for Twilio to
        fetch it as WhatsApp media (send immediately; the URLs expire after ~2h)."""
        resp = self.orders.upload_attachment(order_id, "vial_photo", filename, content,
                                             content_type=content_type)
        atts = next(iter(resp.get("fields", {}).values()), [])
        return atts[-1]["url"] if atts else ""

    def mark_vial_photo_sent(self, order_id: str) -> dict:
        """Vial photo sent = final step right before physical dispatch → 'shipped'."""
        return self._advance_fulfillment(order_id, "shipped", {"vial_photo_sent": True})

    def get_recent_messages_for_phone(self, phone: str, limit: int = 30) -> list[dict]:
        """Chronological transcript rows for one prospect (for conversation rebuild
        after a redeploy). phone may include the whatsapp: prefix; rows store it bare."""
        bare = (phone or "").replace("whatsapp:", "")
        try:
            rows = self.messages.all(formula=f"{{phone}}='{bare}'")
        except Exception as e:
            print(f"[airtable] transcript fetch failed for {bare}: {e}")
            return []
        rows.sort(key=lambda r: r["fields"].get("sent_at", ""))
        return rows[-limit:]

    # The warehouse cannot print a label or take the §16 photo without a NAME, so
    # a nameless order is just as unshippable as an addressless one.
    REQUIRED_SHIP_FIELDS = ("ship_name", "address_line1", "city", "country")

    def get_paid_order_awaiting_address_for_phone(self, phone: str) -> dict | None:
        """A paid, unshipped order for this customer that is still missing any
        required shipping field — we were mid address-collection when state was
        lost (redeploy recovery).

        This used to test `address_line1=''` only, so an order that had a street
        but NO name could not be recovered: after a deploy the customer's reply
        with their name was treated as ordinary chat and the name was lost
        (HANDOFF §30k). Already-shipped orders are excluded — otherwise a customer
        whose old order is missing a field would have their next message swallowed
        by address collection.
        """
        lead = self.find_lead_by_phone(phone)
        if not lead:
            return None
        candidates = []
        for o in self.orders.all(formula="{payment_status}='paid'"):
            f = o["fields"]
            if lead["id"] not in (f.get("lead_id") or []):
                continue
            if f.get("tracking_sent") or f.get("tracking_number"):
                continue                      # already gone out; do not reopen it
            if any(not (f.get(k) or "").strip() for k in self.REQUIRED_SHIP_FIELDS):
                candidates.append(o)
        candidates.sort(key=self._newest_first, reverse=True)
        return candidates[0] if candidates else None

    def get_lead_phone_for_order(self, order_record: dict) -> str:
        """The customer's WhatsApp/phone (from the linked Lead) to send tracking to."""
        ids = order_record["fields"].get("lead_id", [])
        if not ids:
            return ""
        try:
            return (self.get_lead(ids[0])["fields"].get("phone") or "").strip()
        except Exception:
            return ""

    def mark_bulk_ordered(self, order_ids: list[str]) -> None:
        for oid in order_ids:
            try:
                self._advance_fulfillment(oid, "in_bulk_order", {"bulk_ordered": True})
            except Exception as e:
                print(f"[airtable] mark_bulk_ordered {oid} failed: {e}")

    def mark_manifested(self, order_ids: list[str]) -> None:
        for oid in order_ids:
            try:
                self.orders.update(oid, {"manifested": True})
            except Exception as e:
                print(f"[airtable] mark_manifested {oid} failed: {e}")

    def get_items_for_order(self, order_record: dict) -> list[dict]:
        ids = order_record["fields"].get("Order Items", [])
        out = []
        for rid in ids:
            try:
                out.append(self.order_items.get(rid))
            except Exception:
                pass
        return out

    # ── Labs ───────────────────────────────────────────────────────────────

    def get_active_labs(self) -> list[dict]:
        return self.labs.all(formula="{active}=1")

    def get_lab(self, record_id: str) -> dict:
        return self.labs.get(record_id)

    # ── Campaigns ──────────────────────────────────────────────────────────

    def create_campaign(self, meta_campaign_id: str, account_id: str,
                        creative_variant: str) -> dict:
        return self.campaigns.create({
            "meta_campaign_id": meta_campaign_id,
            "account_id": account_id,
            "status": "Active",
            "creative_variant": creative_variant,
            "appeal_status": "Not needed",
        })

    def update_campaign(self, record_id: str, **fields) -> dict:
        return self.campaigns.update(record_id, fields)

    def get_disapproved_campaigns(self) -> list[dict]:
        return self.campaigns.all(formula="{status}='Disapproved'")

    def get_active_campaigns(self) -> list[dict]:
        return self.campaigns.all(formula="{status}='Active'")


airtable = AirtableClient()
