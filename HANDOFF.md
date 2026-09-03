# Northline Group — Agent System Handoff

Paste this into a fresh Claude Code session (run from `~/peptide-agents`) to continue.
It describes the live WhatsApp sales agent, the new order/payment/fulfillment system,
how to deploy/debug, and what's outstanding. No secret tokens are stored here.

**Last updated 2026-09-03. Read §31a FIRST — it is the newest.** §31a records the §31 deploy
(commit `614dd64`) and the one test that had to be fixed to get there. §31 is the change itself.

**§30k and below are history.** §30i restyles the manifest rows
as the workbook table (sticker on the right) and makes the vial photo per PACKAGE, matching the
per-package tracking. §30h records the §30b–§30g deploy.

§30g sorts the manifest oldest-
first with the wait shown on every row, and fixes two payment-recovery sorts that keyed on a field
nothing writes.

§30f §30f rebuilds the manifest WEB
PAGE as the manifest itself — collapsed orders, packages, stickers, and a tracking box per package
feeding Airtable — and drops the emailed spreadsheet.

§30e §30e completes sticker coverage:
every one of the 151 SKUs now has label artwork, sterile water included.

§30d §30d removes Dermorphin and fixes
the §30b font guard, which was checking for fonts the renderer never asks for and let a tofu build
through. Three sheet tests are RED on purpose until the price sheets are rebuilt on the Mac.

§30c §30c rebuilds the warehouse
manifest as a labelling sheet — one row per SKU with the sticker pictured, two tabs, packages broken
out — and installs 132 sticker images mapped to SKUs with the mapping verified by test.

§30b §30b turns the stale-price-sheet
trap §30a caught by hand into a test, and records the thing that makes these sheets special: they
are bilingual, so building them anywhere without a Chinese font renders every CJK glyph as a hollow
box — silently. They can only be built on Jordan's Mac. NOT YET DEPLOYED.

§30a (deployed) records the §30 deploy and that near miss: the tracked price sheets in `static/` were
still showing $12 water while the code quoted $17.

§30 consolidates the catalog into one SKU-keyed source of truth with real weights, closes the
bac-water shipping hole by pricing freight into the product ($12 → $17, both waters) rather than by a
weight rule, and fixes a hole in the §29 test suite itself: it could not have caught a price being
edited.

Before that, §29 FIRST for the pricing guard — §29 fixes a silent revenue leak of a
different kind from §28: catalog name drift left five SKUs unpriceable, and an unpriceable line
skipped the ENTIRE price guard — a missing unit_price shipped the kits free. Fixed by failing closed
plus an alias layer; proven price-neutral by `tests/test_catalog_regression.py`.

Before that, §28 fixed a silent revenue leak:
large-order escalations alerted NOBODY and then ghosted the prospect. §28 also records the
tracking-template swap, Daniel's new email, the Twilio number cleanup, and a full audit marking
several §14 items verified-resolved (DIEGO26 DID run end to end; HK bundle approved).

Earlier context: the 2026-08-19 session built the promo-code deal system (§24), white labelling
(§25), switched the warehouse to **Jason** (§23), and set the **WhatsApp profile picture** (§26).

Earlier context: the **live BTC end-to-end test SUCCEEDED** (Daniel bought bac water, paid real BTC,
verified on-chain, address collected, warehouse pinged — order NL-20260704-0F9D). Two prod bugs were
found & fixed during that test: the "ghosting" after payment (§9) and in-memory payment state
stranded by redeploys (§4a). See §4a (payment recovery), §9 (persona), §13/§16/§17/§18 (manifest page),
and §14 for what is still outstanding.

⚠️ **Railway auto-deploy did not fire ONCE in the 2026-08-19 session (5 pushes, 5 misses).** Treat
force-deploy-by-SHA (§10) as the DEFAULT procedure, not a fallback, and always verify the running
commit — two commits sat undeployed for a day before anyone noticed.

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
- Runs in **webhook mode** in prod (Flask server + in-process schedulers; the ad/leadgen
  background loops are not started in prod; the old tracking loop was deleted — see §22).
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

**4a. Redeploy recovery — deploys are now invisible to prospects (IMPORTANT).** All live state
(`_conversations`, `_lead_stage`, `_pending_payments`) is in-memory and wiped by every deploy. Three
recovery layers rebuild it from Airtable on the prospect's next inbound:
1. **Conversation memory**: rebuilt from the Messages transcript (last ~30 rows, cut at the customer's
   last RESET; consecutive same-role rows merged so the API sees alternating turns). No amnesia, no
   re-greeting mid-negotiation.
2. **Awaiting payment**: if Airtable shows an *awaiting* order for the phone, re-enter
   `awaiting_payment` (wide 7-day `since` window — matching is by unique amount) and verify normally.
   (Originally added after a redeploy stranded the live BTC test mid-payment.)
3. **Awaiting address**: if a *paid* order has no `address_line1`, re-enter `awaiting_address` so the
   customer's address is parsed into the order instead of treated as chat.

While waiting for confirmation, the agent replies with **varied, reassuring, coin-aware** messages
(BTC sets a 10–30 min expectation; never the same line twice, never silence).

## 5. Crypto payment verification (`core/crypto_verify.py`) — READ-ONLY, no keys held
- **USDT = Ethereum (ERC-20)**, verified via **Etherscan API** (`tokentx`; needs `ETHERSCAN_API_KEY`).
  Contract `0xdac17…ec7`, 6 decimals. (Public-RPC `eth_getLogs` is range-capped/403s — Etherscan is the reliable path.)
  NOTE: ERC-20 gas is paid by the customer (~$3–25). Solana (~$0.0005) and Tron (~$1) verifiers were
  also built earlier and are in git history if you want to switch back — Solana/Tron are far cheaper for buyers.
- **BTC**, verified via **mempool.space** (no key); USD→BTC rate locked at quote (`usd_to_btc`), 1 confirmation.
- Matching is by **amount** on a shared address, with a widened auto-accept band + a human-review
  **SAFETY NET** (2026-08-24, commit `922845e`, after TWO escalating overpay incidents):
  - Auto-accept is **UNAMBIGUOUS-ONLY** (2026-08-24 pt.2, `_auto_ok`/`_loose_ok` in `crypto_verify`): a payment
    auto-matches an order iff it is the exact quoted amount (±$0.05, no neighbour that close) OR it is in the
    order's band AND in NO OTHER awaiting order's band. USDT band = overpay up to `max(3% of expected, $20)`,
    underpay −$0.02; BTC = 2% under / 3% over. If two *concurrently-awaiting* orders are close enough that a
    payment could be either, it is NOT auto-matched → the loose scan flags it for review. **Misattribution is
    therefore impossible at any volume.** `allocate_unique_amount` also spaces concurrent charges ≥ $0.10 apart
    (not just distinct) so exact payers of same-priced orders still auto-match.
  - Safety net: `agents/payment_watcher.py`, on no auto-match, reruns `verify_payment(..., loose=True)` (±20–40%);
    any plausible near-miss is emailed to ops (`report_emails` = jordan@/daniel@) ONCE and the order's new
    `payment_flagged` checkbox is set. **A received payment is never silently dropped again.**
  - History: 2026-07-15 added +$5 overpay (a 279.24/279.01 order had stranded); 2026-08-24 the flat $5 was
    STILL too tight — a 923.01 order was paid with 932.20 (+$9.19) and sat stuck — so it was widened to 10%
    and the loose-scan review email added. See §27.
  - **Deeper fix on the table:** amount-matching on ONE shared address is inherently guess-prone; the bulletproof
    architecture is a **unique receiving address per order** (HD-wallet xpub derivation). Scoped but not built — see §27.

## 6. Airtable data model (system of record)
Base `apprMJI8obXHOLvJU`. Tables: Leads, Campaigns, Labs, **Orders**, **Order Items**, **Messages**.
- **Orders** (one row per purchase): `order_ref`, `lead_id`(link), `product`(summary), `total_price`,
  `coin`, `expected_amount`, `payment_status`(awaiting/paid/failed), `tx_hash`, `paid_at`, `week_tag`,
  `ship_name`/`address_line1`/`address_line2`/`city`/`state_province`/`postal_code`/`country`/`ship_phone`,
  `fulfillment_status`, and two cadence flags: **`bulk_ordered`** (checkbox) and **`manifested`** (checkbox).
- **Order Items** (one row per product, linked to Orders): `item`, `Order`(link), `product`, `spec`, `kits`,
  `supplier_sku`, `line_total`.
