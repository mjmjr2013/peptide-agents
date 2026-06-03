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

    # SendGrid
    sendgrid_api_key: str = os.environ.get("SENDGRID_API_KEY", "")
    sendgrid_from_email: str = os.environ.get("SENDGRID_FROM_EMAIL", "")

    # Supplier
    supplier_whatsapp: str = os.environ.get("SUPPLIER_WHATSAPP", "")

    # Sales — where to alert when a large order (>100 kits) needs manual handoff.
    # Accepts a plain SMS number (+1...) or a WhatsApp address (whatsapp:+1...).
    handoff_notify_number: str = os.environ.get("HANDOFF_NOTIFY_NUMBER", "")

    # Business
    company_name: str = os.environ.get("COMPANY_NAME", "PeptideCo")


settings = Settings()
