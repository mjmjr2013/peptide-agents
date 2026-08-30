# Northline Group — Agent System Handoff

Paste this into a fresh Claude Code session (run from `~/peptide-agents`) to continue.
It describes the live WhatsApp sales agent, the new order/payment/fulfillment system,
how to deploy/debug, and what's outstanding. No secret tokens are stored here.

**Last updated 2026-08-30. Read §29 FIRST — it is the newest.** §29 fixes a silent revenue leak of a
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