- **Messages** (one row per WhatsApp message — conversation transcript log; table id `tbldFNHuylHWrQyuF`):
  `phone`, `direction`(singleSelect inbound/outbound), `body`(long text), `sent_at`(dateTime), `Lead`(link).
  Written best-effort by `airtable.log_message()` from the Twilio webhook + operator relay. Read it grouped
  by `phone`, sorted by `sent_at` asc to see each prospect thread. Logs going forward only (no backfill;
  pre-existing history is in Twilio's Message logs). Durable across redeploys (unlike in-memory state).
- The Airtable PAT can create fields/tables via the metadata API (used to build the above), but **cannot add
  new single-select options** to an EXISTING field → use existing option values (e.g. lead `source="Direct"`,
  not "WhatsApp"). NOTE: defining choices when CREATING a brand-new singleSelect field IS allowed (that is how
  the Messages `direction` field was made).
- **Test data was purged** (2026-06-25): all `555`-number test Leads/Orders deleted; base started clean for go-live.

## 7. Fulfillment reports (`agents/weekly_report.py`) + scheduler (`main.py`)
Two independent cadences, two audiences, generated from paid orders; flag-based so each order is
processed once per cadence:
- **DAILY warehouse ping** — `run_daily_manifest()`: counts paid orders still needing tracking
  (`get_orders_needing_tracking` = paid AND not `tracking_sent`) and, if any, **emails** the warehouse
  rep (`WAREHOUSE_EMAIL`, comma-separated ok; Gmail SMTP) a **plain, non-persona** message with a link to
  the tracking page (§18). If `WAREHOUSE_EMAIL` is unset it falls back to WhatsApp
  (`WAREHOUSE_WHATSAPP` = `whatsapp:+8613418806654`) so the ping never silently drops. Fires **daily at
  `DAILY_MANIFEST_HOUR` (0 = midnight Mountain)**. (This replaced the old chunked per-order text
  manifest; `build_warehouse_whatsapp` still exists but is no longer used.)
- **WEEKLY supplier bulk** — `run_supplier_bulk()`: paid orders where `bulk_ordered`=false → aggregate kits
  per SKU (NO names/addresses/prices) → **email** → set `bulk_ordered`. Fires **Sunday 00:00 Mountain** (week =
  Sun 00:00 → Sat 23:59; last order Sat 11:59pm). Brother forwards it to the supplier himself.
- Email delivery: **Gmail SMTP** (Google Workspace) — `GMAIL_USER`/`GMAIL_APP_PASSWORD`, sent to the
  comma-separated `REPORT_EMAIL` (jordan@northlinesupplies.com + daniel@northlinesupplies.com).
  SendGrid was **retired** (the merged Twilio Email console gated everything behind domain auth).
- Scheduler runs in-process in the webhook app (`run_report_scheduler`, checks every 5 min, guarded once/day & once/week).
- The same scheduler runs the **health monitor** (`agents/health_monitor.py`): HOURLY Claude canary
  (tiny real API call; email alert on failure, re-alert every 6h, "resolved" email on recovery — catches
  credit exhaustion before a customer does) and DAILY Twilio balance check (alert below
  `TWILIO_BALANCE_ALERT_USD`, default $25). Alerts email `REPORT_EMAIL`.
- One-shot CLI: `python main.py daily` | `python main.py weekly` | `python main.py report <week-tag>` (preview, no marking).

## 8. Pricing (`core/pricing.py` + `core/price_image.py`)
Source of truth = the price-list image `CATEGORIES` (whole dollars). `get_list_price`/`get_catalog_text`
resolve to the image; floor = ceil(3×cost); discount caps <25:5% / 25–49:10% / 50+:15%. Whole dollars only.
Sheet sent to customers = committed `static/price_list.xlsx` (regenerate after price edits, see §10).
NAD+ is a manual loss-leader: $55 / $135 / $195 (3–4× cost). Audit: catalog markup ~5.6× median.
Wallet/crypto receiving addresses are **set in Railway** (`ETH_ADDRESS`, `BTC_ADDRESS` — see §14). The
agent never hands out an address; the code generates exact payment instructions after a placed order.

## 9. Persona / tone (`_build_order_prompt` in `agents/messaging_agent.py`)
Warm kind Chinese woman **named Lily**, light ESL flavor, frequent "dear" (almost every question
ends in "dear"); offers choices as "which would you like, dear?"; positive payment framing; never
reveals discount % or tiers; no promo in greeting.
- **Never self-identifies as a bot/AI/"agent"** — she is a real person, Lily; deflects "are you a
  robot?" warmly. (Earlier prod build literally said "I am a service agent" — fixed.)
- **Greets only ONCE per conversation** and varies wording. A per-phone lock in `twilio_webhook_handler`
  serializes rapid back-to-back inbound messages (was double-greeting on a race).
- ⚠️ **Do NOT re-add duplicate-reply suppression.** An earlier build silenced any outbound identical to
  the previous one — during the live BTC test this made the agent **ghost a paying customer** (his
  "did you get it? / are you there?" pings all produced the same "don't see it yet" line, every one
  suppressed → total silence → looks like a scam). The lock alone prevents the double-greet race; every
  customer ping must get an answer (the varied waiting messages in §4a make repeats impossible anyway).
- **Think-first:** the order-prompt JSON's FIRST field is `thinking` (private scratchpad, never sent) so
  she reasons before composing `reply_message`. (The Claude client also has native extended thinking on.)
- **Product disambiguation** (from Daniel's live test): match loose customer wording to the EXACT catalog
  item; CJC+Ipamorelin Blend is ONE product (not two lines); CJC-1295 "no DAC" vs "with DAC" are distinct;
  NEVER add line items the customer didn't ask for; ask a short clarifying question when unsure instead of
  guessing or dumping a long "full order".

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

## 13. Current state (DEPLOYED — commit `2c3126dd`; LIVE E2E TEST PASSED)
**The live end-to-end test succeeded (2026-07-03, order `NL-20260704-0F9D`):** Daniel ordered 1x bac
water via WhatsApp, paid **real BTC** (tx `7e026386…`, confirmed on-chain, verified by unique amount),
address collected (Lumex Health, Kaysville UT), order marked paid in Airtable, and the warehouse rep
was pinged with the tracking-sheet link (§18). The flow is proven in prod with real money.

Two prod bugs were found during that test and are FIXED (see §4a and the §9 warning): (1) duplicate-
reply suppression made the agent ghost the customer after payment; (2) in-memory payment state was
wiped by a redeploy, stranding the in-flight order — recovery from Airtable now handles this.

All of §4–§7 + §17 (proof media, de-duped) + §18 (warehouse tracking page) deployed; `/health` ok. All
env vars in §14 set. Email path live-tested. Transcript logging live (§6) — now captures EVERYTHING
outbound incl. the warehouse ping and proof-media sends, so Jordan/Daniel can read every thread in the
Messages table (group by phone). Persona hardened (§9). Base clean (only real orders). Decommissioned
the stale `order_intake_agent` + supplier-leaking `fulfillment_agent`. **Not yet done:** §16 vial-photo
stage (tracking-number stage shipped as §18).

## 14. Open items / TODO
**Env vars now SET in Railway (all of these are live):**
- `ETH_ADDRESS` = `0xD1A3BaAf4d451cD676FFbbf07c09A9833A149E37` (USDT-ERC20 received here).
- `ETHERSCAN_API_KEY` set (verifies USDT).
- `BTC_ADDRESS` = `bc1qxpdqaksmz6uaz5ftfum8y8cmujtzc2xuwaea5p` (BTC accepted; both coins offered).
- `GMAIL_USER` = jordan@northlinesupplies.com, `GMAIL_APP_PASSWORD` set (weekly report email via Gmail SMTP).
- `REPORT_EMAIL` = jordan@northlinesupplies.com,daniel@northlinesupplies.com (weekly report recipients).
- `WAREHOUSE_WHATSAPP` = `whatsapp:+8613418806654`, `DAILY_MANIFEST_HOUR` = 0 (midnight Mountain).
- `WAREHOUSE_EMAIL` = `ybgjwl888@outlook.com` — the daily manifest is emailed here (WhatsApp is only
  a fallback if this is ever unset). Live-tested 2026-07-05: real manifest email delivered via Gmail SMTP.
- `MANIFEST_TOKEN` set (guards the warehouse tracking page, §18).
- `OPERATOR_NUMBERS` (optional) — still unset; large-order alerts only log until set.

**Remaining:**
- ~~Top up Anthropic credits~~ ✅ DONE 2026-07-08: credits added + **auto-reload enabled**; canary
  verified UP. (Incident: account ran dry, a live prospect got NO replies — every Claude call 400'd,
  /sms 500'd, Twilio retried/double-logged. Fixes now in place: webhook fallback holding line +
  hourly canary alert (§7), so a repeat surfaces in ≤1h and customers never get silence.)
- ~~Twilio auto-recharge~~ ✅ enabled 2026-07-08 (balance was $14.31; daily health check alerts
  below $25 as a backstop). Railway billing also confirmed set up (Hobby; postpaid to card —
  usage ~$2–4/mo, scales negligibly with customers). **All billing is now self-refilling:**
  Anthropic auto-reload + Twilio auto-recharge + Railway postpaid, watched by the §7 health monitor. Note: daily WhatsApp manifests to the warehouse rep 7/3–7/5 were all
  `undelivered` (why tracking never got entered) — the email switch (§7) was the fix.
- 🟡 **Airtable free plan caps at 1,000 records/base** — the Messages table logs every WhatsApp message
  (129 rows after ~2 weeks) and will hit the cap first. Options: upgrade to Team (~$20/user/mo) or add
  a pruning job for old Messages rows. Attachments (vial photos, ~30–100KB each) are nowhere near the 1GB cap.
- ~~Run the live end-to-end test~~ ✅ DONE 2026-07-03 with real BTC (see §13). USDT path is code-identical
  (verified against live Etherscan earlier) but has not had a real-money run yet — optional.
- Warehouse rep to enter the tracking number for `NL-20260704-0F9D` on the §18 page (customer then gets
  the tracking text automatically) — watch this complete the first full fulfillment loop.

**Other standing items:**
- Twilio HK **regulatory bundle** = `pending-review` (WhatsApp unaffected; SMS/voice gated until approved).
- Cleanups: delete the Railway token used this session; rotate the GitHub PAT; delete the Cloudflare API token.
- **Delete the orphaned `status` column in the Airtable Orders table** (UI: right-click header → Delete
  field) — code no longer reads/writes it but existing rows still display "Pending" (§22).

**Open as of 2026-08-19 (newest session):**
- 🔴 **Twilio balance $19.96 — below the $25 alert threshold.** Auto-recharge is supposedly enabled
  (§14 above) so either it has not fired or it is not working. A dry Twilio account means Lily goes
  SILENT mid-purchase, and the DIEGO26 order is $3,393.64. Check this first.
- 🟡 **DIEGO26 is built but has never run end to end with a real customer.** Untested in prod: the
  inbound artwork upload and the factory email (both need a real inbound image). To rehearse: WhatsApp
  the HK number `DIEGO26`, pick a coin, send any image — that exercises everything up to payment.
  Delete the resulting test order afterwards (it will carry `promo_code=DIEGO26` but only a PAID order
  burns the code, so a rehearsal is safe).
- 🟡 **Jason has never received an automated manifest** — his first fires when a new order is paid (§23).
- 🟡 **WhatsApp profile text fields still blank** and customer-visible: `about`, `description`,
  `websites`, `emails`, `vertical` (§26). Jordan to supply wording.
- 🟡 `static/northline_banner.jpg` committed but unused — candidate for the §17 proof library (§26).
- Orders fields added 2026-08-19: `legacy_warehouse`, `promo_code`, `label_artwork`, `factory_notified`.
- Railway vars added 2026-08-19: `FACTORY_EMAIL`, `FACTORY_WHATSAPP`; `WAREHOUSE_EMAIL` changed (§23).

## 15. Gotchas (hard-won)
- Consoles lie — verify via API (sender ONLINE w/ empty webhook silently drops inbound; bundle "submitted" while Draft).
- iPhone photos are HEIC even when named `.jpeg` (silently rejected) → `sips -s format jpeg in.jpeg --out out.jpg`.
- VoIP numbers can't receive SMS → verify WhatsApp numbers via phone call + read the code from the call recording.
- `_parse_json` must use a balanced-brace scan (nested `line_items` break naive `rfind`).
- Airtable PAT can't add single-select options → reuse existing option values.
- Public Ethereum RPC caps/blocks wide `eth_getLogs` ranges → use Etherscan for ERC-20.
- Price source of truth = the image (`CATEGORIES`), not `cost×6`; served sheet = committed `static/price_list.xlsx`.
- Railway auto-deploy unreliable → force-deploy by explicit commit SHA and poll.
- Railway GraphQL is behind Cloudflare → requests with a default urllib User-Agent get **403 error 1010**.
  Send a browser-like `User-Agent` header on every Railway API call.
- **WhatsApp 24-hour window**: Meta silently drops freeform business messages sent >24h after the
  customer's LAST inbound (Twilio accepts them, then async `undelivered` error 63016 — code sees
  "success"). Vial photos (sent 1–2 weeks post-purchase) ALWAYS hit this; tracking often does.
  Fix (LIVE): approved utility templates via Twilio Content API — `northline_order_shipped`
  (HX8015be3eb4147531f1a7be20c00544e6) + `northline_vials_ready` (media,
  HXdd2db9caae789a8b39f8137e0c64646c), SIDs in Railway as `TRACKING_CONTENT_SID`/`VIAL_CONTENT_SID`.
  `send_tracking_to_customer`/`send_vial_photo_to_customer` check `_whatsapp_window_open()` (last
  inbound in Airtable transcript <23.5h) and auto-switch to the template when closed. Templates cost
  a few cents (Meta utility fee). Check approval: GET content.twilio.com/v1/Content/<sid>/ApprovalRequests.
- Local `~/peptide-agents/.env` can be STALE vs Railway (it once had the old US number as
  `TWILIO_WHATSAPP_FROM` — a local send silently used the wrong sender → 63016). Railway is the
  source of truth for env values; verify before local sends that touch customers.
- Twilio inbound media URLs (`MediaUrl0`, on api.twilio.com) require account auth → they are NOT directly
  re-sendable as a `media_url` to a customer. To relay an image, re-host it at a public URL first
  (e.g. upload to an Airtable attachment field, which returns a public URL Twilio can fetch).
- iPhone videos are HEVC `.mov`; WhatsApp/Twilio need **H.264 `.mp4`**. This Mac has no ffmpeg/brew, but
  macOS ships **`/usr/bin/avconvert`** — transcode with
  `avconvert -s in.mov -p Preset960x540 -o out.mp4 --replace` (Preset640x480/960x540/1280x720/1920x1080 = H.264).
  Terminal can't read `~/Library/Messages/Attachments` (no Full Disk Access) → get files out of Messages by
  right-click → Copy, then paste (⌘V) into a Finder folder (the repo). Check codec/size with `mdls`/`ls`.

## 16. Shipping notifications to customers (BOTH STAGES BUILT — see §18)
The agent sends each customer fulfillment updates over WhatsApp, in two stages:
  1. **Tracking number** (immediately, when the warehouse makes the label) — LIVE since the §18 page
     shipped; rep enters it on the manifest page → Airtable + auto-text in Lily's voice.
  2. **Vial photo** (~1–2 weeks later, when the weekly bulk arrives and vials are divvied per order) —
     LIVE: the manifest page (§18) has a per-order **photo upload**. The rep taps it (phone camera or
     file picker), the photo is stored on the order (`vial_photo` attachment field — permanent record),
     and the agent WhatsApps it to that customer with a Lily-voice caption, then sets `vial_photo_sent`.
How the match problem was solved: the rep uploads **on the order's own card** on the manifest page, so
photo→order→customer matching is structural — no order refs to type, no WhatsApp caption parsing, no
special-sender handling. (The old `VIALS <order_ref>` WhatsApp-reply proposal was dropped; warehouse
comms moved to email + the web page anyway.)
Mechanics: `POST /manifest/photo` (token-guarded) → Pillow normalizes the image (EXIF rotation fixed,
RGB JPEG, long edge ≤1600 px — safely under Airtable's 5 MB upload cap and WhatsApp's image limit) →
`airtable.attach_vial_photo()` (pyairtable `upload_attachment`, content.airtable.com API) returns the
Airtable-hosted URL → `send_vial_photo_to_customer()` sends it as Twilio `media_url` (send is immediate;
Airtable attachment URLs expire after ~2 h) → `mark_vial_photo_sent`. Orders fields `vial_photo`
(attachment) + `vial_photo_sent` (checkbox) created 2026-07-05 via metadata API. If the Twilio send
fails, the flag stays unset so the card stays on the page for a retry (re-upload appends another photo).

## 17. Proof / legitimacy media library (LIVE)
When a prospect asks for proof we're a real lab / wants to see the product, the agent ("Lily") picks the
best-fitting asset by its own judgement and sends it over WhatsApp with a warm caption.
- **How it works:** `core/proof_media.py` reads `static/proof/manifest.json` (a JSON array of
  `{key, file, type:image|video, description}`); only entries whose file actually exists are loaded. The
  order-prompt injects the asset catalog (`get_media_catalog_text`) so Lily knows what's available and
  chooses by the descriptions. New JSON action **`send_media`** + field **`media_key`** → `_send_proof_media`
  sends `{_BASE_URL}/proof/<file>` as a Twilio `media_url`. The `/proof/<filename>` Flask route serves ONLY
  manifest-listed files. Sends at most one asset per request; falls back to a warm text reply if none fits.
- **Current assets (4):** `lab_paper.mp4` (lab clip with "Northline Group/北线集团" paper — strongest "real
  lab" proof), `vials.mp4` (vials close-up — "see the product"), `lab_equipment.mp4` (facility/manufacture
  proof), `warehouse_boxes.jpeg` (warehouse boxes — real operation/stock). Verified: the agent routes each
  proof-type question to the right asset.
- **To add/swap a clip:** drop the file in `static/proof/`, add a manifest line (good description = good
  judgement), commit, redeploy. No code change. Keep files <16 MB (WhatsApp limit) and videos as **H.264 mp4**
  (HEVC won't play in WhatsApp).
- **Source of the current clips:** Daniel sent them via iMessage as small HEVC `.mov`. Converted to H.264 mp4
  with Apple's built-in `avconvert` (see §15). Originals were not committed.
- **De-dup (important):** an in-memory `_sent_media[phone]` set means each asset is sent **once** per
  conversation and no more than **2** proof clips total — else the agent re-spams the same video (this
  actually happened in a live test: repeated "proof of product" → same video over and over until the
  tester said "STOP"). On a fresh send the handler records `[sent proof video/photo: <key>]` in the
  conversation history via a `_MEDIA_SENT` sentinel (so the caller doesn't double-append and break role
  alternation). Cleared on RESET.
- This is SEPARATE from §16 (per-order shipping notifications) — that's still in design.

## 18. Warehouse manifest page (LIVE) — daily "sheet" for the rep
Replaces the old daily text manifest. The rep (phone OR computer) gets a plain daily **email** (§7) with
a link and completes each order on a simple web page — enter the **tracking number** and upload the
**vial photo**; both flow straight into Airtable + message the customer automatically.
- **Flask routes (`main.py`):** `GET /manifest?token=…` renders a mobile/desktop-friendly page — one card
  per paid order with outstanding work (`get_orders_needing_fulfillment` = paid AND (needs tracking OR
  needs vial photo)); each card shows a tracking form (until `tracking_sent`) and a photo-upload form
  (until `vial_photo_sent`), with green ✓ markers for the completed half. `POST /manifest/save` writes
  `tracking_number` + `tracking_sent` + sets `fulfillment_status="shipped"` (an EXISTING single-select
  option — do NOT invent one, Airtable rejects the whole update otherwise), then texts the customer via
  `send_tracking_to_customer()` in Lily's voice. `POST /manifest/photo` = the vial-photo stage (§16 has
  full mechanics). All routes require `?token=` == `MANIFEST_TOKEN` (403 otherwise). Max-width 640px.
- **Airtable:** `tracking_sent`, `vial_photo` (attachment), `vial_photo_sent` (checkbox). Helpers in
  `core/airtable_client.py`: `get_orders_needing_fulfillment` (also `get_orders_needing_tracking`, kept),
  `set_order_tracking`, `attach_vial_photo`, `mark_vial_photo_sent`, `get_lead_phone_for_order`.
- **Customer phone** to text = the linked Lead's `phone` (kept with the `whatsapp:` prefix).
- **Auto-send on save/upload** was Jordan's choice (vs. record-only). Both §16 stages ride this page.
- Daily ping fires from `run_daily_manifest()` (§7) — the email now says how many orders need tracking
  vs. vial photos. Link: `https://peptide-agents-production.up.railway.app/manifest?token=<MANIFEST_TOKEN>`.

## 18a. Fulfillment status lifecycle (2026-07-20 semantics)
`fulfillment_status` (informational; automation runs on the checkboxes) advances FORWARD ONLY
(`_advance_fulfillment`, rank recorded<in_bulk_order<labeled<shipped — stages can interleave):
- `recorded` — order created (payment instructions sent)
- `in_bulk_order` — included in a successful weekly supplier bulk email
- `labeled` — tracking entered on the manifest page. BUSINESS INTENT: warehouse creates tracking
  FAST (possibly before inventory arrives) as a trust signal; customer message says "shipment is
  booked", NOT "shipped" (freeform reworded; template `northline_tracking_booked`
  HX562f612553f60b54cf12ed9feaa586f8 submitted — swap `TRACKING_CONTENT_SID` to it once approved,
  old "has shipped" template stays active until then)
- `shipped` — vial photo (with client name/address visible) sent right before actual dispatch

## 18b. Negotiation robustness (2026-08-01 incidents — both fixed)
Two live failures with Daniel testing, same root pattern (state machine overriding conversation):
1. **awaiting_payment deafness**: every message ran the verify-with-finance loop; 4 discount asks got
   "checking with finance". FIX: `_is_payment_ping` Claude classifier — only "ALREADY sent / check it /
   resend address-amount" verifies; everything else (negotiation, changes, "I WILL send") routes to the
   normal ordering handler with stage preserved. Same fix applied to awaiting_address (non-address,
   digit-free messages route to Lily instead of the canned re-ask).
