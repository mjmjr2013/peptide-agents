from __future__ import annotations
from pyairtable import Api
from config import settings


class AirtableClient:
    """Thin wrapper around pyairtable with table references."""

    TABLE_LEADS = "Leads"
    TABLE_ORDERS = "Orders"
    TABLE_LABS = "Labs"
    TABLE_CAMPAIGNS = "Campaigns"

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

    def create_order(self, lead_id: str, product: str, quantity_mg: float,
                     total_price: float) -> dict:
        return self.orders.create({
            "lead_id": [lead_id],
            "product": product,
            "quantity_mg": quantity_mg,
            "status": "Pending",
            "total_price": total_price,
        })

    def update_order(self, record_id: str, **fields) -> dict:
        return self.orders.update(record_id, fields)

    def get_order(self, record_id: str) -> dict:
        return self.orders.get(record_id)

    def get_pending_orders(self) -> list[dict]:
        return self.orders.all(formula="{status}='Pending'")

    def get_orders_by_status(self, status: str) -> list[dict]:
        return self.orders.all(formula=f"{{status}}='{status}'")

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
