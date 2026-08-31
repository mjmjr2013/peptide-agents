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

**Keep it current without being asked.** A SessionStart hook (`.claude/handoff-drift.sh`) compares
HEAD against the last commit that touched `HANDOFF.md`. If it told you at startup that commits have
landed since, updating the handoff is part of this session's work — record WHY, not just what. It
goes quiet once the handoff is current.

## Which tool to use on this machine (read before planning any shell work)

**Jordan's MacBook Pro is Intel (x64).** Cowork's local Linux sandbox requires Apple Silicon (M1 or
later), so in a **Cowork** session `device_bash` ALWAYS fails with *"Workspace unavailable — the
isolated Linux environment on this device failed to start."* This is a hardware requirement, not a
transient fault. Restarting the app does not help. Do not retry it, and do not ask Jordan to restart.

What still works in Cowork: reading and listing his files, staging them into the cloud container,
running code THERE (python3 is available; there is no network to github.com, railway.app or PyPI),
and writing files back with `device_commit_files`. That is enough to read this repo, write and test a
fix, and land the files on his Mac — a full catalog fix was done that way on 2026-08-30 (§29).

What CANNOT be done from Cowork: anything that must execute on his Mac — `git` (commit, push),
`pytest` against the real tree, the Railway deploy, the Railway CLI in `~/.railway`. Computer use
cannot substitute: terminals resolve **click-tier only**, so an agent can see a terminal but cannot
type into one.

**So: build in Cowork, deploy in Claude Code.** Claude Code runs its shell natively on the Mac (no VM,
so Intel is fine) and is the tool for git, tests and deploys. Jordan is not a coder — do not hand him
a list of commands and call it done. Either do it in Claude Code, or leave a single runnable script
and tell him the one line to paste.

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

**Prices are pinned.** `tests/test_price_baseline.py` holds a hardcoded snapshot of all 155 list
prices plus a log of every deliberate move. Changing a price means editing the price sheet, the
baseline and `INTENTIONAL_CHANGES` in one commit. Never regenerate that baseline to turn a red test
green — it exists precisely because the older suite compared the price sheet against itself and so
could not catch an edit (HANDOFF §30). Changing a price also means regenerating the price-list
image/XLSX/PDF, or the customer is still sent the old number.

## Secrets

No tokens live in this repo. `.env` is gitignored. Railway tokens are generated per session and
**deleted afterward** (HANDOFF §12). Never commit a token, and never echo one into terminal output.

## Layout

- `agents/` — messaging (the sales agent), payment watcher, weekly/daily reports, health monitor,
  transcript reviewer
- `core/` — Airtable client, crypto verification, pricing, deals, white-label, factory
  - `catalog.py` — **the SKU-keyed view of every product** (cost, price, form, weight, label). It
    JOINS `pricing.CATALOG` and `price_image.CATEGORIES` rather than copying them; `audit()` fails
    the build if they drift. Start here for any product fact.
  - `shipping.py` — kit weights → package splits (2 kg gross cap, balanced) and the customer's
    shipping charge. Bac water is exempt from the cap; see HANDOFF §30.
- `config/settings.py` — every env var, each with a comment explaining why it exists
- `website/` — northlinesupplies.com source (⚠️ hosted on a SECOND Cloudflare account)
- `main.py` — Flask app, routes, and the in-process scheduler loop
