#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deploy the 2026-08-30 catalog fix (HANDOFF §29).
#
# Written by Claude, which could NOT run it: device_bash was unavailable and the
# cloud container has no route to github.com or railway.app. Every step below is
# therefore un-executed — read it before you run it.
#
#   cd ~/peptide-agents
#   export RAILWAY_TOKEN='<fresh token>'      # optional; see step 6
#   bash deploy_catalog_fix.sh
#
# Does the whole thing: tests -> commit -> push -> force-deploy by SHA -> poll
# until Railway reports SUCCESS -> verify the RUNNING commit is the one you just
# pushed (HANDOFF §10 records commits sitting undeployed for days, so "deploy
# succeeded" is checked separately from "your code is live").
#
# Without RAILWAY_TOKEN it still tests, commits and pushes, then stops and tells
# you how to finish. The token is read from the environment and never written to
# disk. HANDOFF §12: generate fresh per session, delete after.
#
# Safe to re-run: if the commit is already made and pushed, git is a no-op and it
# proceeds to the deploy.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"
echo "repo: $(pwd)"

# ── 1. Preflight ───────────────────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] || { echo "ABORT: on '$BRANCH', expected main"; exit 1; }

FILES=(
  core/aliases.py
  core/pricing.py
  core/price_image.py
  agents/messaging_agent.py
  tests/test_catalog_regression.py
  HANDOFF.md
)
for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || { echo "ABORT: missing $f"; exit 1; }
done

# Anything else dirty? Claude only touched the six files above. If the tree has
# other changes they are yours, and they are NOT swept into this commit.
OTHER="$(git status --porcelain -- . ':(exclude)core/aliases.py' ':(exclude)core/pricing.py' \
  ':(exclude)core/price_image.py' ':(exclude)agents/messaging_agent.py' \
  ':(exclude)tests/test_catalog_regression.py' ':(exclude)HANDOFF.md' || true)"
if [[ -n "$OTHER" ]]; then
  echo
  echo "NOTE: other uncommitted changes are present and will NOT be committed:"
  echo "$OTHER"
  echo
  read -r -p "continue? [y/N] " ok; [[ "$ok" == "y" ]] || exit 1
fi

# ── 2. Tests must pass ─────────────────────────────────────────────────────
echo
echo "── running the catalog regression suite ─────────────────────────────"
if ! python3 -m pytest tests/test_catalog_regression.py -q; then
  echo
  echo "ABORT: tests failed. A failure here means a PRICE MOVED. Do not deploy."
  echo "Investigate before touching the baseline — see HANDOFF §29."
  exit 1
fi

# ── 3. Price sheet does NOT need regenerating ──────────────────────────────
# HANDOFF §10 step 1 says to regenerate static/ when prices change. This change
# is price-neutral by construction: all 155 sheet SKUs were priced before and
# after and diffed, 0 moved, and CATEGORIES (which drives the image/XLSX/PDF) was
# not edited. Skipping deliberately. If you want to confirm:
#   RAILWAY_ENVIRONMENT=1 python3 -c "from core.price_image import generate_price_list_xlsx; generate_price_list_xlsx()"
#   git diff --stat static/

# ── 4. Commit ──────────────────────────────────────────────────────────────
echo
echo "── staging ──────────────────────────────────────────────────────────"
git add -- "${FILES[@]}"
git status --short -- "${FILES[@]}"
echo
git commit -F - <<'MSG'
fix(pricing): fail closed on unpriceable lines; align catalog name drift

Five products were spelled differently in core/pricing.py and
core/price_image.py (GLOW70, KLOW, CD5, CP10, MIC10). Neither spelling
worked for both lookups: get_list_price() resolved only the cost-file
name, get_sku() only the price-sheet name.

An unresolvable line was not merely unpriced. The whole clamp in
_validate_line_items sits behind `if list_pk is not None`, so such a line
skipped the floor check, the discount cap, and the `if unit <= 0` backfill
together — a quote below cost passed unchanged, and a missing unit_price
became $0.00 and shipped the kits free. deals.py uses the price-sheet
spellings, so three DIEGO26 lines were in this state.

Fuzzing 930 plausible spellings drawn from our own live price sheet found
36 failures across 8 SKUs via two unrelated root causes, so this is a class
of bug rather than five instances.

- fail closed: _validate_line_items returns (items, clamped, unpriced) and
  excludes unpriceable lines; both call sites escalate via
  _enter_manual_mode(), the same path the large-order handoff uses
- core/aliases.py: one canon() both catalog modules normalize through.
  An alias layer, not a rename — renaming either side would change text a
  buyer sees. No displayed string changed.
- DSIP specs normalized to the standard "Nmg x10" form
- Sermorelin collapsed to one cost basis; the stale "Sermorelin Acetate"
  rows (2.5x, and below the 3x floor) removed, name still aliased
- tests/test_catalog_regression.py: 173 assertions pinning that no
  customer price moved

Provisional: Daniel is confirming the real Sermorelin cost with the lab.

See HANDOFF §29.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MycgMoLGeZtvNatDiuMnp9
MSG