2. **Price-guard flip-flop**: Lily quoted $90/kit (5% off $95=$90.25 rounded naturally); the validator
   CEIL'd the min to $91 and overrode her with an identical canned "$91/kit" line 6x ("STOP FLIP
   FLOPPING"). FIX: (a) discount-cap minimum rounds DOWN to the human whole dollar (hard 3x-cost floor
   still ceils); (b) a below-min quote regenerates through Claude with an INTERNAL note of true minimums
   → one warm apology + corrected number, never a canned repeat; (c) re-`place` after renegotiation
   SUPERSEDES the customer's previous awaiting order (marked `failed`) before allocating the new unique
   amount, so stale amounts can't match and recovery can't resurrect them. Prompt now covers
   post-instructions negotiation explicitly (re-place on any changed total; never state new totals
   without action place).
Verified by live simulation: negotiate $285→$275 → accept → place at $90/kit → supersede → fresh
instructions.

## 19. Cost guardrails (LIVE)
Protects the Anthropic bill from a prospect who chats forever without buying (auto-reload would
otherwise fund it indefinitely). Two layers in `agents/messaging_agent.py`:
- **Daily per-prospect reply cap** — `AGENT_DAILY_MSG_CAP` (default 50; a real buyer closes in 10–30
  messages). Past the cap, Lily sends a canned **time-aware** excuse with NO Claude call: a
  "it's very late here, I'll reply tomorrow" line only when it actually IS night in China (22:00–07:00
  Asia/Hong_Kong — the persona's home), otherwise a "very busy today" line; 2 variants each, rotated.
  Ops get ONE alert email per phone per day (raise the cap for a legit whale, or block the number in
  Twilio's deny list for abuse). Cap resets at China midnight (matches "tomorrow"). RESET and operator
  messages don't count. Counter is in-memory (redeploy resets it — acceptable slack).
- **History cap** — `get_conversation` trims to the last `_MAX_HISTORY=40` messages (first entry kept
  a `user` turn for API role alternation), so per-message input cost stays bounded no matter how long
  the thread; full transcript remains in Airtable.
- Third layer (manual, console-only): a monthly **workspace spend limit** at console.anthropic.com —
  the hard ceiling auto-reload cannot cross. Jordan to set (~$500/mo suggested at current volume).

## 20. Transcript reviewer — QA supervisor agent (LIVE)
`agents/transcript_reviewer.py`, run by the in-process scheduler every `REVIEW_INTERVAL_HOURS`
(default 6). Pulls the Airtable Messages transcript, finds customer threads with recent activity
(warehouse/operator/supplier numbers excluded), and has Claude QA each one against the business
rules: every message answered, persona intact (never admits AI), prices/discounts within authority
(5/10/15% caps, catalog-checked — the reviewer prompt embeds the live catalog), no invented or
extra products, correct escalation of >100-kit below-cap demands, correct order flow. Knows the
guardrail/canned replies (§19) and waiting-on-payment reassurances are NORMAL, so no false alarms.
Emails `REPORT_EMAIL` a per-thread issue list with severities ONLY when something is flagged
(subject counts HIGH issues); clean runs just log. Cost ≈ one small Claude call per active thread
per run. Manual run: `python -m agents.transcript_reviewer [lookback_hours]`.
First live run correctly flagged the 2026-07-08 outage thread (customer ignored — HIGH).

## 21. Infrastructure incidents & upgrades (2026-07-20) — READ THIS
Two silent platform failures were found stacked on the same day:
- **Railway Hobby blocked ALL outbound SMTP** (`OSError 101`) — prod had NEVER successfully sent an
  email: every scheduled manifest, both weekly supplier bulks (Jul 5 + Jul 19), and every health/QA
  alert failed silently. Every email that ever arrived was sent manually from the laptop. **Fixed by
  upgrading Railway to Pro** — verified same day via `GET /admin/email-test?token=<MANIFEST_TOKEN>`
  (token-guarded prod endpoint; also `GET /admin/run-manifest?token=…` triggers a real manifest).
- **Airtable free-plan monthly API quota (1,000 calls) exhausted** (`PUBLIC_API_BILLING_LIMIT_EXCEEDED`,
  mostly payment-watcher polling) — data layer hard-down: orders couldn't be recorded, transcripts
  didn't log. **Fixed by upgrading Airtable to Team** (per-workspace! the base must live in the
  upgraded workspace). Watcher cadence halved to ~10 min; daily Airtable probe added to the health
  monitor (§7) — no usage-% API exists, so it alarms on hard failure.
- The missed supplier bulks were caught up manually 2026-07-20 (email to REPORT_EMAIL: 1 kit bac
  water + 2 kits reta = 3 kits); both orders marked bulk_ordered; books consistent.
- Weekly bulk now marks orders ONLY after a successful email send (was: marked regardless — how the
  Jul 5 and Jul 19 sends vanished without a trace).

## 22. Orphaned `status` field + tracking_agent retired (2026-08-17, commit `9871dfb`)
Jordan asked why Airtable showed orders as "pending". Root cause: the Orders table had a THIRD
status field — bare **`status`** (choices Pending/Sent to lab/In production/Shipped/Delivered), a
relic of the original single-item order model — that `create_pending_order` stamped `"Pending"` at
creation and **nothing ever advanced**. All 4 live orders showed "Pending" regardless of their real
`payment_status`/`fulfillment_status` (which remain the ONLY real lifecycle fields, §6/§18a).
Fixes (all in `9871dfb`):
- `create_pending_order` no longer writes `status` (new orders get no value there at all).
- **Deleted `agents/tracking_agent.py`** — the pre-WhatsApp buyer-notification agent that polled
  Orders by `status` and sent SMS + SendGrid email (both channels retired; wrong persona — not
  Lily). It was never started in prod (webhook mode). Its manifest-page replacement (§16/§18) does
  the whole job. Also removed its dead deps: `create_order`, `update_order`, `get_pending_orders`,
  `get_orders_by_status` (airtable_client), `run_tracking_loop` + the `tracking` CLI mode (main.py),
  and the `agents/__init__` exports. `get_order` kept (manifest page uses it).
- The Airtable **field itself still exists** (API delete was blocked; irreversible-schema guard) —
  delete it in the UI (§14 standing item). Until then old rows keep displaying the frozen "Pending".
- Deployed via Railway auto-deploy (not SHA-verified — no Railway token that session; `/health` ok).

## 23. Warehouse switched to Jason (2026-08-19)
`WAREHOUSE_EMAIL` = **`jason@jjstshipping.com`** (was `ybgjwl888@outlook.com`, the previous rep).
Jason is a **drop-in replacement**: same daily manifest email, same §18 manifest page (enter tracking,
upload vial photo), weekly supplier bulk untouched. `MANIFEST_CC` still CCs Daniel.
`WAREHOUSE_WHATSAPP` (+8613418806654) remains only as the fallback when the email var is unset.
- **His manifest starts CLEAN at new orders.** The two paid-but-unshipped orders (NL-20260704-0F9D
  from the July BTC test, NL-20260808-D4C1) are family trial runs the PREVIOUS rep is finishing
  off-system. Both carry a new Orders checkbox **`legacy_warehouse`**; `get_orders_needing_tracking`
  and `get_orders_needing_fulfillment` exclude flagged orders by default. Net effect: the daily
  manifest finds 0 pending and sends NOTHING until a new order is paid.
- **The old orders are still completable** at `/manifest?token=…&legacy=1` — that view lists them so
  tracking can be entered and the customer still gets the automatic text. Without it, hiding them
  would have silently killed their notification path.
- Verified in prod: default view empty, `?legacy=1` lists both.
- History: `jason@jjstshipping.com` had received exactly ONE manual manifest before this (2026-08-04,
  sent alongside `1073944939@qq.com`). Jordan chose Jason alone going forward.

## 24. Pre-approved deals via promo code (`core/deals.py`) — DIEGO26 LIVE
A **deal** is a human-approved basket at a human-approved total (Daniel price-matches a competitor
sheet). It deliberately **bypasses per-item floor validation** — those guards stop Claude inventing
discounts, they are not there to second-guess a price the owner set himself.
- **Deals live in CODE, redemption state in AIRTABLE.** The basket is fixed and shouldn't be casually
  editable, and codes are checked on every inbound message so a lookup must be free. A code counts as
  spent once an order carrying `promo_code` reaches `paid` (`is_promo_redeemed`) — that survives
  redeploys and can't be resurrected by the awaiting-order recovery path.
- **Flow:** code → confirm basket + ask coin → create order → ask artwork → payment instructions →
  (paid) → factory email. Stages `deal_coin` and `awaiting_artwork`. Coin is **keyword-detected**, not
  sent to Claude — a two-option question can't need a model and this way it can't invent a third coin.
- A code is honoured wherever it appears in conversation, but **NOT** once payment instructions are
  out (a code mentioned then is chatter; re-opening would supersede the order they are about to pay).
  Any earlier unpaid order under the code or from the phone is superseded first.
- A reused one-time code gets a warm holding reply + ops alert, never a second discounted order.
- **DIEGO26** (Verix price match, Diego group): 22 lines / **30 kits**, items **$2,893.64**
  ($2,193.50 original + $700.14 added 08-19), white label **$400**, shipping **$100**, grand total
  **$3,393.64**. One-time, burns on payment confirm. All 22 SKUs exist in the live catalog, so the
  supplier bulk and warehouse manifest work with no additions.
  - Daniel's "standard NG pricing" figure of $3,582 does not reconcile with our catalog ($3,512 by
    `CATEGORIES`) — harmless (the deal total is fixed) but don't quote a "you save $X" from it.
  - The add-on lines were priced at ~12% off catalog vs ~37.5% on the original lines. Jordan
    confirmed this is intentional (per-line Verix match, not a flat %).
- ⚠️ **`allocate_unique_amount` had a latent UNDERCHARGE bug, now fixed.** The unique cents tail was
  built with `int(base)`, which truncates: every total so far has been whole dollars so it never bit,
  but DIEGO26's $3,393.64 would have been quoted as **$3,393.01**. The dollar base is now CEILED, and
  a new `exact=True` mode honours a Daniel-quoted total to the cent, nudging up only on a real
  collision. Verified: normal whole-dollar orders behave identically ($285.01, $285.02).

## 25. White label as a standing service (`core/white_label.py`) + label factory (`core/factory.py`)
Sticker rate card (Daniel, 2026-08-17). Product name + mg = 1 **design**; min **100 stickers/design**;
tier chosen by qty PER design, rate applied to the total sticker count:
`100-249 $0.40 · 250-499 $0.32 · 500-999 $0.25 · 1000+ $0.20` (repeat = exactly half).
Reproduces his worked example: 21 designs × 100 = **$840 new / $420 repeat**.
- **Lily has ZERO discount authority.** CODE computes the figure and she only explains the table —
  same principle as code-generated payment instructions. Below-table deals reach her as a promo code.
  She raises white labelling when it fits (bulk buyers, resellers, branding questions), never in the greeting.
- **Repeats qualify STRUCTURALLY**, nothing is self-reported: a repeat is a reprint of a design we
  already hold artwork for and the factory already has on file. New artwork, or a different product
  or strength (the label prints name + mg), = a NEW design at new-order rates.
- **Artwork is collected BEFORE payment instructions go out**, so the factory hand-off can fire the
  instant payment confirms with nothing left to chase. Twilio inbound media needs account auth
  (§15), so the bytes are downloaded server-side and uploaded onto the order (`label_artwork`).
- **The factory is EMAILED, never WhatsApped** — `FACTORY_EMAIL` = `2641377459@qq.com`. It is a
  mainland-China contact and freeform WhatsApp outside the 24h window is silently dropped by Meta,
  the same failure that lost the July manifests (§15/§21). Artwork rides as a real attachment so
  nothing depends on an Airtable URL that expires in ~2h. `FACTORY_WHATSAPP`
  (+8615381769607) is stored for a HUMAN to use; the agent does not send there.
- Fires from BOTH payment-confirm paths (inbound verify + payment watcher). No-ops unless the order
  is an unsent white-label deal. `factory_notified` is set ONLY on a confirmed SMTP send, so a
  failure stays retryable; a paid order with no usable artwork raises an ops alert instead of a
  silent half-send.
