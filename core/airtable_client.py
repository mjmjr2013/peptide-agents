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

    def allocate_unique_amount(self, base_usd: float, coin: str) -> tuple[float, float]:
        """Return (usd_charge, expected_amount). usd_charge = base + a cents tail not
        currently in use among awaiting orders, so each payment maps to one order.
        expected_amount is in the coin's units (USDT≈USD; BTC via live rate)."""
        used = {round(float(o["fields"].get("total_price") or 0), 2)
                for o in self.get_awaiting_orders()}
        charge = round(base_usd, 2)
        for cents in range(1, 100):
            cand = round(float(int(base_usd)) + cents / 100, 2)
            if cand not in used:
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
        matches.sort(key=lambda o: o["fields"].get("created_at", ""), reverse=True)
        return matches[0] if matches else None

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

    def set_order_tracking(self, order_id: str, tracking_number: str) -> dict:
        """Record a tracking number → status 'labeled'. Per the business flow the
        warehouse creates the label FAST (possibly before inventory arrives) as a
        trust signal; actual dispatch is the vial-photo stage ('shipped').
        NOTE: fulfillment_status is a single-select — only pass existing options
        (Airtable rejects the whole update on an unknown option value)."""
        return self._advance_fulfillment(order_id, "labeled", {
            "tracking_number": tracking_number.strip(),
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

    def get_paid_order_awaiting_address_for_phone(self, phone: str) -> dict | None:
        """A paid order for this customer that has no shipping address yet — means we
        were mid address-collection when state was lost (redeploy recovery)."""
        lead = self.find_lead_by_phone(phone)
        if not lead:
            return None
        for o in self.orders.all(formula="AND({payment_status}='paid',{address_line1}='')"):
            if lead["id"] in o["fields"].get("lead_id", []):
                return o
        return None

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
