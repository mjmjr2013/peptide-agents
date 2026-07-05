# Northline Group — Agent System Handoff

Paste this into a fresh Claude Code session (run from `~/peptide-agents`) to continue.
It describes the live WhatsApp sales agent, the new order/payment/fulfillment system,
how to deploy/debug, and what's outstanding. No secret tokens are stored here.

Last updated after the **live BTC end-to-end test SUCCEEDED** (Daniel bought bac water, paid real
BTC, verified on-chain, address collected, warehouse pinged — order NL-20260704-0F9D). Deployed at
commit `013113db` (daily warehouse manifest now EMAILED to `WAREHOUSE_EMAIL`, §7), `/health` ok. Two prod bugs found & fixed during the test: the "ghosting" after
payment (§9) and in-memory payment state stranded by redeploys (§4a). Remaining: §16 vial-photo
stage, §14 cleanups. See §4a (payment recovery), §9 (persona), §13/§14/§16/§17/§18 (tracking page).

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
- Matching is by **unique amount** (USDT exact; BTC quoted amount + ~1.5% tolerance). All tested against live chains.

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
  comma-separated `REPORT_EMAIL` (jordan@northlinesupplies.com + danielmcwilliams62881@gmail.com).
  SendGrid was **retired** (the merged Twilio Email console gated everything behind domain auth).
- Scheduler runs in-process in the webhook app (`run_report_scheduler`, checks every 5 min, guarded once/day & once/week).
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
- `REPORT_EMAIL` = jordan@northlinesupplies.com,danielmcwilliams62881@gmail.com (weekly report recipients).
- `WAREHOUSE_WHATSAPP` = `whatsapp:+8613418806654`, `DAILY_MANIFEST_HOUR` = 0 (midnight Mountain).
- `WAREHOUSE_EMAIL` = `ybgjwl888@outlook.com` — the daily manifest is emailed here (WhatsApp is only
  a fallback if this is ever unset). Live-tested 2026-07-05: real manifest email delivered via Gmail SMTP.
- `MANIFEST_TOKEN` set (guards the warehouse tracking page, §18).
- `OPERATOR_NUMBERS` (optional) — still unset; large-order alerts only log until set.

**Remaining:**
- ~~Run the live end-to-end test~~ ✅ DONE 2026-07-03 with real BTC (see §13). USDT path is code-identical
  (verified against live Etherscan earlier) but has not had a real-money run yet — optional.
- Warehouse rep to enter the tracking number for `NL-20260704-0F9D` on the §18 page (customer then gets
  the tracking text automatically) — watch this complete the first full fulfillment loop.

**Other standing items:**
- Twilio HK **regulatory bundle** = `pending-review` (WhatsApp unaffected; SMS/voice gated until approved).
- Cleanups: delete the Railway token used this session; rotate the GitHub PAT; delete the Cloudflare API token.

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
- Twilio inbound media URLs (`MediaUrl0`, on api.twilio.com) require account auth → they are NOT directly
  re-sendable as a `media_url` to a customer. To relay an image, re-host it at a public URL first
  (e.g. upload to an Airtable attachment field, which returns a public URL Twilio can fetch).
- iPhone videos are HEVC `.mov`; WhatsApp/Twilio need **H.264 `.mp4`**. This Mac has no ffmpeg/brew, but
  macOS ships **`/usr/bin/avconvert`** — transcode with
  `avconvert -s in.mov -p Preset960x540 -o out.mp4 --replace` (Preset640x480/960x540/1280x720/1920x1080 = H.264).
  Terminal can't read `~/Library/Messages/Attachments` (no Full Disk Access) → get files out of Messages by
  right-click → Copy, then paste (⌘V) into a Finder folder (the repo). Check codec/size with `mdls`/`ls`.

## 16. PENDING FEATURE — shipping notifications to customers (IN DESIGN, not built)
Goal: the agent sends each customer fulfillment updates over WhatsApp, in two stages at two different times:
  1. **Immediately** (order placed → warehouse makes label): send the customer their **tracking number**
     (and possibly the shipping-label image "for legitimacy" — Jordan to confirm tracking-only vs +label).
  2. **~1–2 weeks later** (weekly bulk arrives → warehouse divvies vials per order → photographs them):
     send the customer the **vial photo** before it ships.
Both are per-order, tied to a specific customer, and sent BY the agent.

Open design question (Jordan is confirming the workflow with the warehouse contact before we build):
**how does the warehouse hand each label/tracking/photo back to the system, and how do we match it to the
right customer/order?** Leading proposal (recommended, not yet approved): warehouse **replies on WhatsApp**
to the agent, referencing the **order ref** from the daily manifest — e.g. `TRACK <order_ref> <tracking#>`
for stage 1, and a photo captioned `VIALS <order_ref>` for stage 2. The agent recognizes the warehouse
sender (treat `WAREHOUSE_WHATSAPP` like an operator/special sender, see `_is_operator` pattern), matches
the order, and forwards to that order's customer. Alternatives floated: warehouse enters tracking + uploads
image in Airtable (needs Airtable access; a scheduler then sends), or Jordan/Daniel relay manually.

Implementation notes for whoever builds it:
- Likely new Orders fields: `tracking_number` (text), `label_image`/`vial_photo` (attachment), and
  per-stage sent flags (e.g. `tracking_sent`, `vial_photo_sent`) so each notice fires once.
- Relaying the warehouse's inbound **photo** to the customer needs re-hosting (see §15 gotcha) — simplest
  is to save it to the order's Airtable attachment field, then send that public URL as the Twilio `media_url`.
  Bonus: gives a permanent label/vial-photo record in Airtable.
- Recognize the warehouse number as a special inbound sender (do NOT treat its messages as a prospect).

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

## 18. Warehouse tracking page (LIVE) — daily "sheet" for the rep
Replaces the old daily text manifest. The rep (phone OR computer) gets a plain WhatsApp with a link and
enters tracking numbers on a simple web page; only the tracking field is editable and it flows straight
back into Airtable + texts the customer.
- **Flask routes (`main.py`):** `GET /manifest?token=…` renders a mobile/desktop-friendly page — one card
  per paid order still needing tracking (`get_orders_needing_tracking`), all fields **read-only** except a
  per-order tracking input. `POST /manifest/save` writes `tracking_number` + `tracking_sent` + sets
  `fulfillment_status="shipped"` (an EXISTING single-select option — do NOT invent one, Airtable rejects
  the whole update otherwise), then texts the customer via `send_tracking_to_customer()` in Lily's voice.
  Both routes require `?token=` == `MANIFEST_TOKEN` (403 otherwise). Page is centered, max-width 640px.
- **Airtable:** added `tracking_sent` (checkbox); `tracking_number` already existed. Helpers in
  `core/airtable_client.py`: `get_orders_needing_tracking`, `set_order_tracking`, `get_lead_phone_for_order`.
- **Customer phone** to text = the linked Lead's `phone` (kept with the `whatsapp:` prefix).
- **Auto-text on save** was Jordan's choice (vs. record-only). This IS the tracking-number stage of §16;
  the vial-photo stage is still in design.
- Daily ping fires from `run_daily_manifest()` (§7). Link:
  `https://peptide-agents-production.up.railway.app/manifest?token=<MANIFEST_TOKEN>`.
