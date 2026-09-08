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

    # Warehouse — the daily shipping manifest (tracking-page link) is EMAILED here.
    # Comma-separated list allowed. If unset, the manifest falls back to WhatsApp
    # (warehouse_whatsapp below) so the daily ping never silently drops.
    warehouse_emails: list[str] = [
        e.strip() for e in os.environ.get("WAREHOUSE_EMAIL", "").split(",") if e.strip()
    ]

    # CC'd on every daily manifest email to the warehouse (comma-separated) —
    # so the team can see what the rep was asked to do.
    manifest_cc: list[str] = [
        e.strip() for e in os.environ.get("MANIFEST_CC", "").split(",") if e.strip()
    ]

    # Warehouse WhatsApp — fallback transport for the daily manifest when
    # WAREHOUSE_EMAIL is unset. Use the whatsapp: scheme, e.g. "whatsapp:+8613418806654".
    warehouse_whatsapp: str = os.environ.get("WAREHOUSE_WHATSAPP", "")

    # Label factory — white-label artwork + print specs are EMAILED here once an order
    # is paid. Email deliberately, not WhatsApp: the factory is a mainland-China contact
    # and freeform WhatsApp outside the 24h window is silently dropped by Meta (the same
    # failure that lost the July warehouse manifests). Comma-separated list allowed.
    factory_emails: list[str] = [
        e.strip() for e in os.environ.get("FACTORY_EMAIL", "").split(",") if e.strip()
    ]
    # Kept for a human to reach the factory; the agent does NOT send here.
    factory_whatsapp: str = os.environ.get("FACTORY_WHATSAPP", "")

    # Secret token guarding the warehouse tracking page (/manifest?token=...). The
    # daily WhatsApp ping to the warehouse rep includes this link; only this token
    # can view the page or submit tracking numbers.
    manifest_token: str = os.environ.get("MANIFEST_TOKEN", "")

    # Crypto receiving addresses (public). Payments are verified read-only on-chain.
    # USDT is accepted on Ethereum (ERC-20) — your Phantom ETH address receives it.
    eth_address: str = os.environ.get("ETH_ADDRESS", "")
    etherscan_api_key: str = os.environ.get("ETHERSCAN_API_KEY", "")
    btc_address: str = os.environ.get("BTC_ADDRESS", "")

    # ── Warehouse payouts (HANDOFF §32, 2026-09-04) ──────────────────────────
    # Jason is paid per BOX, nightly, in crypto on Tron. The box count is the one
    # `core/shipping.split_packages()` already computes for his manifest, so his
    # pay and his packing instructions can never disagree.
    #
    # ⚠️ PAYOUT_TRON_PRIVATE_KEY is the ONLY secret in this system that can SPEND.
    # Everything else here reads. Use a DEDICATED wallet holding a small float —
    # never the customer receiving address, never a personal wallet. Its balance
    # is the entire blast radius of a bug in core/tron_payout.py.
    # TWO components (Jordan, 2026-09-04): $12 a box PLUS $15.90 a kilo of GROSS
    # weight — product plus the 350 g empty box, one tare per package. The 350 is
    # NOT repeated here: it is `catalog.PACKAGE_TARE_G`, already added to each 
    # package's `gross_g` by the shipping split, so the pay, the packing and the
    # manifest all move together if it ever changes.
    warehouse_fee_per_box_usd: float = float(os.environ.get("WAREHOUSE_FEE_PER_BOX_USD", "12"))
    warehouse_fee_per_kg_usd: float = float(os.environ.get("WAREHOUSE_FEE_PER_KG_USD", "15.90"))

    # Orders PAID before this date are never swept into a payout. Without it, the
    # first nightly run would pay for every paid order in the base at once — a
    # large surprise transfer for work already settled by other means. ISO date,
    # e.g. 2026-09-05. Empty means no cutoff (only sane on a fresh base).
    warehouse_fee_start_date: str = os.environ.get("WAREHOUSE_FEE_START_DATE", "").strip()

    # Jason's Tron wallet. Checked against Tron's own checksum before every send.
    jason_tron_address: str = os.environ.get("JASON_TRON_ADDRESS", "").strip()

    # USDT (dollar-denominated, settles at the calculated number) or TRX (native,
    # converted at send time, so his effective pay moves with the TRX price).
    payout_asset: str = os.environ.get("PAYOUT_ASSET", "USDT").strip().upper()

    # The spending key. Absent => the payout runs but never broadcasts.
    payout_private_key: str = os.environ.get("PAYOUT_TRON_PRIVATE_KEY", "").strip()

    # DEFAULTS TO ON. While on, every path runs — batch, claim, statement, email —
    # and no funds move. Set PAYOUT_DRY_RUN=0 only after a dry night looks right.
    payout_dry_run: bool = os.environ.get("PAYOUT_DRY_RUN", "1").strip().lower() not in ("0", "false", "no")

    # Bug catcher, NOT an approval gate (Jordan, 2026-09-04: no approvals). A night
    # that wants far more than a normal payout is a miscount. Set 0 to disable.
    # Raised from 1500 when the $15.90/kg term was added (2026-09-04): one bulk
    # bac-water order is ~$378 on its own now, so a legitimate night is much
    # larger than it used to be and a low ceiling would block real payments.
    payout_max_usd: float = float(os.environ.get("PAYOUT_MAX_USD", "3000"))

    # TRC20 fee limit in SUN (1 TRX = 1_000_000 SUN). 100 TRX covers a USDT
    # transfer, including to an address that does not hold USDT yet.
    payout_fee_limit_sun: int = int(os.environ.get("PAYOUT_FEE_LIMIT_SUN", "100000000"))

    # Optional TronGrid key — the free anonymous tier is rate-limited and a
    # nightly job that gets a 429 does not pay anybody.
    trongrid_api_key: str = os.environ.get("TRONGRID_API_KEY", "").strip()
    usdt_trc20_contract: str = os.environ.get(
        "USDT_TRC20_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").strip()

    # Where the payout statement is emailed. Jason gets it so he can check his own
    # pay; MANIFEST_CC is copied. Defaults to the warehouse address.
    payout_statement_emails: list[str] = [
        e.strip() for e in os.environ.get("PAYOUT_STATEMENT_EMAIL", "").split(",") if e.strip()
    ] or [e.strip() for e in os.environ.get("WAREHOUSE_EMAIL", "").split(",") if e.strip()]

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

    # Where large-order alerts are EMAILED. Email is the PRIMARY operator transport:
    # freeform WhatsApp only delivers inside the recipient's 24h window (error 63016),
    # so an operator who has not just messaged us would never be told — the alert would
    # throw and be swallowed. Email has no window. Defaults to REPORT_EMAIL so alerts
    # reach the team even if OPERATOR_EMAIL is never set. Comma-separated.
    operator_emails: list[str] = [
        e.strip() for e in os.environ.get("OPERATOR_EMAIL", "").split(",") if e.strip()
    ] or [
        e.strip() for e in (os.environ.get("REPORT_EMAIL", "") or os.environ.get("GMAIL_USER", "")).split(",") if e.strip()
    ]

    # WhatsApp approved message templates (Twilio Content SIDs). WhatsApp blocks
    # freeform business messages sent >24h after the customer's last message
    # (error 63016) — these pre-approved templates are the official mechanism for
    # business-initiated notices like tracking numbers and vial photos.
    tracking_content_sid: str = os.environ.get("TRACKING_CONTENT_SID", "")
    vial_content_sid: str = os.environ.get("VIAL_CONTENT_SID", "")
    payment_content_sid: str = os.environ.get("PAYMENT_CONTENT_SID", "")

    # Cost guardrail — max Claude-generated replies per prospect per day (China day).
    # A real buyer closing an order uses 10–30 messages; past the cap Lily sends a
    # canned time-aware excuse (no Claude call) and ops get an alert email.
    agent_daily_msg_cap: int = int(os.environ.get("AGENT_DAILY_MSG_CAP", "50"))

    # Business
    company_name: str = os.environ.get("COMPANY_NAME", "PeptideCo")


settings = Settings()
