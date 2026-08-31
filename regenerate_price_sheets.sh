#!/usr/bin/env bash
# Regenerate the price sheets customers are actually served, and prove they are
# current. Run this from Claude Code on Jordan's Mac WHENEVER A PRICE CHANGES.
#
#   bash ~/peptide-agents/regenerate_price_sheets.sh
#
# It replaces deploy_catalog_v2.sh, whose step 3 had the bug HANDOFF §30a
# records: it ran generate_price_list_image() with no RAILWAY_ENVIRONMENT, so
# the rebuild went to Jordan's iCloud folder while the tracked files in static/
# — the ones production serves from /price-list.xlsx, /price-list.xls,
# /price-list.pdf and /北线集团研究肽价格表.xlsx — stayed on the old prices.
# main.py only rebuilds those `if not exists()`, and on Railway they exist, so
# the stale sheet would have been served indefinitely.
#
# Two rules this encodes so nobody has to remember them:
#   RAILWAY_ENVIRONMENT=1  → write to static/, not to iCloud.
#   regenerate_all()       → every format at once, so they cannot drift apart.
#
# It must run on the Mac: the sheets are bilingual and building them anywhere
# without a Chinese font renders every CJK glyph as a hollow box. The generator
# now refuses rather than doing that quietly, so this will stop rather than
# produce a broken sheet.
set -euo pipefail
cd "$(dirname "$0")"

printf '\n\033[1m── Rebuilding static/ price sheets\033[0m\n'
RAILWAY_ENVIRONMENT=1 python3 -c "from core.price_image import regenerate_all as r; r()"

printf '\n\033[1m── Proving they match the catalog\033[0m\n'
python3 -m pytest tests/test_served_price_sheets.py tests/test_price_baseline.py -q

printf '\n\033[1m── What changed\033[0m\n'
git status --short static/
echo
echo "The PNGs are gitignored and rebuild themselves on deploy; the .xlsx, .xls"
echo "and .pdf are tracked and are what a customer downloads. Commit static/ or"
echo "production keeps serving the old numbers."
echo
echo "Note: your own iCloud reference copies are NOT updated by this script — it"
echo "deliberately writes to static/. Regenerate those separately if you want"
echo "them current (needs a terminal with Full Disk Access; see HANDOFF §30a)."
