import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)


class Settings:
    # Anthropic
    anthropic_api_key: str = os.environ["ANTHROPIC_API_KEY"]
    claude_model: str = "claude-opus-4-7"

    # Airtable
    airtable_api_key: str = os.environ.get("AIRTABLE_API_KEY", "")
    airtable_base_id: str = os.environ.get("AIRTABLE_BASE_ID", "")

    # Meta Ads
    meta_app_id: str = os.environ.get("META_APP_ID", "")
    meta_app_secret: str = os.environ.get("META_APP_SECRET", "")
    meta_access_token: str = os.environ.get("META_ACCESS_TOKEN", "")
    meta_business_manager_id: str = os.environ.get("META_BUSINESS_MANAGER_ID", "")
    meta_ad_account_id: str = os.environ.get("META_AD_ACCOUNT_ID", "")   # without "act_" prefix
    meta_page_id: str = os.environ.get("META_PAGE_ID", "")               # Facebook Page ID

    # Railway
    railway_public_url: str = os.environ.get("RAILWAY_PUBLIC_URL", "")

    # Twilio
    twilio_account_sid: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_phone_number: str = os.environ.get("TWILIO_PHONE_NUMBER", "")
    twilio_whatsapp_from: str = os.environ.get("TWILIO_WHATSAPP_FROM", "")

    # Email (Gmail SMTP via Google Workspace). GMAIL_USER is the full address the
    # report is sent FROM (e.g. jordan@northlinesupplies.com); GMAIL_APP_PASSWORD is
    # a 16-char Google App Password (account → Security → App passwords).
    gmail_user: str = os.environ.get("GMAIL_USER", "")
    gmail_app_password: str = os.environ.get("GMAIL_APP_PASSWORD", "")

    # Supplier
    supplier_whatsapp: str = os.environ.get("SUPPLIER_WHATSAPP", "")

    # Warehouse — the daily shipping manifest is sent here over WhatsApp (not email).
    # Use the whatsapp: scheme, e.g. "whatsapp:+8613418806654".
    warehouse_whatsapp: str = os.environ.get("WAREHOUSE_WHATSAPP", "")

    # Secret token guarding the warehouse tracking page (/manifest?token=...). The
    # daily WhatsApp ping to the warehouse rep includes this link; only this token
    # can view the page or submit tracking numbers.
    manifest_token: str = os.environ.get("MANIFEST_TOKEN", "")

    # Crypto receiving addresses (public). Payments are verified read-only on-chain.
    # USDT is accepted on Ethereum (ERC-20) — your Phantom ETH address receives it.
    eth_address: str = os.environ.get("ETH_ADDRESS", "")
    etherscan_api_key: str = os.environ.get("ETHERSCAN_API_KEY", "")
    btc_address: str = os.environ.get("BTC_ADDRESS", "")

    # Weekly fulfillment reports. REPORT_EMAIL may be a comma-separated list.
    report_emails: list[str] = [
        e.strip() for e in (os.environ.get("REPORT_EMAIL", "") or os.environ.get("GMAIL_USER", "")).split(",") if e.strip()
    ]
    report_timezone: str = os.environ.get("REPORT_TIMEZONE", "America/Denver")

    # Sales — where to alert when a large order (>100 kits) needs manual handoff.
    # Accepts a plain SMS number (+1...) or a WhatsApp address (whatsapp:+1...).
    handoff_notify_number: str = os.environ.get("HANDOFF_NOTIFY_NUMBER", "")

    # Operators — the human(s) who supervise large orders (>100 kits). Inbound
    # messages from these numbers are treated as control/relay commands, NOT as
    # prospect messages. Large-order alerts go to all of them, and their replies
    # are relayed (auto-phrased in persona) back to the prospect in the same
    # WhatsApp thread. Comma-separated. Use the whatsapp: scheme if the bot runs
    # on WhatsApp, e.g. "whatsapp:+14805551234,whatsapp:+14806265678". Matching
    # against inbound is by the last 10 digits, so the scheme is optional for
    # detection but required for outbound delivery on WhatsApp.
    operator_numbers: list[str] = [
        n.strip() for n in os.environ.get("OPERATOR_NUMBERS", "").split(",") if n.strip()
    ]

    # Business
    company_name: str = os.environ.get("COMPANY_NAME", "PeptideCo")


settings = Settings()
