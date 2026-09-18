"""tools/qa_loop.py — the fixer loop's plumbing (HANDOFF §34).

The git parts run against a throwaway repo with its own bare "origin", so the
worktree / branch / self-approval logic is exercised for real without touching
~/peptide-agents. Airtable and GitHub are never called from here.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import qa_loop as ql  # noqa: E402
from core.airtable_client import AirtableClient  # noqa: E402


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_slug_and_branch_name():
    assert ql.slug("Lily quoted incorrect US shipping!!") == "lily-quoted-incorrect-us-shipping"
    assert ql.slug("") == "fix"
    rec = {"fields": {"qa_id": 12, "summary": "Coin switch: no BTC address sent"}}
    assert ql.branch_name(rec) == "qa/12-coin-switch-no-btc-address-sent"
    rec["fields"]["branch"] = "qa/12-kept"
    assert ql.branch_name(rec) == "qa/12-kept"


def test_pr_number():
    assert ql.pr_number("https://github.com/o/r/pull/41") == 41
    assert ql.pr_number("") is None


def test_self_approval_is_reviewer_only():
    assert ql.self_approvable(["agents/transcript_reviewer.py", "tests/test_transcript_reviewer.py"])
    assert not ql.self_approvable(["HANDOFF.md"])  # handoff is written by humans, not branches
    assert not ql.self_approvable([])
    assert not ql.self_approvable(["agents/transcript_reviewer.py", "agents/messaging_agent.py"])
    assert not ql.self_approvable(["tools/qa_loop.py"])


def test_remote_parsing_reads_token_from_url(monkeypatch):
    monkeypatch.setattr(ql, "_git", lambda *a, **k: "https://user:ghp_secret@github.com/own/repo.git")
    assert ql._remote() == ("own", "repo", "ghp_secret")
    monkeypatch.setattr(ql, "_git", lambda *a, **k: "https://github.com/own/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "envtok")
    assert ql._remote() == ("own", "repo", "envtok")


def test_qa_open_statuses_include_every_live_state():
    """A phone flagged again while its row is in any of these must UPDATE that
    row, not open a second one — and the fixer's list must show them all."""
    assert set(AirtableClient.QA_OPEN_STATUSES) == {"open", "in_progress", "pr_open",
                                                    "needs_jordan", "deploy_failed"}


# ── Queue dedupe (client) ────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, existing):
        self.existing = existing
        self.created = []
        self.updated = []

    def all(self, formula=""):
        return list(self.existing)

    def create(self, fields):
        self.created.append(fields)
        return {"id": "recNEW", "fields": {**fields, "qa_id": 9}}

    def update(self, rid, fields):
        self.updated.append((rid, fields))
        return {"id": rid, "fields": {**fields, "qa_id": 4}}


def _client(monkeypatch, table):
    monkeypatch.setattr(AirtableClient, "qa_issues", property(lambda self: table))
    return AirtableClient.__new__(AirtableClient)


def test_queue_creates_when_no_open_row(monkeypatch):
    t = _FakeTable([])
    c = _client(monkeypatch, t)
    rec, created = c.queue_qa_issue("+1", "high", "s", [{"severity": "high", "issue": "x"}], "T", "cause")
    assert created and rec["fields"]["qa_id"] == 9
    assert t.created[0]["status"] == "open" and t.created[0]["runs"] == 1
    assert t.created[0]["phone"] == "+1" and '"severity": "high"' in t.created[0]["issues"]
    assert t.created[0]["suspected_cause"] == "cause"


def test_queue_refreshes_open_row_and_counts_runs(monkeypatch):
    t = _FakeTable([{"id": "recOLD", "fields": {"qa_id": 4, "phone": "+1", "status": "open",
                                                 "runs": 2, "flagged_at": "2026-09-17T01:00:00"}}])
    c = _client(monkeypatch, t)
    rec, created = c.queue_qa_issue("+1", "medium", "s2", [], "T2")
    assert not created and not t.created
    rid, fields = t.updated[0]
    assert rid == "recOLD" and fields["runs"] == 3 and fields["severity"] == "medium"
    assert "status" not in fields and "flagged_at" not in fields  # untouched on refresh


# ── Git worktrees against a throwaway repo ───────────────────────────────────

def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def scratch_repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    repo = tmp_path / "repo"
    _git("clone", "-q", str(origin), str(repo), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "agents").mkdir()
    (repo / "agents" / "transcript_reviewer.py").write_text("# v1\n")
    (repo / "agents" / "messaging_agent.py").write_text("# v1\n")
    (repo / ".env").write_text("ANTHROPIC_API_KEY=x\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-q", "-u", "origin", "main", cwd=repo)
    monkeypatch.setattr(ql, "REPO", repo)
    monkeypatch.setattr(ql, "QA_ROOT", tmp_path / "qa")
    monkeypatch.setattr(ql, "MAIN_WT", tmp_path / "qa" / "_main")
    return repo


def test_worktree_lifecycle(scratch_repo):
    rec = {"fields": {"qa_id": 5, "summary": "Coin switch"}}
    wt, br = ql.ensure_worktree(rec)
    assert br == "qa/5-coin-switch" and wt.is_dir() and (wt / ".env").exists()
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt) == br
    # main checkout untouched, and a second call reuses instead of failing
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=scratch_repo) == "main"
    assert ql.ensure_worktree(rec) == (wt, br)
    # a reviewer-only change self-approves; touching Lily does not
    (wt / "agents" / "transcript_reviewer.py").write_text("# v2\n")
    _git("commit", "-q", "-am", "teach the judge", cwd=wt)
    assert ql.changed_files(wt) == ["agents/transcript_reviewer.py"]
    assert ql.self_approvable(ql.changed_files(wt))
    (wt / "agents" / "messaging_agent.py").write_text("# v2\n")
    _git("commit", "-q", "-am", "fix lily", cwd=wt)
    assert not ql.self_approvable(ql.changed_files(wt))
    ql.remove_worktree(rec)
    assert not wt.exists()
    assert not ql._branch_exists(br)


def test_main_worktree_tracks_a_sha(scratch_repo):
    sha = _git("rev-parse", "origin/main", cwd=scratch_repo)
    wt = ql._ensure_main_worktree(sha)
    assert _git("rev-parse", "HEAD", cwd=wt) == sha and (wt / ".env").exists()
    # a new commit on origin/main → the same directory moves to it
    _git("commit", "-q", "--allow-empty", "-m", "more", cwd=scratch_repo)
    _git("push", "-q", "origin", "main", cwd=scratch_repo)
    sha2 = _git("rev-parse", "origin/main", cwd=scratch_repo)
    ql._git("fetch", "-q", "origin", "main")
    assert ql._ensure_main_worktree(sha2) == wt
    assert _git("rev-parse", "HEAD", cwd=wt) == sha2