- **White label is PER-KIT, not per-line.** Each deal item carries `kits` (what ships / what the
  supplier bulks) and `wl_kits` (how many carry the CUSTOMER's branding). For DIEGO26 the customer's
  artwork covers the ORIGINAL batch only: **30 kits ship, 25 customer-branded across 21 designs, 5
  under our own Northline label** — Daniel arranges those with Jason directly, off-system. RT15 is
  excluded from the factory job entirely; Reta 30mg / BPC-157 / Tesamorelin / MOTS-c print for their
  ORIGINAL quantity only. The factory email lists only branded designs and says so explicitly.
- **The manifest card shows the split** so the packer can't misread a 30-kit order as one label type:
  a "TWO LABEL TYPES" panel with the customer-branded designs and the Northline ones, derived from
  `wl_kits` (so it can't drift). Orders with no promo code render exactly as before.

## 26. WhatsApp business profile (2026-08-19)
Sender `XE42b164026f3bbf3bd190502b0ba2c997` (whatsapp:+85292909474), status ONLINE.
- **Profile picture is SET**: `logo_url` = `{_BASE_URL}/static/northline_profile.png`. Confirmed by
  read-back. The source logo Daniel sent is ~2:1, so a circular crop would have kept the mountains and
  cut the NG monogram and wordmark. `northline_profile.png` is a rebuilt **1024×1024**: stray
  full-width white rule (rows 13–17, a crop artifact) removed, mark centred on the logo's own navy
  `(4,20,48)` so padding is seamless, sized to 88% of the circle radius. Verified against a rendered
  circular mask.
- **A BANNER CANNOT BE SET — do not retry.** Twilio's docs list `banner_url` as writable and the API
  accepts the write, then silently drops it. Proven: the SAME image URL persists as `logo_url` and
  vanishes as `banner_url`; sending `banner_url` alone returns "Update request body is empty" (the
  field is stripped before validation). Root cause: Meta's WhatsApp Business Profile has only
  `about`, `address`, `description`, `email`, `profile_picture_url`, `websites`, `vertical` — there is
  no banner/cover concept. Twilio's schema is shared with RCS, where agents DO have banners.
- `static/northline_banner.jpg` (the cleanroom facility shot, 1290×716) is committed but UNUSED.
  Good candidate for the §17 proof-media library — it is strong "real lab" evidence and the library
  is currently 4 assets, only one a photo.
- **Still blank and customer-visible**: `about`, `description`, `websites`, `emails`, `vertical`
  (a `HEALTH` option exists). Jordan to supply wording — do not invent copy for a live profile.
- Getting images out of iMessage without Full Disk Access: right-click → Copy in Messages, then
  `osascript -e 'set f to (open for access POSIX file "/path/out.png" with write permission)'
  -e 'write (the clipboard as «class PNGf») to f' -e 'close access f'`. The Attachments folder
  itself is unreadable (§15) and Claude cannot write image bytes from a chat attachment to disk.
- ⚠️ Profile media must be at a SHORT public URL: Twilio caps `logo_url` at **256 chars**, which
  rules out Airtable attachment URLs (they are far longer). It must be served by the app, which
  means the image has to be committed AND deployed before the profile can be set.

## 27. Payment overpay incident + fix (2026-08-24, commit `922845e`) + per-order-address scope
**Incident:** Order `NL-20260829-B84A` (Daniel/brother, +14806366814) owed **923.01 USDT**; he sent
**932.195761 USDT** on Ethereum (a real, confirmed tx `0xab50f217…`, +$9.19 overpay — likely a 923→932
transposition). `verify_usdt_eth` auto-accepted overpay only up to a flat **$5**, so the watcher never matched
it and the order sat `awaiting`; the customer got "finance doesn't see it yet". Third time this class bit us
(cf. §5 the 279.24/279.01 case). Diagnosed via: Airtable order (`expected_amount` 923.01, still awaiting) vs
Etherscan `tokentx` to `ETH_ADDRESS` (932.20 received) — the delta exceeded the $5 band.
**Fix (deployed, live-verified):** widened USDT auto-accept to `max(10% of expected, $30)` over / −$0.02 under
(BTC under 2% / over 10%); switched the cross-order guard to *closest-match*; and added the payment-watcher
**loose-scan review email** + `payment_flagged` field (see §5). After deploy the prod watcher auto-remediated
B84A (marked paid, messaged Daniel for his address). Overpay of $9.19 is his to refund/credit.

**Root fragility:** we match payments by AMOUNT on ONE shared receiving address (`ETH_ADDRESS` / `BTC_ADDRESS`).
That is inherently guess-prone (over/under/ambiguous), which is why this keeps recurring. The bulletproof
architecture is a **unique receiving address per order**.

### Per-order receiving address — scope (NOT built)
**Concept:** give every order its own fresh receiving address. Then ANY payment to that address unambiguously
belongs to that order — no amount-matching, over/underpay becomes a pure business decision, and the whole
failure class disappears.
**Key mechanic (safe):** use an HD wallet (BIP32). From the **extended PUBLIC key (xpub)** you can derive child
ADDRESSES without any private key. The server stores an `addr_index` per order and derives
`address = derive(xpub, index)` — it **never holds keys**; keys stay cold in your wallet. This is exactly how
exchanges mint per-customer deposit addresses.
**Flow changes:** order create → assign next `addr_index` → derive + store the order's address → payment
instructions use THAT address → watcher checks each awaiting order's OWN address (`get_awaiting_orders` already
gives the per-order context) for any inbound ≥ (expected − dust) → mark paid.
**Chain feasibility (matching works on all; difference is only the treasury/sweeping chore):**
- **BTC — easy.** xpub from Electrum or a hardware wallet (Ledger/Trezor). UTXO model → sweep funds freely, no
  per-address gas. Clean win; do this first if we go this route.
- **USDT-ERC20 — matching works, sweeping is the chore.** Derived ETH addresses can RECEIVE + be watched fine,
  but moving USDT off each derived address needs ETH gas AT that address (pre-fund a little ETH per address, or
  deploy forwarder contracts like commercial processors). Funds can be swept lazily/batched — it does not block
  verification. If per-order on USDT is wanted, easier on **Tron/Solana** (cheap native fees) than ETH.
**What Jordan must provide:** an **xpub** from a wallet HE controls. ⚠️ **Phantom does NOT expose an xpub** —
this needs a different wallet (Electrum for BTC; a hardware wallet or seed-based tool for the xpub). Keys/seed
never touch the server.
**Effort:** ~a day for BTC-only per-order (derivation lib + `addr_index`/`order_address` fields + wire the
watcher/instructions). ETH/USDT per-order adds the sweeping/gas operational piece. Alternative: self-hosted
**BTCPay Server** (BTC) offloads the whole thing but is more infra.
**Recommendation:** the 2026-08-24 amount-band + review-email fix makes the CURRENT shared-address setup robust
for this volume (auto-accept common cases; email on anything unusual). Move to per-order addresses only if
payment reliability must be absolute — start with **BTC via xpub**, keep USDT on the (now-fixed) shared address
unless we also move USDT to Tron/Solana.

## 28. Operator alerts fixed + audit (2026-08-29)
**The bug: a >100-kit prospect was a SILENT dead end.** `_enter_manual_mode` sets stage `manual`,
tells the customer "I must check with my boss, one moment", and calls `_notify_operators`. That
function early-returned when `OPERATOR_NUMBERS` was unset (it was), printing "would have alerted"
to stdout and nothing else. Meanwhile `messaging_agent.py` (stage `manual`) returns `""` for every
subsequent inbound — Lily goes **completely silent**. So the single highest-value prospect type
(100+ kits, negotiating below cap) got stalled and then ghosted, with no alert to anyone.

**Two failure layers, both fixed:**
1. `OPERATOR_NUMBERS` empty → no alert at all.
2. Even set, it would NOT have worked: `_notify_operators` sent **freeform** WhatsApp with no
   window check. Freeform only delivers inside the recipient's 24h window (error 63016); an
   operator who had not just messaged us throws, and the `except` swallows it. Same silent-drop
   class that lost the July warehouse manifests.

**Fix (this session):** `_notify_operators` is now **EMAIL-FIRST and email always fires** — new
`OPERATOR_EMAIL` setting, defaulting to `REPORT_EMAIL` (then `GMAIL_USER`), so alerts reach the
team even if nobody sets it. WhatsApp is a best-effort extra for any `OPERATOR_NUMBERS`. Email has
no 24h window and needs no template approval.

⚠️ **`OPERATOR_NUMBERS` is deliberately EMPTY and should stay that way for now.** Jordan
(+14805893947) and Daniel (+14806366814) both want their numbers free for test orders — and
`_is_operator` is checked at the TOP of `handle_inbound`, BEFORE conversation/stage handling, so
any number listed there can never behave as a customer again. Daniel's number in particular has
**249 messages** and is the primary live test line (it placed B84A).
**CONSEQUENCE — know this:** with no operator number set, you get NOTIFIED by email but cannot
relay/`release` through the system (those are inbound-WhatsApp commands from a recognized operator).
The prospect stays frozen in `manual` until a human contacts them out-of-band, or a redeploy wipes
the in-memory `_lead_stage`. If you ever want in-system relay back, add a THIRD number as the
operator line — never Jordan's or Daniel's.
⚠️ If you do set `OPERATOR_NUMBERS`, entries MUST carry the `whatsapp:` prefix
(`whatsapp:+1480...`). `TWILIO_PHONE_NUMBER` was deleted this session, so a bare number now
resolves to an empty `from_`, throws, and is swallowed — silent failure that looks configured.

### Other changes this session (2026-08-29)
- `TRACKING_CONTENT_SID` → `HX562f612553f60b54cf12ed9feaa586f8` (`northline_tracking_booked`, now
  APPROVED). Customers previously got "your order **has shipped**" while `fulfillment_status` was
  only `labeled` — the DIEGO26 customer got that on 2026-08-27. Now correctly says "shipment is
  **booked**". Both templates take one variable ({{1}} = tracking), so it was a drop-in.
- **Daniel's email switched everywhere** gmail → `daniel@northlinesupplies.com` (`REPORT_EMAIL`,
  `MANIFEST_CC`, this doc). ✅ **Delivery CONFIRMED 2026-08-29** — Daniel received the prod test send,
  so the mailbox is provisioned and not bouncing. That address now carries the weekly report, the
  manifest CC, health alerts AND large-order operator alerts, so if it ever stops working, all four
  go dark together.
- **Twilio numbers released**: `+15014178514` and `+18774692290` (both unused, $3.30/mo). Only
  `+85292909474` remains. `TWILIO_PHONE_NUMBER` (which held the released toll-free) was DELETED.
  Safe because Leads store phones as `whatsapp:+1...`, so every live send path takes the
  `twilio_whatsapp_from` branch. `SUPPLIER_WHATSAPP` lacks the prefix but is only ever an
  exclusion filter (`transcript_reviewer._excluded`), never a send target.
- **`WAREHOUSE_WHATSAPP` DELETED.** It still held the FIRST warehouse rep's number
  (`whatsapp:+8613418806654`) — the rep who ghosted us and is holding lost order 0F9D. It was the
  fallback used only when `WAREHOUSE_EMAIL` is unset, so if that var were ever cleared, daily
  manifests carrying customer names and addresses would have routed to a former contractor. The
  fallback was also already known-broken (the July WhatsApp manifests were all `undelivered` — the
  same 24h-window problem), so removing it costs nothing. `_send_whatsapp` guards on the empty
  value and returns False with a log line; it cannot crash the scheduler.
- **Twilio auto-recharge raised** to threshold $25 / reload $100 (was <$10 → $20). Reason: fixed
  costs were $18.30/mo against a $20 ceiling — no buffer. NOTE the recurring charges are pure
  number rental, NOT usage: $15.00 on the 16th (HK number), $1.15 on the 22nd, $2.15 on the 23rd.
  Actual messaging is ~$1/mo. After the releases, fixed cost should drop to ~$15/mo.

### Verified-resolved (were listed open in §14)
- HK regulatory bundle is **`twilio-approved`** (was pending-review).
- Orphaned Orders `status` field is **deleted**.
- **DIEGO26 ran end to end for real**: `NL-20260822-DDD6`, $3,393.64 USDT paid 2026-08-19,
  `factory_notified=True`, tracking `ZS23651014299` sent. The artwork + factory path is proven.
- Airtable usage is **350/1000** (Messages 312) — ~26 msgs/week, so ~6 months of headroom. Less
  urgent than §14 implies, but still the table that will hit the cap first.

### Still open after this session
- ✅ **The two old "parked" orders are CLOSED business — do not re-flag them.** Their Airtable
  records were deliberately LEFT AS-IS (both still read `in_bulk_order` with no tracking), because
  both predate Jason and their real outcomes are known:
    - `NL-20260704-0F9D` (paid Jul 3) — shipped by the FIRST warehouse rep, who then ghosted us.
      **Written off as lost**; assume it is not coming. No `lost` option was added to the
      `fulfillment_status` single-select — deliberately, to avoid a schema change for dead history.
    - `NL-20260808-D4C1` (paid Aug 2) — **shipped and RECEIVED by the customer.** Fulfilled by a
      third warehouse vendor we were trialling and no longer use; they never entered tracking in
      our system, which is why the record looks incomplete.
  Both carry `legacy_warehouse=True`, so they are already excluded from Jason's manifest and need
  no cleanup. The stale-looking records are expected — treat them as historical, not a backlog.
- 🟡 **`NL-20260829-B84A` (paid Aug 26) is the one LIVE order awaiting shipment** — at `recorded`,
  address collected, correctly appearing on Jason's manifest alongside `DDD6` (which still needs a
  vial photo). Jason IS receiving the daily manifest email; he is new to the cadence and is being
  coached through the process. Not a technical fault — verified the manifest query returns both.
- 🟡 `WAREHOUSE_WHATSAPP` still points at the OLD rep (`+8613418806654`), not Jason. Fallback-only
  (used if `WAREHOUSE_EMAIL` is unset), but if that ever happens, manifests with customer names and
  addresses route to a former contractor.