SHA="$(git rev-parse HEAD)"
echo
echo "committed ${SHA:0:8}"

# ── 5. Push ────────────────────────────────────────────────────────────────
echo
echo "── pushing to origin/main ───────────────────────────────────────────"
git push origin main
echo

cat <<BANNER
=======================================================================
PUSHED ${SHA:0:8}
=======================================================================
BANNER

# ── 6. Railway ─────────────────────────────────────────────────────────────
# Auto-deploy is flaky (HANDOFF §10): it misses commits, or redeploys a stale
# one. So we force by SHA and then verify the RUNNING commit — "deploy
# succeeded" is not the same claim as "your code is live".
#
# The token is read from the environment and never written anywhere.
# HANDOFF §12: generate fresh per session, delete after.

if [[ -z "${RAILWAY_TOKEN:-}" ]]; then
  cat <<EOF

RAILWAY_TOKEN is not set, so I stopped before deploying.

  1. railway.app -> Account Settings -> Tokens -> create one
  2. export RAILWAY_TOKEN='<paste>'
  3. bash deploy_catalog_fix.sh     (re-run; it will skip straight to deploy,
                                     the commit is already made and pushed)
  4. unset RAILWAY_TOKEN  and delete the token in Railway

Or deploy from the dashboard: open the project, confirm the newest deployment
shows commit ${SHA:0:8}, and if it does not, trigger a redeploy on that commit.
Project c3856be2-a3fa-4184-a096-7f8f36f6e762
EOF
  exit 0
fi

echo
echo "── forcing Railway deploy of ${SHA:0:8} ─────────────────────────────"
SHA="$SHA" python3 - <<'PY'
import json, os, sys, time, urllib.request, urllib.error

TOKEN   = os.environ["RAILWAY_TOKEN"]
SHA     = os.environ["SHA"]
PROJECT = "c3856be2-a3fa-4184-a096-7f8f36f6e762"
SERVICE = "4336f9e6-3908-48b5-aa67-4daaf7611c8b"
ENVIRON = "6ef277aa-0bc4-4a79-87c0-34d1af9f0c5c"
URL     = "https://backboard.railway.app/graphql/v2"

def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        # Railway's GraphQL sits behind Cloudflare; a default urllib User-Agent
        # gets 403 error 1010. HANDOFF §15 — this header is load-bearing.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"  HTTP {e.code} from Railway: {e.read()[:400].decode(errors='replace')}")
    except Exception as e:
        sys.exit(f"  could not reach Railway: {e!r}")
    if payload.get("errors"):
        sys.exit("  GraphQL error: " + json.dumps(payload["errors"])[:500])
    return payload["data"]

# Trigger
gql("""mutation($s:String!,$e:String!,$c:String!){
         serviceInstanceDeploy(serviceId:$s, environmentId:$e, commitSha:$c) }""",
    {"s": SERVICE, "e": ENVIRON, "c": SHA})
print(f"  deploy triggered for {SHA[:8]}")

# Poll
Q = """query($in:DeploymentListInput!){
         deployments(first:1, input:$in){ edges{ node{ id status meta } } } }"""
VARS = {"in": {"projectId": PROJECT, "serviceId": SERVICE, "environmentId": ENVIRON}}

deadline = time.time() + 600
last = None
while time.time() < deadline:
    edges = gql(Q, VARS)["deployments"]["edges"]
    if edges:
        node   = edges[0]["node"]
        status = node.get("status")
        meta   = node.get("meta") or {}
        landed = meta.get("commitHash") or meta.get("commitSha") or ""
        if status != last:
            print(f"  status: {status}   commit: {landed[:8] or '?'}")
            last = status
        if status == "SUCCESS":
            if landed.startswith(SHA[:8]):
                print(f"\n  LIVE: {SHA[:8]} is the running commit.")
                sys.exit(0)
            sys.exit(f"\n  *** Deploy SUCCEEDED but the running commit is "
                     f"{landed[:8]}, NOT {SHA[:8]}. This is exactly the stale-deploy\n"
                     f"      failure HANDOFF §10 warns about. Redeploy from the dashboard.")
        if status in ("FAILED", "CRASHED", "REMOVED"):
            sys.exit(f"\n  *** Deploy ended {status}. Check the Railway build logs.")
    time.sleep(10)
sys.exit("\n  *** Timed out after 10 min. Check the Railway dashboard.")
PY

echo
echo "── health check ─────────────────────────────────────────────────────"
curl -sS -o /dev/null -w "  /health -> %{http_code}\n" \
  https://peptide-agents-production.up.railway.app/health || true

cat <<EOF

=======================================================================
DONE — ${SHA:0:8} deployed.

  unset RAILWAY_TOKEN     # and delete the token in the Railway dashboard

SMOKE TEST — this is the actual bug, end to end. From a NON-operator number,
message Lily:  "how much for 5 kits of Klow 80"

  Before this fix that could be quoted at \$0.
  It must now quote \$220/kit, or hand off to a human.
  If it comes back free, the deploy did not land — do not trust it.
=======================================================================
EOF
