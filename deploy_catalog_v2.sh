#!/usr/bin/env bash
# Northline — deploy HANDOFF §30 (consolidated catalog + weight-aware shipping).
#
# Run this from Claude Code on the Mac. Cowork cannot: git, pytest against the
# real tree and the Railway CLI all need to execute natively (CLAUDE.md).
#
#   bash ~/peptide-agents/deploy_catalog_v2.sh
#
# It stops at the first failure and never deploys a tree whose tests are red.
set -euo pipefail
cd "$(dirname "$0")"

banner() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

banner "1/5  Tests"
if ! python3 -c "import pytest" 2>/dev/null; then
  echo "pytest missing — installing to user site (stays out of the Railway image)"
  python3 -m pip install --user --quiet pytest
fi
python3 -m pytest tests/ -q
echo "Expect 402 passed. Anything red: STOP and read the failure — a red price"
echo "baseline means a customer-visible number moved."

banner "2/5  What actually changed for a customer"
python3 - <<'PY'
from core import catalog
print(catalog.catalog_summary())
print()
bac = catalog.get("BAC10"); stw = catalog.get("STW10")
print(f"  BAC10 Bacteriostatic Water  ${bac.list_price:>6.2f}   {bac.unit_weight_g:.0f} g/kit"
      f"   cap-exempt={catalog.is_unrestricted('BAC10')}")
print(f"  STW10 Sterile Water         ${stw.list_price:>6.2f}   {stw.unit_weight_g:.0f} g/kit"
      f"   cap-exempt={catalog.is_unrestricted('STW10')}")
print("\n  Both were $12 before this change. Everything else is untouched —")
print("  tests/test_price_baseline.py pins the other 153 prices.")
if stw.list_price != bac.list_price:
    print("\n  ⚠️  The two waters disagree. They are the same 270 g at the same cost")
    print("      on adjacent rows of the sheet — a gap just moves bulk buyers one")
    print("      row down. Fix BEFORE this deploy. See HANDOFF §30.")
PY
read -r -p $'\nPrices above look right? [y/N] ' ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Stopped. Nothing was changed."; exit 1; }

banner "3/5  Regenerate the customer price list"
# Without this the image/XLSX/PDF the customer is SENT still shows the old price.
python3 -c "from core.price_image import generate_price_list_image as g; print(g('en')); print(g('cn'))"
echo "Open both and check bac water reads \$17 before continuing."

banner "4/5  Commit and push"
git add -A
git status --short
read -r -p $'\nCommit these? [y/N] ' ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Stopped before committing."; exit 1; }
git commit -m "Consolidate catalog into one SKU-keyed source of truth; weight-aware shipping

core/catalog.py joins pricing.CATALOG and price_image.CATEGORIES at import and
asserts the join is total, so catalog drift (the HANDOFF §29 root cause) is now a
test failure rather than an unpriceable order line. Adds the first weights the
system has ever had, and core/shipping.py turns them into balanced package splits
under the 2 kg gross cap.

Bacteriostatic and sterile water \$12 -> \$17: the freight is priced into the
product rather than denied by a weight rule, so the customer's flat quote is
unchanged. Both are also exempt from the 2 kg cap (that cap is about seizure
risk, which a box of water does not carry), so 84 kits ship as one package
instead of fourteen. Lipo-C and MIC are liquid too and stay capped.

Also: colloquial names (MT2, Wolverine, Glow, Bac Water) are in the alias table,
which closes the website/coa.html third-copy drift; EPO's spec gains the x10
suffix every other row has; and tests/test_price_baseline.py pins all 155 prices
to a hardcoded snapshot -- the old suite compared the price sheet against itself
and so could not have caught a price being edited.

See HANDOFF §30."
git push origin main

banner "5/5  Deploy by SHA, then VERIFY THE RUNNING COMMIT"
SHA=$(git rev-parse HEAD)
echo "HEAD is $SHA"
echo
echo "Railway auto-deploy is unreliable (§10) — force-deploy by SHA, then confirm"
echo "the RUNNING commit matches, not just that the deploy reported SUCCESS:"
echo
echo "    railway redeploy --service peptide-agents"
echo "    curl -s https://peptide-agents-production.up.railway.app/health"
echo
echo "Still open after this (HANDOFF §30): the WhatsApp smoke test through a"
echo "non-operator handset, Daniel's Sermorelin cost check, and the GitHub token"
echo "still embedded in the origin remote URL."
