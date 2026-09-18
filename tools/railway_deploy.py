"""Railway deploy-by-SHA, as a module (HANDOFF §10, §34).

Auto-deploy is unreliable, so every deploy here is forced by commit SHA and then
CONFIRMED by reading back the running commit. Extracted from
deploy_catalog_fix.sh so the QA fixer loop (tools/qa_loop.py) can call it, and
so a session can run it by hand:

    python3 tools/railway_deploy.py status          # running commit + status
    python3 tools/railway_deploy.py deploy <sha>    # force + wait + confirm

RAILWAY_TOKEN comes from the environment or .env (persistent, account-scoped —
CLAUDE.md). The browser User-Agent is load-bearing: Railway's GraphQL sits
behind Cloudflare and rejects urllib's default UA with a 403.
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "c3856be2-a3fa-4184-a096-7f8f36f6e762"
SERVICE = "4336f9e6-3908-48b5-aa67-4daaf7611c8b"
ENVIRON = "6ef277aa-0bc4-4a79-87c0-34d1af9f0c5c"
URL = "https://backboard.railway.app/graphql/v2"
HEALTH = "https://peptide-agents-production.up.railway.app/health"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class DeployError(RuntimeError):
    pass


def _token() -> str:
    tok = os.environ.get("RAILWAY_TOKEN", "")
    if not tok:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("RAILWAY_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip("'\"")
    if not tok:
        raise DeployError("RAILWAY_TOKEN not set (env or .env)")
    return tok


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json",
        "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise DeployError(f"HTTP {e.code} from Railway: {e.read()[:300].decode(errors='replace')}")
    if payload.get("errors"):
        raise DeployError("GraphQL error: " + json.dumps(payload["errors"])[:500])
    return payload["data"]


def latest_deployment() -> dict:
    """{'id','status','commit','created'} of the newest deployment."""
    q = """query($in:DeploymentListInput!){
             deployments(first:1, input:$in){ edges{ node{ id status meta createdAt } } } }"""
    edges = gql(q, {"in": {"projectId": PROJECT, "serviceId": SERVICE,
                           "environmentId": ENVIRON}})["deployments"]["edges"]
    if not edges:
        return {"id": "", "status": "NONE", "commit": "", "created": ""}
    n = edges[0]["node"]
    meta = n.get("meta") or {}
    return {"id": n["id"], "status": n.get("status", ""),
            "commit": meta.get("commitHash") or meta.get("commitSha") or "",
            "created": n.get("createdAt", "")}


def running_commit() -> str:
    """Full SHA of the deployment that is live right now ('' if none SUCCESS)."""
    d = latest_deployment()
    return d["commit"] if d["status"] == "SUCCESS" else ""


def deploy(sha: str, timeout_s: int = 600, log=print) -> str:
    """Force-deploy `sha`, wait for SUCCESS, and confirm the running commit
    matches. Returns the running commit. Raises DeployError otherwise — never
    reports a deploy as done on the strength of the dashboard alone."""
    gql("""mutation($s:String!,$e:String!,$c:String!){
             serviceInstanceDeploy(serviceId:$s, environmentId:$e, commitSha:$c) }""",
        {"s": SERVICE, "e": ENVIRON, "c": sha})
    log(f"[deploy] triggered {sha[:8]}")
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        d = latest_deployment()
        if d["status"] != last:
            log(f"[deploy] status {d['status']} commit {d['commit'][:8] or '?'}")
            last = d["status"]
        if d["status"] == "SUCCESS":
            if d["commit"].startswith(sha[:8]):
                return d["commit"]
            raise DeployError(f"deploy SUCCEEDED but running commit is {d['commit'][:8]}, "
                              f"not {sha[:8]} (the HANDOFF §10 stale-deploy failure)")
        if d["status"] in ("FAILED", "CRASHED", "REMOVED"):
            raise DeployError(f"deploy ended {d['status']} — check Railway build logs")
        time.sleep(10)
    raise DeployError("deploy timed out after 10 min")


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "status":
        d = latest_deployment()
        print(f"{d['status']} {d['commit'][:8]} {d['created']}  health={'ok' if health_ok() else 'DOWN'}")
        return 0
    if argv[0] == "deploy" and len(argv) == 2:
        live = deploy(argv[1])
        print(f"LIVE {live[:8]}  health={'ok' if health_ok() else 'DOWN'}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except DeployError as e:
        sys.exit(f"*** {e}")
