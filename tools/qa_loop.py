"""The QA fixer loop's plumbing (HANDOFF §34).

The transcript reviewer (Railway) queues flagged conversations in Airtable
`QA Issues`. A scheduled Claude Code session on Jordan's Mac works them: it
reads the row, diagnoses against the code, fixes on a branch in an ISOLATED git
worktree, adds a test, and hands back. Everything mechanical lives here so the
session only supplies judgment, and so every step is testable:

    python3 tools/qa_loop.py list                   open work, one line each
    python3 tools/qa_loop.py show QA-12             the whole row, transcript included
    python3 tools/qa_loop.py start QA-12            claim it: worktree + branch, prints the path
    python3 tools/qa_loop.py finish QA-12 --notes-file N [--title T]
                                                    tests → push → PR → pr_open (self-approves
                                                    when only the reviewer changed)
    python3 tools/qa_loop.py resolve QA-12 --status false_positive|needs_jordan|wont_fix --notes-file N
    python3 tools/qa_loop.py deploy-check           merge approved PRs, test main, deploy by SHA,
                                                    confirm, mark fixed, clean up worktrees

Design rules, each learned the hard way elsewhere in this repo:
- Never touch the main checkout at ~/peptide-agents. Other sessions leave
  uncommitted work there; every fix is built in ~/peptide-agents-qa/qa-<n>.
- Nothing ships without the full suite green, twice: on the branch at `finish`
  and on `main` after the merge, right before the deploy.
- A deploy is only "done" when Railway reports the running commit (§10).
- Customer-facing changes wait for Jordan's `approve` tick (or a manual merge).
  Only a diff confined to SELF_APPROVE_FILES — the reviewer and its tests —
  approves itself: that is the judge improving, which no customer sees.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

QA_ROOT = Path(os.environ.get("QA_WORKTREE_ROOT", str(Path.home() / "peptide-agents-qa")))
MAIN_WT = QA_ROOT / "_main"
SELF_APPROVE_FILES = {
    "agents/transcript_reviewer.py",
    "tests/test_transcript_reviewer.py",
}
TERMINAL = ("fixed", "false_positive", "wont_fix")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(args: list[str], cwd: Path | None = None, check: bool = True,
         capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd or REPO), check=check,
                          capture_output=capture, text=True)


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return _run(["git", *args], cwd=cwd, check=check).stdout.strip()


# ── Airtable ─────────────────────────────────────────────────────────────────

def _at():
    from core.airtable_client import airtable
    return airtable


def _find(ref: str) -> dict:
    """QA-12 / 12 / recXXXX → the record."""
    ref = ref.strip()
    if ref.startswith("rec"):
        return _at().qa_issues.get(ref)
    n = int(re.sub(r"\D", "", ref))
    rows = _at().qa_issues.all(formula=f"{{qa_id}}={n}")
    if not rows:
        sys.exit(f"*** no QA row with qa_id {n}")
    return rows[0]


def _label(rec: dict) -> str:
    return f"QA-{rec['fields'].get('qa_id', '?')}"


# ── Git worktrees ─────────────────────────────────────────────────────────────

def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n].rstrip("-") or "fix"


def branch_name(rec: dict) -> str:
    f = rec["fields"]
    return f.get("branch") or f"qa/{f.get('qa_id', 'x')}-{slug(f.get('summary', ''))}"


def worktree_dir(rec: dict) -> Path:
    return QA_ROOT / f"qa-{rec['fields'].get('qa_id', 'x')}"


def _copy_env(dst: Path) -> None:
    src = REPO / ".env"
    if src.exists():
        shutil.copy2(src, dst / ".env")


def _branch_exists(branch: str) -> bool:
    return _run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                check=False).returncode == 0


def ensure_worktree(rec: dict) -> tuple[Path, str]:
    """A worktree for this row on its own branch off origin/main. Reused if it
    already exists (a session can be interrupted and resume)."""
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    _git("fetch", "-q", "origin", "main")
    br = branch_name(rec)
    wt = worktree_dir(rec)
    if not wt.exists():
        if _branch_exists(br):
            _git("worktree", "add", str(wt), br)
        else:
            _git("worktree", "add", "-b", br, str(wt), "origin/main")
    _copy_env(wt)
    return wt, br


def remove_worktree(rec: dict, delete_remote: bool = False) -> None:
    wt = worktree_dir(rec)
    br = branch_name(rec)
    if wt.exists():
        _run(["git", "worktree", "remove", "--force", str(wt)], check=False)
    _run(["git", "worktree", "prune"], check=False)
    if _branch_exists(br):
        _run(["git", "branch", "-D", br], check=False)
    if delete_remote:
        _run(["git", "push", "-q", "origin", "--delete", br], check=False)


def run_tests(cwd: Path) -> tuple[bool, str]:
    """Full suite, no cache dir left behind. (ok, last lines of output)."""
    p = _run([sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider",
              "-x", "--no-header"], cwd=cwd, check=False)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-15:])
    return p.returncode == 0, tail


def changed_files(cwd: Path) -> list[str]:
    out = _git("diff", "--name-only", "origin/main...HEAD", cwd=cwd)
    return [l for l in out.splitlines() if l.strip()]


def self_approvable(files: list[str]) -> bool:
    """Only the judge (and its tests / the handoff) changed → nothing a customer
    sees → the loop may approve its own PR."""
    return bool(files) and all(f in SELF_APPROVE_FILES for f in files)


# ── GitHub ────────────────────────────────────────────────────────────────────

def _remote() -> tuple[str, str, str]:
    """(owner, repo, token) from the origin URL. The PAT lives in the remote URL
    on this Mac (HANDOFF §12) — read it from there, store it nowhere else."""
    url = _git("remote", "get-url", "origin")
    m = re.match(r"https://(?:[^:/@]+:)?([^@/]+)?@?github\.com/([^/]+)/([^/.]+)(?:\.git)?$", url)
    if not m:
        sys.exit(f"*** cannot parse GitHub remote: {re.sub(r':[^@/]+@', ':***@', url)}")
    token = m.group(1) or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("*** no GitHub token in the remote URL or GITHUB_TOKEN")
    return m.group(2), m.group(3), token


def gh(method: str, path: str, body: dict | None = None) -> dict:
    owner, repo, token = _remote()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}{path}",
        data=json.dumps(body).encode() if body is not None else None, method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "northline-qa-loop"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.load(r) if r.status != 204 else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub {method} {path}: HTTP {e.code} "
                           f"{e.read()[:300].decode(errors='replace')}")


def pr_number(pr_url: str) -> int | None:
    m = re.search(r"/pull/(\d+)", pr_url or "")
    return int(m.group(1)) if m else None


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(_args) -> int:
    rows = _at().get_qa_issues(("open", "in_progress", "pr_open", "needs_jordan", "deploy_failed"))
    if not rows:
        print("no open QA work")
        return 0
    for r in rows:
        f = r["fields"]
        flags = []
        if f.get("approve"):
            flags.append("APPROVED")
        if (f.get("jordan_notes") or "").strip():
            flags.append("jordan answered")
        if int(f.get("runs") or 1) > 1:
            flags.append(f"seen {f.get('runs')}x")
        print(f"{_label(r):7s} {f.get('status','?'):13s} {f.get('severity','?'):6s} "
              f"{f.get('phone','?'):14s} {(' [' + ', '.join(flags) + ']') if flags else ''}\n"
              f"        {(f.get('summary') or '').strip()[:160]}")
    return 0


def cmd_show(args) -> int:
    r = _find(args.ref)
    f = r["fields"]
    order = ["qa_id", "status", "severity", "phone", "flagged_at", "last_seen_at", "runs",
             "summary", "suspected_cause", "issues", "jordan_notes", "fix_notes", "branch",
             "pr_url", "fix_commit", "approve", "transcript"]
    print(f"record: {r['id']}")
    for k in order:
        if k in f and f[k] not in ("", None):
            v = f[k]
            print(f"\n== {k} ==\n{v}" if isinstance(v, str) and "\n" in v else f"{k}: {v}")
    return 0


def cmd_start(args) -> int:
    r = _find(args.ref)
    f = r["fields"]
    if f.get("status") in TERMINAL:
        sys.exit(f"*** {_label(r)} is {f['status']} — nothing to start")
    wt, br = ensure_worktree(r)
    _at().update_qa_issue(r["id"], status="in_progress", branch=br)
    print(f"{_label(r)} claimed.\nworktree: {wt}\nbranch:   {br}\n"
          f"Work ONLY inside that directory. Commit there. Then:\n"
          f"  python3 tools/qa_loop.py finish {_label(r)} --notes-file <plain-English notes>")
    return 0


def cmd_finish(args) -> int:
    r = _find(args.ref)
    notes = Path(args.notes_file).read_text().strip()
    if len(notes) < 40:
        sys.exit("*** notes are too short — Jordan reads these; say what was wrong and what changed")
    wt, br = ensure_worktree(r)
    if _git("status", "--porcelain", cwd=wt):
        sys.exit(f"*** uncommitted changes in {wt} — commit them first")
    ahead = int(_git("rev-list", "--count", "origin/main..HEAD", cwd=wt) or 0)
    if ahead == 0:
        sys.exit("*** branch has no commits beyond origin/main — nothing to ship")
    files = changed_files(wt)
    forbidden = [p for p in files if p.startswith("core/price_sheets") or
                 p == "tests/test_price_baseline.py" or p.startswith("core/crypto_verify")]
    if forbidden:
        sys.exit(f"*** {forbidden} may not be changed by the QA loop (CLAUDE.md) — "
                 f"resolve as needs_jordan instead")
    print(f"[finish] running the suite in {wt} …")
    ok, tail = run_tests(wt)
    if not ok:
        sys.exit(f"*** tests FAILED on the branch — not pushing:\n{tail}")
    print(f"[finish] tests green: {tail.splitlines()[-1] if tail else ''}")
    _git("push", "-q", "-u", "origin", br, cwd=wt)
    title = args.title or _git("log", "-1", "--format=%s", cwd=wt)
    f = r["fields"]
    auto = self_approvable(files)
    body = (f"{_label(r)} · {f.get('phone', '')} · severity {f.get('severity', '?')}\n\n"
            f"**Reviewer alert:** {f.get('summary', '')}\n\n{notes}\n\n"
            f"Files: {', '.join(files)}\n\n"
            + ("Self-approved: only the QA reviewer changed (nothing customer-facing).\n\n"
               if auto else
               "Approve by ticking `approve` on the Airtable row or merging here; the fixer "
               "deploys it on its next run.\n\n")
            + "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    existing = pr_number(f.get("pr_url", ""))
    if existing:
        pr = gh("GET", f"/pulls/{existing}")
    else:
        pr = gh("POST", "/pulls", {"title": title, "head": br, "base": "main", "body": body})
    fields = {"status": "pr_open", "pr_url": pr["html_url"], "fix_notes": notes,
              "branch": br}
    if auto:
        fields["approve"] = True
    _at().update_qa_issue(r["id"], **fields)
    print(f"{_label(r)} → pr_open {pr['html_url']}" + ("  (self-approved)" if auto else ""))
    return 0


def cmd_resolve(args) -> int:
    r = _find(args.ref)
    notes = Path(args.notes_file).read_text().strip()
    if len(notes) < 40:
        sys.exit("*** notes are too short — Jordan reads these")
    fields = {"status": args.status, "fix_notes": notes}
    if args.status in TERMINAL:
        fields["resolved_at"] = _now()
    _at().update_qa_issue(r["id"], **fields)
    if args.status in TERMINAL:
        remove_worktree(r)
    print(f"{_label(r)} → {args.status}")
    return 0


def _ensure_main_worktree(sha: str) -> Path:
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    if MAIN_WT.exists():
        _git("checkout", "-q", "--detach", sha, cwd=MAIN_WT)
    else:
        _git("worktree", "add", "-q", "--detach", str(MAIN_WT), sha)
    _copy_env(MAIN_WT)
    return MAIN_WT


def cmd_deploy_check(_args) -> int:
    from tools import railway_deploy as rd
    _git("fetch", "-q", "origin", "main")
    main_sha = _git("rev-parse", "origin/main")
    rows = _at().get_qa_issues(("pr_open", "deploy_failed"))
    merged: list[dict] = []
    for r in rows:
        f = r["fields"]
        n = pr_number(f.get("pr_url", ""))
        if not n:
            continue
        pr = gh("GET", f"/pulls/{n}")
        if pr.get("state") == "closed" and not pr.get("merged"):
            # Jordan closed it on GitHub without merging — that is a "no".
            _at().update_qa_issue(r["id"], status="wont_fix", resolved_at=_now(),
                                  fix_notes=(f.get("fix_notes", "") +
                                             f"\n\n[{_now()}] PR #{n} was closed without merging."))
            remove_worktree(r, delete_remote=True)
            print(f"[deploy-check] {_label(r)} PR #{n} closed unmerged → wont_fix")
            continue
        if not pr.get("merged") and f.get("approve") and pr.get("state") == "open":
            try:
                gh("PUT", f"/pulls/{n}/merge",
                   {"merge_method": "squash", "commit_title": f"{pr['title']} (#{n})"})
                print(f"[deploy-check] merged {_label(r)} PR #{n}")
                pr = gh("GET", f"/pulls/{n}")
            except RuntimeError as e:
                print(f"[deploy-check] merge of {_label(r)} failed: {e}")
                _at().update_qa_issue(r["id"], status="deploy_failed",
                                      fix_notes=(f.get("fix_notes", "") +
                                                 f"\n\n[{_now()}] MERGE FAILED: {e}"))
                continue
        if pr.get("merged"):
            merged.append(r)
    if not merged:
        print(f"[deploy-check] nothing merged; origin/main {main_sha[:8]}, "
              f"running {rd.running_commit()[:8] or '?'}")
        return 0
    _git("fetch", "-q", "origin", "main")
    main_sha = _git("rev-parse", "origin/main")
    running = rd.running_commit()
    if running != main_sha:
        wt = _ensure_main_worktree(main_sha)
        print(f"[deploy-check] testing main {main_sha[:8]} …")
        ok, tail = run_tests(wt)
        if not ok:
            for r in merged:
                _at().update_qa_issue(r["id"], status="deploy_failed",
                                      fix_notes=(r["fields"].get("fix_notes", "") +
                                                 f"\n\n[{_now()}] DEPLOY BLOCKED — tests fail on "
                                                 f"main {main_sha[:8]}:\n{tail}"))
            sys.exit(f"*** tests FAILED on main {main_sha[:8]} — not deploying:\n{tail}")
        try:
            running = rd.deploy(main_sha)
        except rd.DeployError as e:
            for r in merged:
                _at().update_qa_issue(r["id"], status="deploy_failed",
                                      fix_notes=(r["fields"].get("fix_notes", "") +
                                                 f"\n\n[{_now()}] DEPLOY FAILED: {e}"))
            sys.exit(f"*** {e}")
    health = rd.health_ok()
    for r in merged:
        _at().update_qa_issue(r["id"], status="fixed", fix_commit=running,
                              resolved_at=_now(), approve=True)
        remove_worktree(r, delete_remote=True)
        print(f"[deploy-check] {_label(r)} fixed — live on {running[:8]}")
    print(f"[deploy-check] running {running[:8]}  health={'ok' if health else 'DOWN'}")
    # Keep Jordan's checkout current when that is free of risk (main, fast-forward only).
    if _git("rev-parse", "--abbrev-ref", "HEAD") == "main":
        p = _run(["git", "pull", "-q", "--ff-only"], check=False)
        print("[deploy-check] local main fast-forwarded" if p.returncode == 0
              else "[deploy-check] local main NOT updated (dirty or diverged) — left alone")
    return 0


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="qa_loop", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("ref"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("start"); p.add_argument("ref"); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("finish"); p.add_argument("ref")
    p.add_argument("--notes-file", required=True); p.add_argument("--title")
    p.set_defaults(fn=cmd_finish)
    p = sub.add_parser("resolve"); p.add_argument("ref")
    p.add_argument("--status", required=True, choices=["false_positive", "needs_jordan", "wont_fix"])
    p.add_argument("--notes-file", required=True); p.set_defaults(fn=cmd_resolve)
    sub.add_parser("deploy-check").set_defaults(fn=cmd_deploy_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
