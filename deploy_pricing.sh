#!/usr/bin/env bash
# Deploy the 2026-09-03 fixed-pricing change (HANDOFF §31).
#
#   bash ~/peptide-agents/deploy_pricing.sh
#
# Everything up to and including `git push`. The Railway force-deploy is the one
# step this cannot do — it needs a token generated fresh per session and deleted
# afterward (§12) — so it stops and prints exactly what is left.
#
# Ordering is deliberate and `set -e` enforces it: the sheets are rebuilt BEFORE
# the tests run, and nothing is committed unless the whole suite is green. The
# §30a near miss was a rebuild that went to iCloud while the tracked files in
# static/ kept the old prices; the §30b tests are what catch that, so they have
# to run against the freshly built files, not before them.
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

bold "1/5  Where we are"
git status --short || true
echo "HEAD: $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"

bold "2/5  Rebuilding the served price sheets"
# RAILWAY_ENVIRONMENT=1 forces the write to static/ rather than Jordan's iCloud
# copies. Without it production keeps serving whatever is committed (§30a).
# This also builds static/price_list_us.xlsx, new in §31.
RAILWAY_ENVIRONMENT=1 python3 -c "from core.price_image import regenerate_all as r; import json; print(json.dumps(r(), indent=2))"

bold "3/5  Full test suite"
python3 -m pytest tests/ -q

bold "4/5  What is about to be committed"
git add -A
git status --short
echo
git diff --cached --stat | tail -20

bold "5/5  Commit and push"
git commit -q -m "Fixed pricing by warehouse and order size; negotiation removed

Prices now come off Daniel's published sheets, chosen by warehouse (US/China)
and by the TOTAL kits on the order (China: 1-24 / 25-99 / 100+). No discount
authority, no floor, no cap, no large-order escalation.

- core/price_sheets.py is GENERATED from the four workbooks by
  tools/build_price_sheets.py; price_image.CATEGORIES keeps layout only and
  looks its numbers up, so the sheet and the quote cannot drift.
- _validate_line_items sums kits first and prices second: the tier belongs to
  the order, not the line.
- Duplicate TB-500 / Sermorelin / BB10 rows resolved to the lower price,
  Dermorphin kept out, Etelcalcetide dropped (Jordan, 2026-09-03).
- RT80, TR80 and sterile water paused pending lab confirmation; SM60, SM100
  and TR120 added. 147 prices moved.
- US warehouse: flat pricing, single vials, \$30 flat shipping, its own sheet.
- Old price baseline kept as BASELINE_2026_08_31 with every move enumerated.

See HANDOFF §31.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BSjwprte6zSyq1asoh6Mzj"

git push origin main
echo
printf '\033[1mPushed %s\033[0m\n' "$(git rev-parse --short HEAD)"

cat <<'REMAINING'

────────────────────────────────────────────────────────────────────────────
STILL TO DO — neither of these can be scripted from here.

1. FORCE-DEPLOY ON RAILWAY (auto-deploy is flaky and has left commits
   undeployed for days — HANDOFF §10). Generate a Railway token, then
   serviceInstanceDeploy with the SHA printed above, poll until SUCCESS with a
   matching meta.commitHash, and check /health. Delete the token after (§12).

       project  c3856be2-a3fa-4184-a096-7f8f36f6e762
       service  4336f9e6-3908-48b5-aa67-4daaf7611c8b
       env      6ef277aa-0bc4-4a79-87c0-34d1af9f0c5c

2. AIRTABLE: add a single-line-text field named `warehouse` to the Orders
   table. Optional — orders create fine without it — but until it exists no
   order records which warehouse it shipped from.

Then send yourself a "prices" message on WhatsApp and check you get the China
sheet with the new numbers, and that asking for US stock switches it.
────────────────────────────────────────────────────────────────────────────
REMAINING
