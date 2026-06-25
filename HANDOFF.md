# Northline Group — Agent System Handoff

Paste this into a fresh Claude Code session (run from `~/peptide-agents`) to continue.
It describes the live WhatsApp sales agent, the new order/payment/fulfillment system,
how to deploy/debug, and what's outstanding. No secret tokens are stored here.

Last updated after building the order-intake + crypto-payment + fulfillment-reports system.
The order system is committed on `main` and pushed for backup, but **NOT deployed** (prod still
runs the prior build) — see §13/§14.

---

## 1. What this is
Northline Group LLC ("Northline Supplies") runs an automated **WhatsApp sales agent** for
research peptides. A prospect WhatsApps the business; an AI agent (Claude) greets them, sends
a price list, quotes/negotiates within limits, **takes the order, verifies crypto payment
on-chain, collects the shipping address, and records everything**. Fulfillment reports then go
out **daily** (warehouse) and **weekly** (supplier). Ads drive prospects to the HK number.

## 2. The live agent
- The `peptide-agents` Flask app on **Railway**. Repo `mjmjr2013/peptide-agents` (branch `main`);
  local `~/peptide-agents`; URL `https://peptide-agents-production.up.railway.app`.
- Runs in **webhook mode** in prod (Flask server + in-process schedulers; the ad/leadgen/tracking
  background loops are not started in prod).
- ⚠️ `~/Downloads/northline-agent` (Node) is **NOT live** — ignore it.

