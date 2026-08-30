# Northline peptide-agents — start here

**This is a LIVE production system handling real customers and real money.** A WhatsApp sales agent
("Lily") sells research peptides: it negotiates, takes orders, verifies crypto payment on-chain,
collects shipping addresses, and drives warehouse fulfillment. Mistakes here are visible to paying
customers within seconds.

## Read this first, every session

**`HANDOFF.md` is the canonical state document. Read its header, then the HIGHEST-numbered section
first** — sections are append-only and the newest one is always the most relevant. It records what
is deployed, what broke and why, and what is deliberately left alone.

Do not "fix" something that looks stale without checking HANDOFF for whether it is intentional.
Several records and settings are deliberately in an odd state and documented as such.

## Hard-won rules

- **Consoles lie — verify through the API.** A Twilio sender can read ONLINE with an empty webhook
  and silently drop every inbound. Check the actual resource, not the dashboard.
- **Railway auto-deploy is unreliable.** Force-deploy by commit SHA (HANDOFF §10) and then confirm
  the running commit matches HEAD. Commits have sat undeployed for days unnoticed.
- **WhatsApp freeform messages only deliver inside the recipient's 24h window** (error 63016).
  Outside it you need an approved template. This has caused three separate silent-drop incidents
  (warehouse manifests, operator alerts, tracking notices). If you add any business-initiated
  message, check the window or use email — and never let the failure be swallowed by an `except`.
- **Prefer email for anything operational** (manifests, alerts, reports). It has no window and needs
  no template approval. Gmail SMTP is already wired.
- **Never put Jordan's or Daniel's phone number in `OPERATOR_NUMBERS`.** `_is_operator` is checked
  at the top of `handle_inbound`, before any conversation handling, so a listed number can never
  behave as a customer again — and both lines are needed for test orders. See HANDOFF §28.
- **Airtable is the system of record.** Live agent state (`_conversations`, `_lead_stage`,
  `_pending_payments`) is in-memory and wiped by every deploy; it rebuilds from Airtable on the
  prospect's next inbound (HANDOFF §4a).
- **Payments are matched by AMOUNT on one shared receiving address.** This is inherently
  guess-prone and has misfired three times. The band logic is deliberate — read HANDOFF §5 and §27
  before touching `core/crypto_verify.py`.

## Before you change customer-facing behavior

Message copy, templates, pricing, and the WhatsApp profile are all seen by real buyers. Confirm with
Jordan before changing any of them, even when the change looks obviously correct.

## Secrets

No tokens live in this repo. `.env` is gitignored. Railway tokens are generated per session and
**deleted afterward** (HANDOFF §12). Never commit a token, and never echo one into terminal output.

## Layout

- `agents/` — messaging (the sales agent), payment watcher, weekly/daily reports, health monitor,
  transcript reviewer
- `core/` — Airtable client, crypto verification, pricing, deals, white-label, factory
- `config/settings.py` — every env var, each with a comment explaining why it exists
- `website/` — northlinesupplies.com source (⚠️ hosted on a SECOND Cloudflare account)
- `main.py` — Flask app, routes, and the in-process scheduler loop
