"""
One-time script to create the Airtable base schema via the Airtable Meta API.
Run once after setting AIRTABLE_API_KEY in your .env.

The Airtable Meta API creates tables with fields.
After running, copy the printed base ID into AIRTABLE_BASE_ID in .env.
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AIRTABLE_API_KEY"]
# Set this to your Airtable workspace/organization ID
# Found at: https://airtable.com/account → Workspace ID
WORKSPACE_ID = os.environ.get("AIRTABLE_WORKSPACE_ID", "")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

BASE_URL = "https://api.airtable.com/v0/meta"


def create_base() -> str:
    """Create a new Airtable base and return its ID."""
    resp = requests.post(
        f"{BASE_URL}/bases",
        headers=HEADERS,
        json={
            "name": "Peptide Research Supply — CRM",
            "workspaceId": WORKSPACE_ID,
            "tables": [
                # Minimal initial table (Airtable requires at least one)
                {"name": "Leads", "fields": [{"name": "Name", "type": "singleLineText"}]}
            ],
        },
    )
    resp.raise_for_status()
    data = resp.json()
    base_id = data["id"]
    print(f"✅ Base created: {base_id}")
    return base_id


def get_table_id(base_id: str, table_name: str) -> str:
    resp = requests.get(f"{BASE_URL}/bases/{base_id}/tables", headers=HEADERS)
    resp.raise_for_status()
    for t in resp.json()["tables"]:
        if t["name"] == table_name:
            return t["id"]
    raise ValueError(f"Table {table_name!r} not found")


def create_table(base_id: str, table_def: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/bases/{base_id}/tables",
        headers=HEADERS,
        json=table_def,
    )
    resp.raise_for_status()
    return resp.json()


def update_table_fields(base_id: str, table_id: str, fields: list[dict]):
    """Add fields to an existing table."""
    for field in fields:
        resp = requests.post(
            f"{BASE_URL}/bases/{base_id}/tables/{table_id}/fields",
            headers=HEADERS,
            json=field,
        )
        if resp.status_code not in (200, 201):
            print(f"  ⚠️  Field {field['name']!r}: {resp.text}")
        else:
            print(f"  ✅ Field created: {field['name']}")


def setup_leads_table(base_id: str):
    """Create the Leads table."""
    print("\n📋 Creating Leads table...")
    table = create_table(base_id, {"name": "Leads", "fields": [{"name": "Name", "type": "singleLineText"}]})
    table_id = table["id"]
    print(f"  Table ID: {table_id}")

    fields = [
        {"name": "email", "type": "email"},
        {"name": "phone", "type": "phoneNumber"},
        {
            "name": "buyer_type",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "Research lab"},
                    {"name": "Distributor"},
                    {"name": "Individual"},
                ]
            },
        },
        {
            "name": "source",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "Meta ad"},
                    {"name": "Direct"},
                    {"name": "Referral"},
                ]
            },
        },
        {
            "name": "status",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "New"},
                    {"name": "Contacted"},
                    {"name": "Qualified"},
                    {"name": "Converted"},
                    {"name": "Dead"},
                ]
            },
        },
        {"name": "notes", "type": "multilineText"},
        {"name": "created_at", "type": "dateTime", "options": {"timeZone": "America/New_York", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}},
    ]
    update_table_fields(base_id, table_id, fields)
    return table_id


def setup_campaigns_table(base_id: str) -> str:
    print("\n📋 Creating Campaigns table...")
    table = create_table(base_id, {"name": "Campaigns", "fields": [{"name": "meta_campaign_id", "type": "singleLineText"}]})
    table_id = table["id"]

    fields = [
        {"name": "account_id", "type": "singleLineText"},
        {
            "name": "status",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "Active"},
                    {"name": "Disapproved"},
                    {"name": "Appealing"},
                    {"name": "Paused"},
                ]
            },
        },
        {"name": "creative_variant", "type": "singleLineText"},
        {
            "name": "appeal_status",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "Not needed"},
                    {"name": "Submitted"},
                    {"name": "Won"},
                    {"name": "Lost"},
                ]
            },
        },
        {"name": "launched_at", "type": "dateTime", "options": {"timeZone": "America/New_York", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}},
        {"name": "pulled_at", "type": "dateTime", "options": {"timeZone": "America/New_York", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}},
    ]
    update_table_fields(base_id, table_id, fields)
    print(f"  ✅ Campaigns table: {table_id}")
    return table_id


def setup_labs_table(base_id: str) -> str:
    print("\n📋 Creating Labs table...")
    table = create_table(base_id, {"name": "Labs", "fields": [{"name": "name", "type": "singleLineText"}]})
    table_id = table["id"]

    fields = [
        {"name": "email", "type": "email"},
        {"name": "products", "type": "multilineText"},
        {"name": "lead_time_days", "type": "number", "options": {"precision": 0}},
        {"name": "active", "type": "checkbox", "options": {"color": "greenBright", "icon": "check"}},
    ]
    update_table_fields(base_id, table_id, fields)
    print(f"  ✅ Labs table: {table_id}")
    return table_id


def setup_orders_table(base_id: str, leads_table_id: str, labs_table_id: str) -> str:
    print("\n📋 Creating Orders table...")
    table = create_table(base_id, {"name": "Orders", "fields": [{"name": "product", "type": "singleLineText"}]})
    table_id = table["id"]

    fields = [
        {
            "name": "lead_id",
            "type": "multipleRecordLinks",
            "options": {"linkedTableId": leads_table_id},
        },
        {"name": "quantity_mg", "type": "number", "options": {"precision": 2}},
        {
            "name": "status",
            "type": "singleSelect",
            "options": {
                "choices": [
                    {"name": "Pending"},
                    {"name": "Sent to lab"},
                    {"name": "In production"},
                    {"name": "Shipped"},
                    {"name": "Delivered"},
                ]
            },
        },
        {
            "name": "lab_id",
            "type": "multipleRecordLinks",
            "options": {"linkedTableId": labs_table_id},
        },
        {"name": "tracking_number", "type": "singleLineText"},
        {"name": "total_price", "type": "currency", "options": {"precision": 2, "symbol": "$"}},
        {"name": "created_at", "type": "dateTime", "options": {"timeZone": "America/New_York", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}},
        {"name": "updated_at", "type": "dateTime", "options": {"timeZone": "America/New_York", "dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}}},
    ]
    update_table_fields(base_id, table_id, fields)
    print(f"  ✅ Orders table: {table_id}")
    return table_id


def add_campaign_link_to_leads(base_id: str, leads_table_id: str, campaigns_table_id: str):
    print("\n🔗 Adding campaign_id link to Leads...")
    field = {
        "name": "campaign_id",
        "type": "multipleRecordLinks",
        "options": {"linkedTableId": campaigns_table_id},
    }
    resp = requests.post(
        f"{BASE_URL}/bases/{base_id}/tables/{leads_table_id}/fields",
        headers=HEADERS,
        json=field,
    )
    if resp.status_code not in (200, 201):
        print(f"  ⚠️  {resp.text}")
    else:
        print("  ✅ campaign_id linked")


def main():
    base_id = os.environ.get("AIRTABLE_BASE_ID", "")
    if not base_id:
        print("❌ Set AIRTABLE_BASE_ID in .env first.")
        return

    print("🚀 Setting up Airtable tables for Peptide Research Supply...\n")
    print(f"Using existing base: {base_id}")

    leads_id = setup_leads_table(base_id)
    campaigns_id = setup_campaigns_table(base_id)
    labs_id = setup_labs_table(base_id)
    setup_orders_table(base_id, leads_id, labs_id)
    add_campaign_link_to_leads(base_id, leads_id, campaigns_id)

    print(f"""
✅ Airtable base setup complete!

Add these to your .env:

AIRTABLE_BASE_ID={base_id}

View your base at: https://airtable.com/{base_id}
""")


if __name__ == "__main__":
    main()