- 🟡 `$9.19` overpay on B84A is Daniel's to refund/credit.
- 🟡 **WhatsApp profile text still blank + customer-visible** (`about`, `description`, `websites`,
  `emails`, `vertical`). Logo IS set; sender ONLINE, quality HIGH. **Copy is DRAFTED and approved-
  pending — paste it in when Jordan signs off** (2026-08-29). It deliberately mirrors the language
  already on northlinesupplies.com so the profile makes no claim the website does not:
    - `about` (139-char cap; this is 128): "Research supply company serving licensed institutions
      and professionals. Research use only — not for human or animal consumption."
    - `description` (512 cap; ~340): "Northline Group LLC, operating as Northline Supplies, is a
      research supply company providing high-quality compounds to licensed research institutions
      and professionals.\n\nAll products are sold strictly for research use only. Products are not
      for human or animal consumption, and are not intended to diagnose, treat, cure, or prevent
      any disease."
    - `websites` → https://northlinesupplies.com ; `emails` → jordan@northlinesupplies.com
    - `vertical` → **Professional Services**. Deliberately NOT "Medical and Health": that implies a
      therapeutic positioning which contradicts our research-use-only language and invites scrutiny
      of a peptide seller.
    - `address` → **LEFT BLANK ON PURPOSE** (Jordan's call, 2026-08-29). The registered address
      (233 N Heathermoor Ln, Kaysville UT) appears to be residential and is the B84A ship-to. It is
      already on the website, but a WhatsApp profile puts it one tap from every unvetted prospect
      who messages the ad number. Do not fill this in without asking again.
- 🟡 `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` are set in Railway but **SendGrid is referenced
  nowhere in the code** — dead config, safe to delete.
- 🟡 `static/northline_banner.jpg` still committed and unused.
- 🟡 Airtable "Table 1" (3 junk records from the default base) — safe to delete.
- 🟡 Credential cleanup: rotate the GitHub PAT, delete the Cloudflare API token, delete the Railway
  token generated this session.
- Per-order receiving addresses (§27) — still the real fix for payment matching; not built.

### Handoff upkeep is now automatic (2026-08-29)
Jordan's standing instruction: *"I don't want to have to keep manually giving context and updating
handoffs."* Two mechanisms now cover that, so neither he nor a future session has to remember.

1. **`CLAUDE.md`** (repo root) loads automatically in any Claude session opened on this directory —
   Cowork, Claude Code, anywhere. Nobody pastes context in ever again. It points here and carries the
   rules that have actually cost money (consoles lie, deploys miss, the WhatsApp 24h window, never
   operator-ize Jordan's or Daniel's numbers).
2. **`.claude/handoff-drift.sh`**, wired as a **SessionStart hook** in `.claude/settings.json`. It
   compares HEAD against the last commit that touched `HANDOFF.md`. On drift it injects the commit
   list into the session's context at startup and instructs it to bring this file current
   unprompted. It is SILENT when the handoff is current, so a clean start stays quiet.
   - It is a `command` hook because `agent`/`prompt` hook types are only available on TOOL events
     (PreToolUse/PostToolUse/PermissionRequest), not `SessionStart`.
   - It is `SessionStart`, not `Stop`: a Stop hook that blocks until the handoff is written risks a
     loop and nags mid-task. Catching drift at the START of the next session is safe and sufficient.
   - It also reports uncommitted tracked changes.
   - To silence it deliberately, update `HANDOFF.md` — that is the point.

`CLAUDE.md` also states the expectation directly, so a session that somehow starts without the hook
still knows the handoff is its job.

**So the workflow is: just work. The handoff keeps itself honest.** If a session ends without the
handoff being updated, the next one is told immediately and fixes it.

## 29. Catalog drift → unpriced lines shipped FREE (2026-08-30) — FIXED

**The bug.** Five products were spelled differently in `core/pricing.py` (the cost basis) and
`core/price_image.py` (the customer price sheet). Neither name worked for both lookups:
`get_list_price()` only resolved the pricing.py spelling, `get_sku()` only the price_image.py one.

| SKU | price sheet spelling | cost-file spelling |
|---|---|---|
| `GLOW70` | BPC+TB+GHK Blend | BPC+GHK-Cu+TB Blend |
| `KLOW` | BPC+TB+GHK+KPV | BPC+TB+GHK-Cu+KPV Blend |
| `CD5` | CJC-1295 (w/ DAC) | CJC-1295 (with DAC) |
| `CP10` | CJC+Ipa Blend | CJC+Ipamorelin Blend |
| `MIC10` | MIC (Lipo+B12) | MIC (Lipo-C+B12) |

**Why it cost money.** The whole clamp in `_validate_line_items` was wrapped in
`if list_pk is not None:`. An unresolvable line therefore skipped the floor check, the discount cap,
AND the `if unit <= 0: unit = list_pk` backfill *together*. Two live failure modes:
- Lily quotes below cost → passed through unclamped (verified: `CJC+Ipa Blend` sold at $20 vs a
  $56.70 floor, `clamped=False`).
- Lily omits `unit_price` → **line_total $0.00 and the kits ship free.**

`core/deals.py` uses the price-sheet spellings, so three DIEGO26 lines were in this state.

**It was a class of bug, not five instances.** Fuzzing 930 plausible product/spec spellings drawn
from our own live price sheet found **36 failures across 8 SKUs via two unrelated root causes** —
the name drift above, plus `DSIP`, whose rows were the only ones in `CATALOG` written without the
` x10` spec suffix (`find_item` matches specs by prefix, so `"10mg x10"` never matched a bare
`"10mg"` row, three candidates tied, and it returned `None`). Lily writes these strings freely, so
the input space cannot be enumerated.

**The four fixes (one commit).**
1. **Fail closed** — `_validate_line_items` now returns `(items, clamped, unpriced)` and EXCLUDES
   unpriceable lines from `items`. Both call sites escalate via `_enter_manual_mode()` — the same
   path the large-order handoff uses (§28) — so the buyer gets a warm stall, an operator is alerted,
   and no order is ever built from a partial basket. **This is the fix that matters**: it covers
   every case not yet found, including products added later.
2. **`core/aliases.py` (new)** — one `canon()` both `pricing.py` and `price_image.py` normalize
   product names through. Deliberately an ALIAS LAYER, not a rename: `price_image.CATEGORIES` drives
   the customer price-list image/XLSX/PDF and `pricing.CATALOG` names are injected into Lily's prompt
   by `get_catalog_text()`, so renaming either side would change text a real buyer sees. **No
   displayed string changed.** Unknown products pass through unchanged, so the table cannot break a
   product it does not know about.
3. **DSIP specs** normalized to the standard `"Nmg x10"` form.
4. **Sermorelin collapsed to one cost basis.** `CATALOG` carried both `Sermorelin`
   (5mg cost $14.90 → $90) and `Sermorelin Acetate` (5mg cost $37.24 → $224) — the same product
   (acetate is just the salt form), 2.5x apart. The cheap rows are correct: the sheet prices ARE
   derived from them (`ceil(14.90*6)=90`, `ceil(19.72*6)=119` = SMO5/SMO10), $19.72/10mg fits its
   class (CJC-1295 no-DAC 10mg $26.21), and pricing off the Acetate rows would have put SMO5 at
   **2.4x cost — below the 3x floor the guard exists to enforce**. The Acetate rows are removed; the
   name still resolves via the alias, now to $90/$119.
   - ⚠️ **This one is provisional.** Daniel is checking the real cost with the lab. If $37.24 turns
     out to be current, the *sheet* is what's wrong and SMO5/SMO10 have been selling under floor.
   - Side effect: this dropped the only 2mg row. 2mg is not on the price sheet and is not sold;
     a request for it now escalates to a human (fix 1) rather than shipping free.

**Verification — `tests/test_catalog_regression.py`, 173 assertions, all passing.**
- **No customer price moved.** Every one of the 155 sheet SKUs was priced before and after and
  diffed: **0 moved**, 5 went from `None` → exactly their sheet value, 5 gained a floor.
- Re-fuzzed: **0 of 930** spellings now price at `None` (was 36).
- The guard was tested by lifting the REAL patched `_validate_line_items` out of
  `agents/messaging_agent.py` via `ast` and running it against the real pricing module — not a
  reimplementation. An unresolvable line yields `items=0, unpriced=1`; a mixed basket with one good
  and one bad line stops the whole order.
- `test_no_sheet_price_is_below_its_own_floor` is what caught the Sermorelin problem. Keep it.

**One new customer-visible string** (per CLAUDE.md, Jordan approved building this; the copy itself is
worth a second look): *"Let me confirm the exact price on this one with my boss, dear — one moment,
I come right back to you."*

**Not yet done.** This was step one of consolidating the catalog into a single SKU-keyed source of
truth carrying product, mg, class (lyophilized vs liquid), price, `unit_weight_g`, box dims and label
file — the prerequisite for the labeling-manifest and weight-based shipping work. Still open there:
- `website/coa.html` holds a THIRD hardcoded copy of the catalog with its own spellings
  (`GLOW 70`, `KLOW 80`, `Melanotan II (MT2)`, `BPC-157 + TB-500 (Wolverine)`). Aliased for the
  five known cases; the rest are unaudited.
- Colloquial names customers actually type (`Glow`, `Klow`, `MT2`, `Wolverine`) are documented in
  Lily's prompt but are NOT in the alias table yet.
- **Bac water is the shipping trap.** `BAC10` is ~250 g/kit against ~75 g for a lyophilized kit, at
  $12 list — roughly $48/kg of value against ~$11,920/kg for `RT100`. `_shipping_fee()` gives free
  shipping over a $1,000 subtotal with **no weight term**, so ~84 bac water kits = $1,008 = free
  shipping on ~21 kg. `STW10` (Sterile Water) is identical and `LC216`/`MIC10` are also liquid — fix
  it as a liquid/supplies CLASS on the consolidated catalog, never as a `BAC10` special case.
- Agreed package split: **2 kg hard cap, self-imposed**, and *balanced* not greedy — `N = ceil(total
  / 2kg)`, then even out (3 kg → two ~1.5 kg packages, NOT 2.0 + 1.0). Do not hardcode a kit count;
  it falls out of the weights and changes with the mix.

## 29a. §29 deployed (2026-08-29, commit `1aa2007f`)

Built in Cowork, deployed from Claude Code on the Mac — the split CLAUDE.md now describes. Ran
`deploy_catalog_fix.sh` unchanged (it is in the repo root, untracked; safe to delete now).

- `tests/test_catalog_regression.py` — **173 passed** on the real tree before the commit.
- Force-deployed by SHA and confirmed the RUNNING commit is `1aa2007f`, not just that the deploy
  reported SUCCESS (§10). `/health` → 200.
- Independently re-checked the live failure modes against the fixed code:
  both KLOW spellings price at $220/kit with SKU `KLOW`; `CJC+Ipa Blend` quoted at $20 now clamps
  to $108 (`clamped=True`) instead of passing through; bare-spec `DSIP 10mg` resolves to `DS10`;
  `Sermorelin Acetate 5mg` → $90 (`SMO5`); an unresolvable product yields `items=0, unpriced=1` and
  escalates instead of selling.

**`pytest` is NOT installed on Jordan's Mac and is NOT in `requirements.txt`.** Installed it with
`python3 -m pip install --user pytest` (user site, so it stays out of the Railway image). Anyone
running the suite for the first time needs that one line. Left out of `requirements.txt` deliberately
— it is a dev-only dependency and the deploy does not need it.

**Still open from §29, unchanged by this deploy:** the smoke test through a real WhatsApp number has
not been run (needs a non-operator handset — see the script's closing banner); Daniel's lab check on
the Sermorelin cost is still outstanding, and if $37.24 is current then SMO5/SMO10 are selling under
floor; `website/coa.html` still holds a third hardcoded catalog; colloquial names (`Glow`, `Klow`,
`MT2`, `Wolverine`) are still not in the alias table; the bac-water shipping-weight hole is untouched.

⚠️ **The `origin` remote URL has a GitHub personal access token embedded in it** (`git remote -v`
prints it in the clear, and so does any tool output that includes it). Nothing here changed that, but
it should be rotated and moved to a credential helper.

## 30. Consolidated catalog + weight-aware shipping (2026-08-31) — DEPLOYED (see §30a)

Step two of the consolidation §29 started. §29 stopped unpriceable lines from shipping free; this
section gives the catalog a single SKU-keyed identity and, for the first time, a **weight**.

### `core/catalog.py` — one SKU-keyed view, JOINED not re-typed

Product facts lived in three files that each knew part of the truth: `pricing.CATALOG` (cost + the
names in Lily's prompt), `price_image.CATEGORIES` (SKU, category, the price the customer sees) and
`website/coa.html` (a third hardcoded copy). Nothing tied a SKU to its cost and nothing anywhere
knew what a kit weighs.

**It is deliberately a JOIN, not a fourth copy.** Re-typing 155 rows of live money data is the
riskiest edit available on this system. The catalog is built at import time from the two existing
sources and the join is asserted TOTAL, so those files stay authoritative for what they already
drive (the price-list image/XLSX/PDF, and the prompt text) and **drift between them is now a test
failure rather than an unpriceable order line** — the §29 root cause, closed structurally.

The join was already clean after §29: **155 cost rows ↔ 155 sheet rows, 1:1, no orphans.**

Each `Item` carries sku, product/spec (both spellings), category, unit, dose, vials, form, cost,
list price, floor price, `unit_weight_g` and `label_file`. `catalog.audit()` returns every way the
sources can disagree; the suite asserts it is empty.

### Weights (Jordan's measurements, 2026-08-31)

| what | grams |
|---|---|
| lyophilized kit | 75 |
| liquid kit (10 mL × 10) | 270 |
| NAD and Glutathione, every dose | 170 |
| **empty shipping box** | **350** |

Resolution order is SKU override → product override → form default, so a new dose of NAD needs no
edit and a new liquid product is classed automatically. **`liquid` is a CLASS derived from the spec's
unit of measure** — `BAC10` is not special-cased anywhere, exactly as §29 asked. It catches
`BAC10`, `STW10`, `LC216`, `MIC10` and anything liquid added later.

The 350 g box is per PACKAGE, not per kit, so it comes out of the cap: **1,650 g of payload** inside
a 2 kg gross limit.

### The bac water hole — closed by PRICING, not by a weight rule

The exposure was real: at exactly $1,000, **144 of the 155 SKUs fit in one box; bac and sterile
water need fourteen** (22.7 kg at ~$44 of value per kilo, against ~$11,920/kg for RT100).

Jordan's call was **not** to deny free shipping to heavy orders but to **price the carriage into the
product: bac water AND sterile water $12 → $17/kit.** Buyers keep the simple flat quote, and the
freight is paid at the till. Both moved together on purpose — they are the same 270 g of water at the
same $2 cost on adjacent lines of the price sheet, so any gap between them would just have moved bulk
buyers one row down. `shipping_quote()` is therefore byte-identical to the old `_shipping_fee()` — there is no
weight term in anything a customer sees, and a test asserts no one reintroduces one.

### Package splitting (`core/shipping.py`)

2 kg **gross** cap, balanced not greedy, via longest-processing-time bin packing: the 3 kg example
in §29 comes out **1500 g / 1500 g**, never 2.0 + 1.0. Package count falls out of the weights — no
kit count is hardcoded anywhere.

**The water exemption.** The 2 kg cap is about SEIZURE RISK, not carrier limits, and a box of
nothing but water has none (Jordan). So `catalog.UNRESTRICTED_SKUS = {"BAC10", "STW10"}` ships whole:
**84 kits is ONE box, not fourteen.** Sterile water is the same thing without the benzyl alcohol, so
it qualifies on the same reasoning; it was added alongside bac water because pricing the freight in
only works if both actually ship the cheap way.

The exemption is narrow and listed by SKU on purpose — it is **not "liquids"** (`LC216` and `MIC10`
are liquid, 270 g, and deliberately still capped) and not "cheap things". It is the specific products
whose contents are uninteresting at a border. A test asserts exactly which SKUs are in it, so
widening it is a decision someone has to make on purpose.

Both arrangements are built and the one with **fewer packages wins**, so a single bac water kit
rides along inside a capped peptide box rather than costing a second parcel, while bulk water
separates. A test asserts a restricted SKU can never end up in an uncapped box. Uncapped is not
unlimited: a package over 30 kg is FLAGGED (`over_courier_limit`), not silently split, because
splitting would contradict the rule.

### Other fixes in this pass

- **Colloquial names are in the alias table at last** — `MT2`, `Melanotan 2`, `Wolverine`,
  `Glow`, `Bac Water`, `5-Amino 1MQ`, `SWFI` and more. These failed CLOSED since §29 (no free kits),
  but every one stalled an order and pinged an operator — the exact babysitting this system exists to
  remove. **This also closes the `website/coa.html` third-copy drift**: all 25 of its rows now
  resolve to their own SKU, and a test parses that file and proves it.
- **EPO spec `3000IU` → `3000IU x10`.** It was the only row in the catalog without the suffix — the
  same shape as the DSIP bug in §29 — and priced correctly only because EPO has a single row and
  fell through `find_item`'s single-candidate branch. A second EPO dose would have made it
  unpriceable. Jordan confirmed it ships as a ten-vial kit. **No price moved.**
  - Cosmetic, not fixed: its SKU on the sheet is **`EP0` with a digit zero**, not the letter O.
    That string goes to the supplier on the weekly bulk order.
- **Internal freight telemetry.** Every placed order logs kg, package count and $/kg, and an
  operator is emailed when free shipping goes out on **4+ packages** — so the next cheap-and-heavy
  product is caught on its first order, not on a courier invoice. Wrapped so it can never break an
  order.

### ⚠️ The regression suite could not catch a price edit — fixed

`test_no_price_moved` compares `get_list_price()` against `price_image.CATEGORIES` — **the price
sheet itself**. That correctly catches the two sources drifting apart (the §29 bug) but it cannot
catch a price being *edited*, because editing the sheet moves the thing it compares against. Its own
docstring claimed a `BASELINE_LIST_PRICES` "captured BEFORE any change"; **no such constant ever
existed.** The suite written to stop a revenue leak would have sat green through a fat-fingered zero
on the customer price list.

`tests/test_price_baseline.py` is now that snapshot: all **155 prices hardcoded** at commit
`1aa2007f`, plus an `INTENTIONAL_CHANGES` log recording who decided each move and why. Verified it
bites — `$894 → $850` on RT100 (still far above its floor, so every other test stays green) fails
only this one. **Changing a price now means editing the sheet, the baseline and the log in one diff
a human reads.** Do not regenerate it wholesale to turn a red test green.

### Verification — 397 assertions, all passing

- **Exactly two prices moved**, and they are the intended ones. Every sheet SKU was priced under the
  pre-change code (from `git show HEAD:`) and again after, and diffed: `BAC10 12.0 → 17.0` and
  `STW10 12.0 → 17.0`, nothing else, no SKU added, removed, or newly unpriceable.
- The live `_shipping_fee` was lifted out of `agents/messaging_agent.py` with `ast` (the §29
  technique) and compared to the old rule across every boundary — **identical everywhere**.
- Anything that PRICES also WEIGHS, over ~900 spellings. If a line could price but not weigh it
  would pass the §29 guard and reach shipping at an unknown weight — the same hole one layer down.
- Splits stay under the cap and balanced across order sizes from 1 to 250 kits.

### To deploy (Claude Code on the Mac — Cowork cannot run git or Railway)

1. `python3 -m pytest tests/ -q` → expect **402 passed**. (`pytest` is user-site only, see §29a.)
2. Regenerate the customer price list, or both waters still show $12 on the image/XLSX/PDF the
   customer is sent: `python3 -c "from core.price_image import generate_price_list_image as g; g('en'); g('cn')"`.
3. Commit, push, **force-deploy by SHA (§10) and confirm the RUNNING commit**, not just SUCCESS.
4. Still open, unchanged: the WhatsApp smoke test through a non-operator handset; Daniel's lab check
   on the Sermorelin cost (§29); and the **GitHub PAT embedded in the `origin` remote URL** (§29a) —
   still unrotated.

## 30a. §30 deployed (2026-08-30) — and the stale price sheets it nearly shipped over

Deployed `f7d4e73` from Claude Code on the Mac. 402 tests green, catalog `audit(): clean`, Railway
force-deploy by SHA (§10), running commit confirmed as `f7d4e73` — the service had still been on
`1aa2007`, so auto-deploy had again not picked the push up (§10 holds).

**What `deploy_catalog_v2.sh` got wrong, and why it matters.** Its regeneration step ran
`generate_price_list_image()` only, which on the Mac writes to the **iCloud** folder — Jordan's own
copies. But `static/price_list.pdf`, `.xls` and `.xlsx` are **tracked in git**, were the 2026-06-22
build still showing **$12** water, and `main.py:138,173,186` regenerate them only `if not
...exists()`. On Railway those committed files exist, so the app would have gone on serving a $12
price sheet from `/price-list.xlsx`, `/北线集团研究肽价格表.xlsx`, `/price-list.xls` and
`/price-list.pdf` for as long as the files stayed committed — while `core/pricing` quoted $17. A
customer would have been sent one number and charged another.

Fixed by regenerating with `RAILWAY_ENVIRONMENT=1` so the writes land in `static/`, exactly as §10
step 1 already prescribes; the three binaries are in the deploy commit. Verified after deploy by
pulling `/price-list.xlsx` off production and reading the cells: both waters read $17.

The two PNGs are gitignored, so they were never part of this problem — they self-heal on each
deploy. That asymmetry is the trap: the file you check by eye is the one that was already fine.

**If a price ever moves again, regenerate with `RAILWAY_ENVIRONMENT=1` and commit `static/`.** The
iCloud copies are Jordan's reference, not what customers receive. `deploy_catalog_v2.sh` still has
the wrong command in step 3 — it was committed as-is for the record; fix or delete it before reuse.

Not done this session, unchanged from §30: the WhatsApp smoke test through a non-operator handset,
Daniel's Sermorelin cost check, and the GitHub PAT still embedded in the `origin` remote URL (§29a).
Also unresolved: this machine cannot write to the iCloud folder at all — macOS denies the process
access to `~/Library/Mobile Documents/...`, so Jordan's own price-sheet copies are still the old
build and need regenerating from a terminal that has Full Disk Access.

## 30b. The stale-price-sheet trap, made into a test (2026-08-31) — DEPLOYED (see §30h)

§30a caught by hand that the tracked `static/price_list.{xlsx,xls,pdf}` were still on $12 water while
`core/pricing` said $17. Catching it by hand is not a control — the same mistake is available on
every future price change, and the failure is invisible from the code. This section makes it a test.

**`tests/test_served_price_sheets.py`.** Reads the workbook production actually serves and compares
every SKU to the catalog. `openpyxl` is already a dependency, so this parses real bytes rather than
trusting that someone remembered to regenerate. Verified it bites: restoring the genuine pre-deploy
`price_list.xlsx` from `6225f5b` turns four tests red, naming both waters.

**The two formats that cannot be read back.** `.xls` needs `xlrd`, which is not a dependency, and the
PDF's numbers are drawn with a subsetted matplotlib font — `pdftotext` returns the headings with the
digits *missing entirely*, so no extractor recovers them. Those are covered by content hash instead:
`static/price_list.stamp.json` records a sha256 of each tracked sheet alongside the price fingerprint
they were certified against. Swapping the old `.pdf` back in is caught by that hash alone.

**⚠️ Building these anywhere but the Mac produces a sheet of hollow boxes.** The sheets are bilingual
and matplotlib substitutes a missing font *silently*. Rendering a container-built PDF to PNG on
2026-08-31 showed the entire Chinese footer as tofu — while the English half looked perfect, so a
glance at the file would not have caught it. `regenerate_all()` now calls `_assert_cjk_font_available()`
and raises `CJKFontMissing` rather than building. **This is why the sheets are regenerated on Jordan's
Mac and never in a cloud session** — worth knowing before anyone "helpfully" rebuilds them in CI.

**`deploy_catalog_v2.sh` is deleted**, per §30a's instruction to fix or delete it before reuse. It was
a one-shot for a deploy that has already happened and it carried the wrong regeneration command.
Replaced by **`regenerate_price_sheets.sh`**, which is the reusable procedure for the recurring risky
operation — a price change — and encodes both rules that were missed: `RAILWAY_ENVIRONMENT=1` so the
writes land in `static/` rather than iCloud, and `regenerate_all()` so the five formats are rebuilt
together and cannot drift apart.

**`core/price_image.py` gains three helpers**, all small and none touching the rendering: 
`price_fingerprint()` (hash of every SKU/price on the sheet), `verify_static_sheets()` (parses the
committed workbook, raises if it disagrees with `CATEGORIES`), and `stamp_static_sheets()`. Note that
`STAMP_PATH` follows the *same* iCloud/static branch as the artifacts — that is deliberate, and is
what makes the iCloud mistake self-reporting: regenerate to iCloud and the tracked stamp stays behind,
so the suite goes red instead of production going quietly stale.

**413 assertions passing.** The committed sheets were verified as current before stamping — the water
rows read $17 in the served workbook — so this deploy certifies the artifacts §30a already fixed
rather than rebuilding them.

### To deploy
`bash ~/peptide-agents/regenerate_price_sheets.sh` is NOT needed for this commit (the sheets are
already correct and now stamped). Just run the suite, commit, push, force-deploy by SHA and confirm
the running commit (§10). Use that script the *next* time a price moves.

Still open, unchanged: the WhatsApp smoke test through a non-operator handset; Daniel's Sermorelin
cost check (§29); the GitHub PAT in the `origin` remote URL (§29a); and Jordan's own iCloud copies of
the price sheets, which this machine cannot write to without Full Disk Access (§30a).

## 30c. The labeling manifest Jason's crew works from (2026-08-31) — DEPLOYED (see §30h)

The daily warehouse email carried a link and nothing else; the weekly workbook squashed each order
into one cell — `3x Retatrutide 10mg x10; 2x Retatrutide 100mg x10`. A crew reading that has to parse
a semicolon-separated string and keep 10mg and 100mg apart by eye. Now the email carries a workbook
shaped the way the bench actually works.

### `core/manifest.py`

**One row per SKU**: SKU · the product name AS PRINTED ON THE STICKER · strength · kits · a picture of
the sticker. Format follows the sheet Daniel produced, which the crew already reads.

**Two tabs**, because the two jobs are different and mixing them is how things get missed:
1. **Label & Ship** — no tracking number yet. This is the labelling work.
2. **Photo Before Ship** — tracked already, waiting only on the vial photo (§16/§18a).

They are disjoint and together are exactly `get_orders_needing_fulfillment()`; a test asserts it.

**Packages are broken out** via `shipping.split_packages()` — the crew packs parcels, not orders. Each
package is its own block, and **every package banner repeats its order ref** because a big order runs
over a page break and a package header stranded at the top of a page identifies nothing.

**A SKU with no sticker prints "⚠ NO STICKER ON FILE — do not label, ask Jordan" in red**, never a
blank cell. At a bench a blank reads as "no sticker needed".

Attached to `run_daily_manifest()`. The build is wrapped: a link-only email is degraded, but no email
is a day of orders nobody works on.

### The stickers — `static/labels/<SKU>.png`

138 label images came from the shared Google Photos album (the ones in Daniel's example workbook were
a FORMATTING REFERENCE ONLY and are not ours — those were the old design). **132 of 155 SKUs now have
artwork**, ~11 MB at 660px wide, 64-colour palette: the QR code and strength stay sharp and the whole
set is a tenth of the original.

**THE FILENAME IS THE MAPPING.** There is deliberately no hand-maintained SKU→file table — a table is
one more copy to drift, which is the failure mode of §29, §30 and of the album itself, whose
filenames use *its own* SKU codes (`5AM5`, `KLOW80`, `HGH10`, `AD5`, `SMO2`) — **a fifth spelling of
the catalog**, after pricing.py, the price sheet, coa.html and Daniel's manifest. `label_text.json`
holds what is printed on each sticker, because the stickers carry Northline's naming (`GLP-3 RT` for
Retatrutide, `WOLVERINE`, `MT-2`), not ours.

**The new labels print the strength** — `10mg` / `100mg` in large type — where the old ones had a row
of checkboxes to tick. That fixes the mislabelling risk at the source; the manifest's strength column
now agrees with the sticker beside it instead of doing the work alone.

**Mapping was verified, not assumed.** Matching is on product AND exact dose, never a single-candidate
fallback: `catalog.find()` inherits pricing's fuzzy matcher, which cheerfully paired the album's
**3 ml bacteriostatic water sticker with our 10 ml SKU**. Fine for pricing a typo, a mislabel on a
vial. `tests/test_labels.py` asserts, for all 132, that **the strength printed on a sticker equals the
strength of the SKU it is filed under** — verified to bite by filing the 10mg Retatrutide sticker
under `RT100`.

Two label generations overlapped on three SKUs. Jordan's call: **`BPC 157 / TB500` for the blend**
(so BB10 matches BB20, which exists only in that wording) **and plain `TB 500` for TB-500**, not the
`(THYMOSIN B4)` variant.

Three album files map to nothing we sell and are unused: bac water **3 ml** (we sell 10 ml only),
Sermorelin **2 mg** (removed in §29), TB-500 **2 mg** (we sell 5 and 10).

### Open

**23 SKUs have no sticker** — ACTH, Cardiogen ×2, Crystagen ×2, Dermorphin ×4, Dulaglutide ×2, EPO,
Humanin, Liraglutide ×3, Matrixyl, Melatonin, MIC, Pinealon 5mg, Snap-8 ×2, **Sterile Water**. Jordan
is checking which are discontinued; anything dead should come off the price list rather than get
artwork. Two look like album gaps rather than dead products: **sterile water** (just repriced to $17
in §30) and **Pinealon 5mg** (the album has the 10mg).

Rows print at 84pt so five fit a landscape page; each order starts on a fresh page and each subsequent
package breaks too. A single package with more than ~5 SKUs can still run over a page — the package
banner names its order, but the customer address will be on the previous page.

### To deploy
1. `rm -f agents/messaging_placeholder_ignore.py northline_labels.zip.bak` — a stray file landed
   there from a mistyped path in the Cowork session; it is junk and must not be committed.
2. `unzip northline_labels.zip -d static/ && rm northline_labels.zip` — the 132 stickers (binary, so
   they were delivered as a zip rather than 132 separate writes).
3. `python3 -m pytest tests/ -q` → expect **834 passed**. `test_labels.py` runs against the real
   artwork, so a missing or misnamed sticker fails here rather than reaching the bench.
4. Commit, push, force-deploy by SHA, confirm the running commit (§10).

## 30d. Dermorphin removed + the CJK guard was checking the wrong thing (2026-08-31) — DEPLOYED (see §30h)

**Dermorphin is discontinued** (Jordan). All four doses removed from `pricing.CATALOG`, from
`price_image.CATEGORIES`, and from the frozen baseline. It was not in `coa.html` or the WhatsApp
price list, so those needed no change. 155 SKUs → **151**.

A discontinued product now fails CLOSED like any unpriceable line (§29): a customer asking for
Dermorphin gets a warm stall and an operator alert, rather than being sold from a stale row.

`tests/test_price_baseline.py` gained a **`REMOVED_SKUS` log**. A SKU may leave `BASELINE` only by
being listed there with a date, who decided, and why — otherwise `test_baseline_covers_every_sku`
would let a product silently vanish from the price list, which is the same class of unreviewed
change the baseline exists to catch. Two tests assert a removed SKU is gone from every catalog copy
and no longer prices.

### ⚠️ The font guard from §30b did not work, and I only found it by looking

§30b added `_assert_cjk_font_available()` so nobody could build the bilingual price sheets on a
machine without Chinese fonts. It checked whether **any** CJK font was installed — including
`Noto Sans CJK SC` and `WenQuanYi Zen Hei`, which **the renderer never asks for**. The Chinese sheet
sets `rcParams["font.family"]` to four Mac fonts plus `DejaVu Sans`; DejaVu resolves everywhere and
contains no CJK glyphs at all, so it is precisely what draws the tofu.

The container had a CJK font installed. The guard said fine. `regenerate_all()` ran to completion and
overwrote `static/price_list.{png,pdf,xls,xlsx}` with sheets whose entire Chinese half was empty
boxes — the exact failure §30b was written to prevent, sailing straight through the guard against it.
The files were restored from git; nothing bad reached the repo.

**Fixed** by resolving each font the way the renderer will, with `fallback_to_default=False` so
matplotlib cannot quietly answer "DejaVu". The guard's list is now `CJK_FONTS`, and a test asserts it
equals the renderer's own list minus the DejaVu fallback — because *a guard that checks a different
list than the renderer uses will pass while the output is wrong*, which was the whole bug.

The lesson generalises: §30b's guard was written from reasoning about what could go wrong. It was
never watched failing on a machine that genuinely lacked the fonts. **Guards need to be seen to bite.**

### Expect 3 RED tests until the sheets are rebuilt

`test_served_price_sheets.py` fails on `DR2/DR5/DR10/DR20` — the committed `static/price_list.*` still
list Dermorphin, and they can only be rebuilt on the Mac. That is the §30b guard doing its job.

### To deploy
1. `rm -f agents/messaging_placeholder_ignore.py`
2. `unzip -o northline_labels.zip -d static/ && rm northline_labels.zip`
3. **`bash regenerate_price_sheets.sh`** — its first real use. Rebuilds all five formats to
   `static/`, re-stamps, and re-runs the sheet tests. The three reds above go green here.
4. `python3 -m pytest tests/ -q` → expect **829 passed**.
5. Commit, push, force-deploy by SHA, confirm the running commit (§10).

## 30e. Sticker coverage is complete — 151 of 151 (2026-08-31) — DEPLOYED (see §30h)

Jordan supplied the 19 remaining labels (`~/Downloads/individual`) — the §30c gap list minus
Dermorphin, which §30d removed. **Every SKU we sell now has artwork**, so the manifest's red
"NO STICKER ON FILE" warning should never appear in normal operation; if it does, something is
genuinely wrong rather than merely incomplete.

- All 19 matched on product AND exact dose, the same strict rule §30c uses — no fuzzy fallback, no
  manual assignment. `ACTH5 CAR10 CAR20 CRY10 CRY20 DUL5 DUL10 EP0 HUM10 LGT5 LGT10 LGT20 MAT10
  MEL10 MIC10 NP810 NP8100 PI5 STW10`.
- **Sterile water finally has one** — it was the gap that mattered most, being repriced to $17 in §30
  alongside bac water and actively sold.
- These are a different aspect ratio to the album set (1400×600 vs 1663×946) and carry a storage
  panel instead of the reconstitution checkboxes on the water/liquid ones. Same design family, and
  the strength is printed on every one. The manifest scales each sticker to fit its row, so the two
  shapes sit side by side without breaking the layout — checked by eye, not assumed.
- `static/labels` is now **151 stickers, ~11 MB**.

`test_labels.py` now runs 151 strength assertions instead of 132: for every SKU, the strength printed
on its sticker must equal the strength of the SKU it is filed under.

**883 passing**, with the same 3 reds from §30d — the committed price sheets still list Dermorphin
and only the Mac can rebuild them. Step 3 of the deploy clears them.

## 30f. The page IS the manifest now (2026-08-31) — DEPLOYED (see §30h)

§30c built the labelling sheet as an XLSX attached to the daily email. Jordan's correction:

> *"I want the actual web page to be formatted that way... and then within that, a spot where he can
> enter tracking for each package in the order, and that way it'll automatically feed to Airtable.
> I don't want it to be a separate spreadsheet that's siloed away that isn't automatically
> integrated."*

He is right, and the reason is worth keeping: **anything typed into an emailed workbook reaches
nobody.** It cannot become a tracking number in Airtable and it cannot message a customer. A sheet
that looks like the system but isn't wired to it is worse than no sheet.

### What changed

**`core/manifest_page.py` (new)** renders the whole page. It takes `core.manifest.build_view` output
— the SAME structure the workbook renders from — so the page and any printed copy cannot show
different pictures of an order.

- **Orders are collapsed.** Each is a native `<details>`: order ref, customer, package and kit count,
  and a progress pill. Tap to open. `<details>` is native HTML, so **expanding needs no JavaScript**
  — it works on a warehouse phone with a bad connection. JS only remembers the last tab, wrapped in
  try/catch because localStorage throws in private mode.
- **Open, it is the workbook**: packages broken out with their weight, then one row per SKU with the
  sticker pictured, the wording printed on it, the strength large and alone, and the kit count.
- **Two tabs** with live counts, same split as §30c.
- **The email has NO attachment any more** — it says what is waiting and links to the page.
- The white-label labelling-split notice (§25) is preserved, injected via a callback so the page
  module needs no `deals` import.

### Per-package tracking

One box per package. `POST /manifest/save` now takes `package` and `of`, MERGES into the order's
tracking rather than replacing it, and — the part that matters —

**an order is only marked tracked, and the customer only messaged, once EVERY package has a number.**

Telling a buyer "your shipment is booked" while two of three parcels have no label is worse than
waiting a few hours. Until then the order stays on tab 1 showing `1/3 tracked`.

**No Airtable schema change.** `tracking_number` is a `singleLineText` and stays one: a single-parcel
order stores the bare number exactly as before, and a multi-parcel order stores
`1/3 AAA | 2/3 BBB | 3/3 CCC`. `parse_tracking()` reads a bare value as package 1, so **every
existing order keeps reading correctly**. `set_order_tracking(..., complete=False)` records progress
without advancing `fulfillment_status` or setting `tracking_sent`; the default is still `True`, so
every existing caller behaves as it did.

### The customer message

`send_tracking_to_customer` now accepts a list. One parcel: **wording unchanged**, and a test pins
that. Several: *"Your order is on its way in 3 packages, so please look out for all of them"* + a
numbered list + a note they may not arrive the same day — because a buyer told about one parcel who
receives three assumes two are lost.

⚠️ Outside the 24h window only the approved template can be sent and **it takes ONE variable**, so
the numbers are joined into it (`"AAA, BBB"`). No template re-approval needed — §18a records that
approval as slow — but the template's own wording still says "has shipped", singular. Worth revising
when the `northline_tracking_booked` swap in §18a happens anyway.

### Verification — 909 passing

`tests/test_manifest_page.py` (26) covers the collapsed summary carrying no SKU rows, one tracking box
per package each naming its own index, partial tracking not completing, the storage round-trip
including out-of-order entry, escaping of customer data, and the no-JS requirement. The page was also
rendered in a real browser and screenshotted, collapsed and expanded — the layout was checked by eye,
not assumed.

The 3 known reds from §30d remain (Dermorphin still in the committed price sheets).

`core/manifest.build_labeling_manifest` is KEPT — it shares the same view data and still produces a
printable workbook — but nothing emails it now. The page carries `@media print` rules so the crew can
print from the browser instead.

## 30g. Manifest sorted oldest-first, with the wait on every row (2026-08-31) — DEPLOYED (see §30h)

Jordan's concern, and it is a specific one worth recording: a delivery of fresh stock arrives from the
lab and Jason fulfils **whatever is in front of him**, so an order that has already waited two weeks
waits longer still. Sorting is the fix, but only if the age is visible — otherwise the queue silently
depends on nobody reordering it.

- **Oldest first**, and the page says so at the top: *"Oldest orders are at the top — please work
  down the list."*
- **Every row shows the date and the wait** — `8 Aug · waiting 23 days`. Amber at 7 days, red at 14.
  Not deadlines, just "impossible to scroll past".
- Dates are written `8 Aug`, never `08/12` — the crew reading this is in China, where that means
  something else.
- An order with no resolvable date sorts **last, badged "date unknown"**. Every order on this page is
  paid, so a missing date is an oddity that must not jump the queue by being unparseable.

### ⚠️ `created_at` is dead, and two sorts were relying on it

`created_at` is in the Airtable schema (`setup_airtable.py`) but **nothing anywhere writes it** —
`create_pending_order` does not set it. It is empty on every order.

Two sorts in `core/airtable_client.py` keyed on it to pick "the most recent" of a customer's awaiting
orders: `get_awaiting_order_for_phone` (rebuilds payment state after a redeploy wipes memory, §4a) and
the promo-code supersede path (§24). With an empty key the sort is a no-op and they returned whatever
Airtable listed first — so with two awaiting orders they could pick the wrong one, in exactly the
payment-matching area §5 and §27 exist to protect.

Both now use `_newest_first`, which prefers `paid_at` and falls back to Airtable's own `createdTime` —
always present, no schema change. A test covers it.

The manifest's own date follows the same order: **`paid_at` → `createdTime` → the date inside the
order ref** (`NL-YYYYMMDD-…`). `paid_at` first because the customer's clock starts when their money
arrives, not when a record was made.

⚠️ Also noticed: **`paid_at` is written by `mark_order_paid` but is NOT in `setup_airtable.py`**.
It clearly exists in the live base — payments demonstrably work — so the setup script is stale rather
than the code being broken. Worth knowing before anyone rebuilds a base from that script and wonders
why payment recording 422s.

**920 passing** (37 in `test_manifest_page.py`), same 3 known Dermorphin reds.

## 30h. §30b–§30g deployed (2026-08-31)

Deployed `67dc866`. Railway force-deploy by SHA (§10); the service had been on
`f7d4e73`, so auto-deploy missed the push again — that is now five for five, treat
§10 step 2 as mandatory rather than a fallback.

**Verified against production, not just the deploy status:**
- running commit `67dc866` == HEAD; `/health` ok
- `/price-list.xlsx` off production is **byte-identical** to the committed
  `static/price_list.xlsx` — 151 products, **0** Dermorphin rows, both waters $17
- `/manifest` returns **403** with no token and with a wrong token
- `/static/labels/SM5.png` serves 200 image/png, so the artwork deployed with the app

923 tests pass. §30d's "expect 829" was written before §30e–§30g added tests; 923
is the current figure. If you are reading an older section's expected count, trust
the newest one.

### Dead code this deploy created — `build_labeling_manifest`

§30f removed the workbook attachment from the daily email, which was
`build_labeling_manifest`'s only production caller. The function (≈200 lines in
`core/manifest.py`) and the 18 tests in `test_manifest.py` that exercise it now
prove the correctness of something nothing reaches. It was left in deliberately
rather than deleted during a deploy — but do not read those 18 green tests as
evidence the crew's sheet works, because the crew no longer gets a sheet. The page
is the manifest (§30f). Delete or revive it as a separate change.

### The placeholder file, resolved

`agents/messaging_placeholder_ignore.py` (the §30c step-1 stray) is gone. It was
NOT identical to `agents/weekly_report.py` — it was the older draft that still
built and mailed the workbook, superseded by §30f's decision. Checked before
deleting; the tracked file carried the newer behaviour.

### Still open, unchanged

The WhatsApp smoke test through a non-operator handset; Daniel's Sermorelin cost
check (§29); the **GitHub PAT still embedded in the `origin` remote URL** (§29a),
still unrotated; and Jordan's iCloud reference copies of the price sheets, which
this machine cannot write (macOS denies the process access to
`~/Library/Mobile Documents/…` — see §30a). `regenerate_price_sheets.sh` writes to
`static/` on purpose and does not touch them.

## 30i. Sheet-style rows + a vial photo per package (2026-08-31) — DEPLOYED f95bc3f

Two revisions from Jordan after seeing §30f live.

### The rows are a table again

§30f rendered each SKU as a flex row with the sticker on the LEFT. Jordan wanted the workbook
layout the crew already reads — so the rows are now a real `<table>`:

| SKU | Product | Size | Kits | Sticker Label |

**Sticker on the RIGHT**, matching Daniel's sheet, because that is the column they look at while
they work. `Size` is the strength on its own — the anti-mix-up column from §30c, now with a header
over it. A test pins both the column ORDER and that the sticker is last, so a future tidy-up cannot
quietly move it back.

### One vial photo per PACKAGE

Photos were per order; tracking was already per package. A single photo of a three-parcel order
proves nothing about the other two, so tab 2 now shows an upload box for each package and a
`1/2 photographed` pill on the headline.

**No Airtable schema change.** `vial_photo` is an attachment field, which holds a LIST, and
`upload_attachment` appends. The package number rides in the FILENAME
(`vials_<ref>_pkg2of3.jpg`) and `parse_photo_packages()` reads it back. An attachment with no
package marker counts as package 1 — that is **every photo taken before today**, so old orders read
as photographed rather than suddenly looking incomplete.

The customer is messaged **only once every package is photographed**, same rule as tracking (§30f):
that message is the final "about to dispatch" signal (§18a) and must not fire on a partial job. All
the photos go in ONE WhatsApp message — Twilio accepts up to 10 media items — with the body naming
the package count.

⚠️ **Outside the 24h window only the first photo was sent.** Fixed in §30j — and note that this
section's claim that it "needs a new template approval" was WRONG. Read §30j before acting on it. Note the bug this nearly caused: passing the list straight into
`content_variables` would serialize a JSON array into a single-value variable and Twilio would
reject the whole send — there is a test for it.

### One bug worth recording

Wiring the photo form through `_package()` left the old signature `with_tracking: bool` in place
while the caller began passing `mode` (a string). `"photo"` is truthy, so **the photo tab rendered
tracking boxes and no photo upload at all**. Caught by an existing test asserting tab 2 offers an
upload — the kind of thing that reads fine in review and is only caught by running it.

**933 passing.**

### A second bug, caught reviewing this before deploy

The rewritten upload handler dropped the early `return` from its `except`, so a FAILED upload fell
through into the success redirect — and a duplicate unreachable `return` sat below it. The page
then told the crew **"Vial photo sent to the customer"** in two cases where nothing was sent:

- the upload threw, and
- the job was PARTIAL (1 of 3 packages), which this very section says must not message the customer.

Either one silently ends the work: nobody re-uploads a photo the page called sent, and nobody
chases a customer the page said was messaged. The green banner was the whole signal.

The handler now reports what it actually did — `sent`, `partial:have:need`, `savedonly` (stored but
the WhatsApp send failed) or `failed` — and only `sent` claims the customer was messaged. Failures
render in a new red `.warn` style rather than the green one, because the crew reads colour before
words. `test_manifest_page.py` pins each of the four banners, that a `.warn` style exists at all,
and that main.py still branches on all four — so the tests cannot pass against a drifted copy.

Both bugs in this section are the same shape: a signature or a control-flow edge changed underneath
code that still read correctly. **933 passing did not catch either.** Tests covered what the page
renders, not what it says after a write fails.

### To deploy
Code only — no labels to unzip, no price sheets to regenerate (the catalog is untouched).
`python3 -m pytest tests/ -q` → **940 passed**, then commit, push, force-deploy by SHA and confirm
the running commit (§10 — auto-deploy has now missed five for five).

## 30j. A WhatsApp template can only ever carry ONE image (2026-08-31)

**§30i was wrong and this is worth remembering:** it said the multi-package vial photo needed "a new
template approval". No approval can fix it. A WhatsApp template's header holds **exactly one** media
item — that is the platform's template structure, not a property of our approved template. Twilio
also locks a template to the media TYPE it was approved with. Verified 2026-08-31 against Twilio's
`twilio/media` docs and the WhatsApp template component reference.

So a template approval request would have been submitted, waited on (§18a records approvals as slow),
and come back still unable to do the thing. **Check whether the platform permits it before queuing an
approval.**

### What we do instead — Jordan's call

Inside the 24h window: unchanged, one message carrying every photo (Twilio caps media at 10).
Outside it: **one approved template message PER package**, reusing the existing `VIAL_CONTENT_SID`.
Every parcel gets its own full-resolution picture, which is what §16 needs — the customer's name and
address must be legible in the photo.

The alternative considered and rejected was stitching the photos into one grid: it would have been a
single message and a single charge, but each panel shrinks and the address is exactly what stops
being readable. Cost was not the deciding factor — an order only splits above ~26 kits (75 g/kit
against the 2 kg cap; bac water is cap-exempt and never splits), so these are large orders and rare.

Two consequences to know:
- The approved template has ONE variable (the image), so its body cannot say "1 of 3". The photos
  arrive as N messages with identical wording. Changing that needs a new template — and *that* is a
  copy change, so it is Jordan's to approve.
- `send_vial_photo_to_customer` now returns True only if EVERY photo went. A partial delivery
  returns False, so the order is **not** marked photographed and stays on the manifest's photo tab
  where someone can see it. A single failing photo no longer abandons the rest — each send is
  wrapped individually.

**943 passing.** The test that pinned "template gets one URL, not a list" is kept — that bug is still
live if anyone passes the list into `content_variables` — and joined by tests that every package
gets its own message, and that a partial delivery is not reported as sent.

## 30k. The order that shipped with no name (2026-08-31)

`NL-20260822-DDD6` went out with `ship_name` and `country` both empty. The customer had sent both
name and address. Here is the actual transcript:

```
02:33:07  IN   Landon Anderson
02:33:09  IN   773 E 9630 S Sandy UT 84094
02:33:17  OUT  ...I have your address: Landon Anderson / 773 E 9630 S / Sandy, UT 84094, USA
02:33:18  OUT  Almost done — just tell me what name should go on the package and which country...
```

**The name arrived FIRST, in its own message, two seconds ahead of the street.** `_parse_address`
extracted it correctly. The handler then threw the entire parse away because *that* message had no
`address_line1` and no `city`:

```python
addr = _parse_address(body)
if not addr or not addr.get("address_line1") or not addr.get("city"):
    ...  # → _handle_ordering, and every field we just parsed is discarded
```

With no digits in "Landon Anderson" it was routed to Lily's ordinary chat, which is why 02:33:17
*quotes the name back* — it was in her context, never in Airtable. The address message then filled
street/city/state/postal, leaving name and country blank, which produced the 02:33:18 follow-up. The
customer never replied again, and a week later it shipped nameless.

The bare-name fallback (`if not v and k == "ship_name" and ...: v = bare`) would have caught it — but
it only armed once `need_addr_fields` was set, which happens **after** an address is processed. The
name came first, so it missed the one net that would have held it.

### What changed

**Partial information is now kept.** Every inbound in `awaiting_address` contributes whatever it
contains via `_merge_shipping()`, which writes only fields the order does not already have and never
overwrites. What is still outstanding is recomputed from the **order record**, not from the current
message and not from `_pending_payments` — so it survives a redeploy and a customer who splits their
details across messages, in either order.

**A name is now required.** `_REQUIRED_SHIP = (ship_name, address_line1, city, country)`. The
warehouse cannot print a label or take the §16 photo without one, so it is as blocking as the street.

**Redeploy recovery was also broken for this case.** `get_paid_order_awaiting_address_for_phone`
queried `AND({payment_status}='paid',{address_line1}='')`, so an order that had a street but no name
could not be recovered at all — after a deploy the customer's name reply would again be plain chat.
It now matches a paid order missing ANY required field, and **excludes already-shipped orders** so
broadening it cannot swallow the next message of a customer whose old order is incomplete.

**A junk name is worse than none — but a stop-list that eats real names is worse still.** The first
version of `_plausible_ship_name()` listed auxiliaries (`will`, `can`, `do`, `may`) and silently
rejected **"Will Smith", "Can Yilmaz", "Do Van Hai", "Grace Do"**. That does not protect anyone; it
just loses the name somewhere else. The list is now short and holds only words that are never part of
a name (`update`, `thanks`, `tracking`, `paid`, …).

The real protection is **contextual, not lexical**: `_merge_shipping` only falls back to the raw
message when `ship_name` was in what we asked for on the previous turn. Outside that, an unrecognised
message is left alone rather than guessed at. The extractor path is unaffected — that is the path
that captures a name arriving unprompted, as Landon's did.

**The manifest makes it impossible to miss.** The name renders bold on its own line above the address
— that is what the rep copies onto the label and what must be visible in the vial photo. A nameless
order shows `⚠ NO NAME` in the collapsed row and a red *"do not label or photograph it yet"* block
when expanded, instead of the empty span that let this ship.

### Lily's wording was NOT the problem — no copy was changed

She asked correctly ("please send your shipping details... full name, street address, ..."), and the
customer complied. The loss was entirely in our handling. Every customer-facing string is byte-for-
byte what it was; the only additions are asks for `address_line1`/`city`, which previously had no ask
at all. If Jordan wants Lily to request the details as one message, that is a copy change and his
call — it would reduce the chance of a split, but the split is now handled either way.

**958 passing** (943 → 958). `tests/test_shipping_name_capture.py` replays the exact two-message
Landon sequence, and pins that recovery finds a nameless order but ignores a shipped one.

### Still to decide
`NL-20260822-DDD6` already shipped, so its record is only history — but it is still nameless in
Airtable. Backfilling "Landon Anderson" from the transcript is a one-line write; left undone
deliberately, since editing a shipped order's record is Jordan's call.

## 31. Negotiation removed; fixed prices by warehouse and order size (2026-09-03) — DEPLOYED, see §31a

Jordan, 2026-09-03: **stop negotiating.** Every price now comes off a sheet Daniel publishes,
chosen by two facts — which warehouse, and how many kits in total. There is no discount
authority, no floor to defend, no cap, and no large-order escalation, because there is nothing
left to escalate about.

    CHINA     1-24 kits   standard      25-99 kits   reseller      100+ kits   trading company
    US        one flat price at any quantity, and the only place we sell single vials

The 5-kit MOQ in Daniel's notes was **scrapped** by Jordan — 1 kit is a normal China order.

### Where the numbers live now

`core/price_sheets.py` is GENERATED from Daniel's four workbooks by
`tools/build_price_sheets.py`. Re-running that script on a new set of sheets is the entire
update procedure:

    python3 tools/build_price_sheets.py ~/Downloads

151 SKUs × 5 numbers is not something to re-type by hand, and CLAUDE.md says as much. Nothing
else in the repo holds a price any more: `price_image.CATEGORIES` kept its layout (which SKUs,
what order, which heading, the customer-facing spelling) but its NUMBERS are now looked up from
price_sheets at import, so the sheet and the quote cannot drift — the §29/§30b failure mode is
structurally gone rather than merely tested for.

**We kept OUR SKU codes.** Daniel's sheets renamed thirteen (`EP0`→`EPO`, `H8`→`HGH8`,
`LGT5`→`LIR5`, `PI5`→`PIN5`, `KLOW`→`KLOW80`, …). `static/labels/<SKU>.png` is the sticker
mapping and every Airtable order ever placed carries the old code, so renaming would orphan 13
sticker files and break history for nothing. `SKU_ALIASES` in the generator is the only place
his codes appear.