## 3. Phone numbers (all WhatsApp; SMS unused)
- **+85292909474 (HK)** — the live ad number; inbound + outbound from-number. Sender `XE42b164026f3bbf3bd190502b0ba2c997`.
- +15014178514 (US) — legacy sender, online, not the from-number. Sender `XE0da4554ade73310eb6cacbdb0456639d`.
- +18774692290 (toll-free) — unused.
- Inbound routing is per-**WhatsApp-sender** webhook (NOT the number's `sms_url`), pointing to `…/sms`.
  Outbound media uses env `TWILIO_WHATSAPP_FROM` = `whatsapp:+85292909474`.

## 4. Order → payment → address flow (`agents/messaging_agent.py`)
The agent negotiates, then on Claude action `place`:
1. Validates line items (per-item floor/cap clamp), computes total = items + shipping
   (std $95 / free >$1000 / expedited $235).
2. Allocates a **unique cents amount** (so each payment maps to one order), creates a **pending
   order** (payment_status=`awaiting`) + Order Items, sets stage `awaiting_payment`, and sends
   code-generated **payment instructions** (exact amount + wallet address). Claude does NOT give
   the wallet/amount.
3. Customer pays, then messages → agent **verifies on-chain** (`core/crypto_verify.py`). If found,
   marks order `paid`, stage `awaiting_address`, asks for shipping details.
4. Customer sends address → Claude parses to structured fields → `set_order_shipping` → confirm.
Every order is recorded at step 2 (awaiting); only **paid** orders flow into fulfillment reports.
Conversation/stage state is in-memory (resets on redeploy). `RESET` from a contact clears their state.
Large orders >100 kits below cap → operator relay (`OPERATOR_NUMBERS`, currently unset).

## 5. Crypto payment verification (`core/crypto_verify.py`) — READ-ONLY, no keys held
- **USDT = Ethereum (ERC-20)**, verified via **Etherscan API** (`tokentx`; needs `ETHERSCAN_API_KEY`).
  Contract `0xdac17…ec7`, 6 decimals. (Public-RPC `eth_getLogs` is range-capped/403s — Etherscan is the reliable path.)
  NOTE: ERC-20 gas is paid by the customer (~$3–25). Solana (~$0.0005) and Tron (~$1) verifiers were
  also built earlier and are in git history if you want to switch back — Solana/Tron are far cheaper for buyers.
- **BTC**, verified via **mempool.space** (no key); USD→BTC rate locked at quote (`usd_to_btc`), 1 confirmation.
- Matching is by **unique amount** (USDT exact; BTC quoted amount + ~1.5% tolerance). All tested against live chains.

## 6. Airtable data model (system of record)
Base `apprMJI8obXHOLvJU`. Tables: Leads, Campaigns, Labs, **Orders**, **Order Items** (new).
- **Orders** (one row per purchase): `order_ref`, `lead_id`(link), `product`(summary), `total_price`,
  `coin`, `expected_amount`, `payment_status`(awaiting/paid/failed), `tx_hash`, `paid_at`, `week_tag`,
  `ship_name`/`address_line1`/`address_line2`/`city`/`state_province`/`postal_code`/`country`/`ship_phone`,
  `fulfillment_status`, and two cadence flags: **`bulk_ordered`** (checkbox) and **`manifested`** (checkbox).
- **Order Items** (one row per product, linked to Orders): `item`, `Order`(link), `product`, `spec`, `kits`,
  `supplier_sku`, `line_total`.
- The Airtable PAT can create fields/tables via the metadata API (used to build the above), but **cannot add
  new single-select options** → use existing option values (e.g. lead `source="Direct"`, not "WhatsApp").

## 7. Fulfillment reports (`agents/weekly_report.py`) + scheduler (`main.py`)
Two independent cadences, two audiences, generated from paid orders; flag-based so each order is
processed once per cadence:
- **DAILY warehouse manifest** — `run_daily_manifest()`: paid orders where `manifested`=false → per-order
  name+address+items (NO costs/supplier) → email → set `manifested`. Fires **daily at `DAILY_MANIFEST_HOUR`
  (default 07:00 Mountain)**. Purpose: warehouse makes labels + sends tracking fast.
- **WEEKLY supplier bulk** — `run_supplier_bulk()`: paid orders where `bulk_ordered`=false → aggregate kits
  per SKU (NO names/addresses/prices) → email → set `bulk_ordered`. Fires **Sunday 00:00 Mountain** (week =
  Sun 00:00 → Sat 23:59; last order Sat 11:59pm).
- Delivery: **email via SendGrid** to `REPORT_EMAIL` (defaults to `SENDGRID_FROM_EMAIL`). Needs `SENDGRID_API_KEY`.
- Scheduler runs in-process in the webhook app (`run_report_scheduler`, checks every 5 min, guarded once/day & once/week).
- One-shot CLI: `python main.py daily` | `python main.py weekly` | `python main.py report <week-tag>` (preview, no marking).

## 8. Pricing (`core/pricing.py` + `core/price_image.py`)
Source of truth = the price-list image `CATEGORIES` (whole dollars). `get_list_price`/`get_catalog_text`
resolve to the image; floor = ceil(3×cost); discount caps <25:5% / 25–49:10% / 50+:15%. Whole dollars only.
Sheet sent to customers = committed `static/price_list.xlsx` (regenerate after price edits, see §10).
NAD+ is a manual loss-leader: $55 / $135 / $195 (3–4× cost). Audit: catalog markup ~5.6× median.
⚠️ Wallet/crypto receiving addresses are **not yet set** (placeholders) — see §14.

## 9. Persona / tone
Warm kind Chinese-woman agent, frequent "dear"; greets on first contact; offers choices as
"which would you like, dear?"; positive payment framing; never reveals discount % or tiers; no promo in greeting.

## 10. Deployment (Railway) — auto-deploy is FLAKY
1. Commit, `git push origin main`. If prices changed, regenerate sheet into `static/` and commit it:
   `RAILWAY_ENVIRONMENT=1 python3 -c "from core.price_image import generate_price_list_xlsx, generate_price_list_pdf; generate_price_list_xlsx(); generate_price_list_pdf()"`
2. **Force-deploy by commit SHA** (auto-deploy misses or redeploys stale): GraphQL `serviceInstanceDeploy(serviceId, environmentId, commitSha)`.
3. Poll `deployments(first:1,…)` until `SUCCESS` + matching `meta.commitHash`; check `/health`.
Railway IDs — project `c3856be2-a3fa-4184-a096-7f8f36f6e762`, service `4336f9e6-3908-48b5-aa67-4daaf7611c8b`, env `6ef277aa-0bc4-4a79-87c0-34d1af9f0c5c`.

## 11. Verify / debug
- Twilio REST (Account SID + Auth Token): Messages, Calls/Recordings (read voice-verification codes),
  `monitor.twilio.com/v1/Alerts`, `messaging.twilio.com/v2/Channels/Senders/<sid>` (GET/POST by SID).
- Railway GraphQL (needs token): env vars (`variables`/`variableUpsert`), deploy, `deploymentLogs`.
- Airtable metadata API for schema; data via pyairtable.

## 12. Credentials & IDs (secrets NOT stored here)
- Twilio Account SID + Auth Token (master cred, also used by app) — both from Console → Account Info; **don't rotate** the auth token.
- Railway token — generate fresh per session, delete after.
- GitHub PAT — embedded in local git remote (push works), **should be rotated**.
- Airtable PAT `pat…` (in Railway `AIRTABLE_API_KEY`); base `apprMJI8obXHOLvJU`.
- WABA id `1010468724997939`; HK regulatory bundle `BUad64de52410298f0c0252f7c651b9534`.

## 13. Current state (built + tested locally, COMMITTED, NOT deployed)
All of §4–§7 is implemented, unit/integration-tested against live Airtable + live chains, committed on
`main` (pushed for backup) but **not deployed** — the live agent still runs the prior (pre-order-system) code.
Reason: going live needs the env vars in §14, or customers would be told to pay to an unset wallet. Airtable
schema (Orders fields + Order Items table + flags) IS already created in the live base.
Decommissioned the stale `order_intake_agent` + supplier-leaking `fulfillment_agent`.

## 14. Open items / TODO
**To go live (then deploy + run a $2–3 live USDT test):**
- `ETH_ADDRESS` — Phantom Ethereum `0x…` address (USDT-ERC20 received here).
- `ETHERSCAN_API_KEY` — free (etherscan.io → API Keys); required to verify USDT.
- `SENDGRID_API_KEY` — for daily/weekly report emails (only `SENDGRID_FROM_EMAIL` is set).
- **BTC?** decide: provide Phantom BTC address (`BTC_ADDRESS`) or go USDT-only (then drop the coin-choice step in the prompt).
- Confirm `REPORT_EMAIL` (default = `SENDGRID_FROM_EMAIL`; likely jordan@northlinesupplies.com) and `DAILY_MANIFEST_HOUR`/timezone (default 07:00 Mountain) + the warehouse's timezone.
- `OPERATOR_NUMBERS` (optional) — so "order ready / large order" alerts reach a phone.
**Other standing items:**
- Twilio HK **regulatory bundle** = `pending-review` (WhatsApp unaffected; SMS/voice gated until approved).
- Cleanups: delete the Railway token used; rotate the GitHub PAT; delete the Cloudflare API token.

## 15. Gotchas (hard-won)
- Consoles lie — verify via API (sender ONLINE w/ empty webhook silently drops inbound; bundle "submitted" while Draft).
- iPhone photos are HEIC even when named `.jpeg` (silently rejected) → `sips -s format jpeg in.jpeg --out out.jpg`.
- VoIP numbers can't receive SMS → verify WhatsApp numbers via phone call + read the code from the call recording.
- `_parse_json` must use a balanced-brace scan (nested `line_items` break naive `rfind`).
- Airtable PAT can't add single-select options → reuse existing option values.
- Public Ethereum RPC caps/blocks wide `eth_getLogs` ranges → use Etherscan for ERC-20.
- Price source of truth = the image (`CATEGORIES`), not `cost×6`; served sheet = committed `static/price_list.xlsx`.
- Railway auto-deploy unreliable → force-deploy by explicit commit SHA and poll.
