#!/usr/bin/env bash
# One-paste sanity check for the nightly warehouse payout (HANDOFF §32).
# Reads only — it claims nothing and sends nothing, whatever PAYOUT_DRY_RUN says.
set -uo pipefail
cd "$(dirname "$0")"

echo "── dependency ──────────────────────────────────────────────"
python3 -c "import tronpy" 2>/dev/null || {
  echo "tronpy is missing; installing it"
  python3 -m pip install -q 'tronpy>=0.6.2' || pip3 install -q 'tronpy>=0.6.2'
}

echo
echo "── payout tests ────────────────────────────────────────────"
python3 -m pytest tests/test_warehouse_fees.py tests/test_warehouse_payout.py -q || {
  echo; echo "❌ Payout tests failed. Do NOT turn PAYOUT_DRY_RUN off."; exit 1; }

echo
echo "── what tonight would pay ──────────────────────────────────"
python3 -m agents.warehouse_payout preview