### The sheets had four real defects, and Jordan ruled on each

Found by cross-checking his four workbooks against each other and against the live catalog:

- **TB-500 listed twice** — `BT5`/`BT10` at $105/$160 and `TB2(BT)`/`TB5(BT)`/`TB10(BT)`
  ("TB500 CTHYMOSIN B4 Acetate") at $85/$160/$245, 17 rows apart. Same product.
- **Sermorelin listed twice** — `SMO5`/`SMO10` at $105/$135 and `SMO-2`/`SMO-5`/`SMO-10`
  ("Sermorelin Acetate") at $140/$255/$455, **75 rows apart**, the second block appended at the
  very bottom where it does not read as a duplicate. A 2.4× spread on the same product, and
  `find_item` would have returned whichever matched first.
- **`BB10` listed twice** under the SAME SKU code — $125 and $120.
- **`IP5`/`IP10` listed twice** at identical prices (harmless).
- **Dermorphin was back** — all four doses, four days after §30d removed them.
- **Etelcalcetide priced "Quote"** — unpriceable, and there is no negotiation path left to
  price it through.

Jordan's call: take the LOWER row on both duplicates, keep Dermorphin out, drop Etelcalcetide,
de-duplicate Ipamorelin. **The source workbooks in `~/Downloads` were edited to match** (162 →
151 rows each, same SKU set and order across all four) so the next regeneration cannot
reintroduce any of it. Dropping the higher blocks also drops the only 2 mg TB-500 and 2 mg
Sermorelin; neither was ever in our catalog. Note `SMO-2` was $140 for 2 mg against `SMO5` at
$105 for 5 mg — more money for less product.

### Catalog moves

151 SKUs before, 151 after: 148 carried over, 3 out, 3 in.

- **Out:** `RT80`, `TR80` (both live at $729 — simply absent from Daniel's sheets) and `STW10`
  (sterile water). Jordan: pulled **"for now until we confirm with lab they can sell it"**, so
  this is a pause, not a discontinuation.
- **In:** `SM60` $400, `SM100` $455, `TR120` $380. **None of the three has sticker artwork** —
  they will show the red *no sticker* warning on the labeling manifest until Daniel's crew
  supplies it. That is §30e's mechanism working, not a bug.
- 147 prices moved. Median **+12%**, but the long tail moved down hard: `50AM` −83%, `FN1` −71%,
  `TR100` −57%, `RT100` −52%.
- **Bac water $17 → $20**, which partly undoes the 2026-08-31 freight-pricing decision, and
  sterile water is no longer beside it to stay level with. `catalog.UNRESTRICTED_SKUS` is now
  just `{"BAC10"}`.

`catalog.RETIRED_LABEL_SKUS` is new: it exempts the three paused SKUs' artwork from the orphan
check **without** making them sellable. Dermorphin's stickers were deleted outright on
2026-08-31 because that call was final; these are explicitly temporary, and re-cutting artwork
to un-pause a product is waste. `test_paused_skus_keep_their_artwork_but_are_not_sellable`
asserts both halves.

### The baseline was replaced, not regenerated away

CLAUDE.md forbids regenerating `tests/test_price_baseline.py` to turn a red test green, and 147
prices moving at once is exactly the situation that rule exists for. So the old snapshot is
**still in the file**, renamed `BASELINE_2026_08_31`, and `test_every_price_move_is_accounted_for`
asserts that every single difference between it and today's `BASELINE` appears in
`PRICE_MOVES_2026_09_03` with both numbers. The change is wholesale but it is enumerated, and a
148th price that moved without being written down still fails.

New pins alongside it: `RESELLER_BASELINE`, `TRADING_BASELINE`, `US_BASELINE` (vial + kit), the
tier boundaries themselves (1/24/25/99/100/500), and a check that US prices do NOT move with
volume. The 3× floor assertions are gone — nothing can push a price down any more — and are
replaced by "no tier price is below cost", asked at the trading tier because that is the one
that would be.

### Two-pass pricing, and why

`_validate_line_items` now sums the kits FIRST and prices second. The negotiation-era code
looked the discount cap up per line with that line's own quantity; carried forward unchanged
that shape would quote a 25-kit order made of five 5-kit lines at the standard rate —
overcharging precisely the buyer who spread their order across products. It also now **discards
Lily's `unit_price` entirely** and substitutes the sheet price. Her number is kept only to
detect a mismatch, log it, and restate the real figure to the customer; there is no second
Claude call any more, because with fixed prices there is nothing to re-derive.

Manual mode still exists but has ONE remaining trigger: a line we cannot price (§29 fail-closed,
now including a China-only SKU asked for at the US warehouse — 121 of 151 SKUs).

### Warehouse selection

Lily asks which warehouse early; the prompt is built from that choice, so the catalog she sees
IS that warehouse's. `_warehouse` (phone → "china"/"us") holds it, `RESET` clears it, and it is
written onto the order record — **tolerantly**: Airtable rejects an entire create with 422 for
an unknown field, so a `warehouse` column Jordan has not added yet would take down every order.
It is attempted once, and on that specific failure retried without it and disabled for the
process. **Add a `warehouse` field to the Orders table and it starts populating with no code
change.** Until then, a mid-order redeploy loses it in memory — Lily re-derives it from the
recovered transcript on her next reply, which is why the prompt insists she set the field on
every turn and not just the one where the buyer chose.

The old prompt line *"We ship everything from China. We do NOT have US-based fulfillment. No US
warehouse."* was flatly false as of today and is gone.

### Shipping

China unchanged ($95 / free over $1,000 / $235 expedited — Daniel's note quotes the same
numbers). **US is $30 flat, full stop:** no expedited tier and **no free-shipping threshold**,
still $30 on a $5,000 order. Do not "fix" that by copying `FREE_OVER_USD` across — the China
threshold buys a four-week consolidated freight lane; a domestic overnight label costs what it
costs. The 2 kg split logic is untouched.

### A second served sheet

`static/price_list_us.xlsx`, built by `generate_price_list_us_xlsx()` and served at
`/Northline_US_Warehouse_Price_List.xlsx`. Deliberately **openpyxl, not matplotlib, and not
bilingual**: 30 rows of English for US buyers, so it has no font dependency, cannot repeat the
§30d tofu failure, and builds correctly off a Mac. `regenerate_all()` builds it alongside the
others so the formats cannot drift.

### State in Cowork: 831 passing, 12 red

The 12 are all missing static assets that only exist on the Mac — the label PNGs and the
`price_list.{xlsx,xls,pdf}` that must be rebuilt there. **That is the §30b guard doing its job:
the committed sheets still carry the old prices.** They go green at step 2 below.

### To deploy

1. `python3 tools/build_price_sheets.py ~/Downloads` — optional; `core/price_sheets.py` is
   already generated and committed. Run it only to prove it reproduces byte-for-byte.
2. **`bash regenerate_price_sheets.sh`** — rebuilds all six formats into `static/`, re-stamps,
   and re-runs the four price test files. The 12 reds go green here.
3. `python3 -m pytest tests/ -q` — full suite.
4. Commit `static/` or production keeps serving the old numbers (§30a).
5. Push, force-deploy by SHA, confirm the running commit (§10).
6. **In Airtable:** add a `warehouse` single-line-text field to the Orders table. Optional, but
   without it no order records which warehouse it shipped from.

### Still open with Daniel

- `LIR30` is labelled **20mg** on his sheet — the SKU says 30, the spec says 20. We followed the
  spec (it maps to our `LGT20`). His typo to confirm.
- `SLU-PP-332` on his sheet vs `SLU-PP-322` in our catalog. We kept ours; one of them is wrong
  and it is a real compound name.
- Artwork for `SM60`, `SM100`, `TR120`.
- Whether `RT80`, `TR80` and sterile water come back.
- The group volume discounts in his notes (10% at 5,000 kits/month rising to 50% at 100,000) are
  **not implemented** — that is a standing monthly-commitment deal, not an order-size tier, and
  Lily has no way to know a buyer's monthly volume. Left for a human.

## 31a. §31 deployed (2026-09-03) — commit `614dd64`

Ran `deploy_pricing.sh`. Both of the steps it could not script are now also done, so §31 is fully
live and nothing about it is outstanding.

- **Deployed `614dd64`** — force-deployed by SHA per §10, polled to SUCCESS with a matching
  `meta.commitHash`, `/health` returns ok. The new US sheet is being served:
  `/Northline_US_Warehouse_Price_List.xlsx` returns 200 at exactly the 6,107 bytes committed.
- **Airtable `warehouse` field created** on Orders (`fldI4OcZao18Ho0Ss`, single line text), via the
  metadata API. `_warehouse_field_ok` therefore never trips and orders start recording their
  warehouse with no code change, exactly as §31 designed. Note it is a DIFFERENT thing from the
  existing `legacy_warehouse` checkbox, which is the pre-Jason-handoff flag from 2026-08-19 and is
  unrelated — checked before adding, so the base does not now carry two columns meaning one thing.
- Sheets rebuilt into `static/` with `RAILWAY_ENVIRONMENT=1` before the tests ran, so the §30a
  stale-sheet trap is closed for this change: fingerprint `f8cc5b74e16963bd`. `PingFang HK` is not
  installed on this Mac and matplotlib says so ~600 times; the §30d font guard passes anyway
  because the renderer falls back to a CJK face that is present. Noise, not tofu.

### The one red test, and why it was the test that was wrong

The suite stopped the script on its first run — `set -e` did its job and nothing was committed.
Three reds, all `test_every_sticker_file_is_named_for_a_real_sku[RT80|TR80|STW10]`: the three SKUs
§31 paused pending lab confirmation, whose artwork §31 deliberately KEEPS.

`RETIRED_LABEL_SKUS` was wired into `catalog.labels_orphaned()` and into the strength test, but
`tests/test_labels.py` re-implements the same orphan check a second time, parametrized over files
instead of SKUs, and that copy never consulted the exemption. So the test demanded we delete exactly
the files the §31 decision says to keep. Fixed by honouring `RETIRED_LABEL_SKUS` there too — the
same skip the strength test already had.

Worth noticing that this is the second time a duplicated check has drifted from its source (§29/§30b
was prices; this is artwork). The lesson §31 applied to prices — make the second copy LOOK UP the
first rather than restate it — has not been applied to the label checks. Left alone for now because
the assertion is cheap and correct, but if a third exemption ever appears, `labels_orphaned()`
should become the only implementation.

**Not verified by me:** the WhatsApp round trip. Send yourself "prices" and confirm you get the China
sheet with the new numbers, and that asking for US stock switches it — that is the one thing on this
change no test covers.

### Still open

Unchanged from §31's list: `LIR30`'s 20mg/30mg conflict, `SLU-PP-332` vs `-322`, artwork for `SM60`,
`SM100`, `TR120`, whether `RT80`/`TR80`/sterile water come back, and Daniel's monthly-commitment
volume discounts (deliberately not implemented).

