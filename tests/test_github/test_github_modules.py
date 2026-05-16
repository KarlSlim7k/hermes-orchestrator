"""Tests para T-11 (GitHubClient), T-12 (GitOperations), T-13 (PRManager), T-14 (ConfirmationHandler)."""

import json
from unittest.mock import patch, MagicMock
import pytest

from src.github.client import GitHubClient, GHCLIError, GHCommandResult
from src.github.git_ops import GitOperations, GitError, GitResult
from src.github.pr_manager import PRManager, PRError, PRInfo
from src.github.confirmation import (
    ConfirmationHandler,
    ConfirmationRequest,
    ConfirmationResponse,
    ConfirmAction,
)
from src.core.models import Task, TaskStatus, TaskType


# ====== T-11: GitHubClient ======


class TestGitHubClient:
    @pytest.fixture
    def client(self):
        return GitHubClient(gh_path="gh", timeout=30)

    def test_version(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="gh version 2.92.0", stderr="")
            v = client.version()
            assert "2.92.0" in v

    def test_version_failure(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not logged in")
            v = client.version()
            assert v == "unknown"

    def test_auth_status(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Logged in to github.com",
                stderr="",
            )
            status = client.auth_status()
            assert status["success"] is True
            assert "Logged in" in status["output"]

    def test_repo_view(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "name": "my-repo",
                    "owner": {"login": "myuser"},
                    "url": "https://github.com/myuser/my-repo",
                    "defaultBranchRef": {"name": "main"},
                }),
                stderr="",
            )
            data = client.repo_view()
            assert data["name"] == "my-repo"
            assert data["defaultBranchRef"]["name"] == "main"

    def test_repo_list(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"nameWithOwner": "user/repo1", "url": "https://...", "visibility": "public"},
                    {"nameWithOwner": "user/repo2", "url": "https://...", "visibility": "private"},
                ]),
                stderr="",
            )
            repos = client.repo_list()
            assert len(repos) == 2
            assert repos[0]["nameWithOwner"] == "user/repo1"

    def test_issue_list(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps([
                    {"number": 1, "title": "Bug fix", "state": "open"},
                ]),
                stderr="",
            )
            issues = client.issue_list(repo="user/repo")
            assert len(issues) == 1
            assert issues[0]["number"] == 1

    def test_api_call(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"login": "testuser"}),
                stderr="",
            )
            data = client.api("/user")
            assert data["login"] == "testuser"

    def test_gh_cli_error(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="not found",
            )
            with pytest.raises(GHCLIError) as exc_info:
                client.repo_view()
            assert exc_info.value.exit_code == 1

    def test_timeout_raises_error(self, client):
        with patch("src.github.client.subprocess.run") as mock_run:
            mock_run.side_effect = __import__("subprocess").TimeoutExpired("cmd", 30)
            with pytest.raises(GHCLIError) as exc_info:
                client.repo_view()
            assert exc_info.value.exit_code == -1


# ====== T-12: GitOperations ======


class TestGitOperations:
    @pytest.fixture
    def git(self, tmp_path):
        # Init a real git repo for testing.
        import subprocess
        import os
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(tmp_path),
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path),
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path),
            capture_output=True,
        )
        # Create initial commit so branches work properly.
        initial_file = os.path.join(tmp_path, "README.md")
        with open(initial_file, "w") as f:
            f.write("# test repo")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path),
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(tmp_path),
            capture_output=True,
        )
        return GitOperations(workdir=str(tmp_path))

    def test_current_branch(self, git):
        branch = git.current_branch()
        assert branch == "main"

    def test_is_clean(self, git):
        assert git.is_dirty() is False

    def test_is_dirty(self, git):
        (git.workdir_path if hasattr(git, "workdir_path") else git.workdir)
        import os
        test_file = os.path.join(git.workdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello")
        assert git.is_dirty() is True

    def test_changed_files(self, git):
        import os
        test_file = os.path.join(git.workdir, "file1.txt")
        with open(test_file, "w") as f:
            f.write("content")
        git.add(["file1.txt"])
        # Changed files shows staged after add.
        files = git.changed_files()
        assert "file1.txt" in files

    def test_create_and_checkout_branch(self, git):
        result = git.create_and_checkout("feature/test")
        assert result is True
        assert git.current_branch() == "feature/test"

    def test_commit(self, git):
        import os
        test_file = os.path.join(git.workdir, "commit_test.txt")
        with open(test_file, "w") as f:
            f.write("data")
        git.add()
        result = git.commit("test commit")
        assert result.success is True

    def test_commit_log(self, git):
        import os
        test_file = os.path.join(git.workdir, "log_test.txt")
        with open(test_file, "w") as f:
            f.write("data")
        git.add()
        git.commit("first commit")
        log = git.commit_log()
        assert len(log) >= 1
        assert "first commit" in log[0]

    def test_latest_commit_hash(self, git):
        import os
        test_file = os.path.join(git.workdir, "hash_test.txt")
        with open(test_file, "w") as f:
            f.write("data")
        git.add()
        git.commit("hash commit")
        h = git.latest_commit_hash()
        assert len(h) == 40  # SHA-1 hash length

    def test_list_branches(self, git):
        git.create_branch("feature/a")
        branches = git.list_branches()
        assert "feature/a" in branches

    def test_delete_branch(self, git):
        git.create_branch("to_delete")
        result = git.delete_branch("to_delete")
        assert result is True
        branches = git.list_branches()
        assert "to_delete" not in branches

    def test_get_remote_url_no_remote(self, git):
        url = git.get_remote_url()
        assert url is None

    def test_ensure_clean_or_stash(self, git):
        # Clean repo, should return True.
        assert git.ensure_clean_or_stash() is True

    def test_git_error(self, git):
        with pytest.raises(GitError):
            git._run_checked(["checkout", "nonexistent-branch-xyz"])


# ====== T-13: PRManager ======


class TestPRManager:
    @pytest.fixture
    def pr_mgr(self):
        return PRManager(gh_path="gh", timeout=30)

    def test_create_pr(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = json.dumps({
                "number": 42,
                "title": "Add feature",
                "state": "open",
                "url": "https://github.com/user/repo/pull/42",
                "headRefName": "feature/add",
                "baseRefName": "main",
            })
            pr = pr_mgr.create(
                title="Add feature",
                body="Description",
                base="main",
                head="feature/add",
            )
            assert pr.number == 42
            assert pr.url == "https://github.com/user/repo/pull/42"
            assert pr.head == "feature/add"

    def test_create_pr_fallback_text(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = "https://github.com/user/repo/pull/1"
            pr = pr_mgr.create(title="Test", body="body", base="main")
            assert pr.number == 0
            assert pr.url == "https://github.com/user/repo/pull/1"

    def test_view_pr(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = json.dumps({
                "number": 10,
                "title": "Fix bug",
                "state": "merged",
                "url": "https://github.com/user/repo/pull/10",
                "headRefName": "fix/bug",
                "baseRefName": "main",
                "body": "Fixed the bug",
                "author": {"login": "dev"},
                "createdAt": "2026-05-16T00:00:00Z",
                "mergedAt": "2026-05-16T01:00:00Z",
            })
            pr = pr_mgr.view(10)
            assert pr.number == 10
            assert pr.merged is True
            assert pr.author == "dev"

    def test_list_prs(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = json.dumps([
                {"number": 1, "title": "PR 1", "state": "open", "url": "https://.../1", "headRefName": "a", "baseRefName": "main"},
                {"number": 2, "title": "PR 2", "state": "open", "url": "https://.../2", "headRefName": "b", "baseRefName": "main"},
            ])
            prs = pr_mgr.list()
            assert len(prs) == 2
            assert prs[0].number == 1

    def test_list_prs_not_list(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = json.dumps({"not": "a list"})
            prs = pr_mgr.list()
            assert prs == []

    def test_merge_pr(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run") as mock:
            mock.return_value = (True, "", "", 0)
            result = pr_mgr.merge(42, method="squash")
            assert result is True

    def test_comment_on_pr(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run") as mock:
            mock.return_value = (True, "", "", 0)
            result = pr_mgr.comment(42, "LGTM!")
            assert result is True

    def test_pr_checks(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run_checked") as mock:
            mock.return_value = json.dumps([
                {"name": "tests", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ])
            checks = pr_mgr.checks(42)
            assert len(checks) == 2
            assert checks[0]["name"] == "tests"

    def test_pr_error_raises(self, pr_mgr):
        with patch("src.github.pr_manager.PRManager._run") as mock:
            mock.return_value = (False, "", "not found", 1)
            with pytest.raises(PRError) as exc_info:
                pr_mgr.view(999)
            assert exc_info.value.exit_code == 1


# ====== T-14: ConfirmationHandler ======


class TestConfirmationHandler:
    @pytest.fixture
    def handler(self):
        tm = MagicMock()
        return ConfirmationHandler(task_manager=tm)

    def test_needs_confirmation(self, handler):
        assert handler.needs_confirmation("commit") is True
        assert handler.needs_confirmation("push") is True
        assert handler.needs_confirmation("pull_request") is True
        assert handler.needs_confirmation("merge") is True
        assert handler.needs_confirmation("read") is False

    def test_create_request(self, handler):
        req = handler.create_request("task-1", "commit", "Commit changes")
        assert req.task_id == "task-1"
        assert req.action == "commit"
        assert "task-1" in handler._pending_requests

    def test_approve(self, handler):
        handler.create_request("task-1", "commit", "test")
        resp = handler.approve("task-1")
        assert resp.action == ConfirmAction.APPROVE
        assert "task-1" not in handler._pending_requests

    def test_reject(self, handler):
        handler.create_request("task-1", "push", "test")
        resp = handler.reject("task-1")
        assert resp.action == ConfirmAction.REJECT
        assert "task-1" not in handler._pending_requests

    def test_custom_confirm_fn_approve(self):
        tm = MagicMock()
        def custom_approve(req: ConfirmationRequest) -> ConfirmationResponse:
            return ConfirmationResponse(action=ConfirmAction.APPROVE)
        handler = ConfirmationHandler(task_manager=tm, confirm_fn=custom_approve)
        resp = handler.request_confirmation("t1", "commit", "test")
        assert resp.action == ConfirmAction.APPROVE

    def test_custom_confirm_fn_reject(self):
        tm = MagicMock()
        def custom_reject(req: ConfirmationRequest) -> ConfirmationResponse:
            return ConfirmationResponse(action=ConfirmAction.REJECT)
        handler = ConfirmationHandler(task_manager=tm, confirm_fn=custom_reject)
        resp = handler.request_confirmation("t1", "push", "test")
        assert resp.action == ConfirmAction.REJECT

    def test_commit_with_confirmation_approved(self, handler, tmp_path):
        # Setup real git repo.
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True)

        # Auto-approve.
        handler._confirm_fn = lambda req: ConfirmationResponse(action=ConfirmAction.APPROVE)

        task = MagicMock()
        task.id = "task-1"
        task.errors = []
        task.status = TaskStatus.PENDING

        git_ops = GitOperations(workdir=str(tmp_path))
        # Create a file to commit.
        import os
        with open(os.path.join(tmp_path, "new.txt"), "w") as f:
            f.write("content")

        result = handler.commit_with_confirmation(task, git_ops, "add new file")
        assert "Commit exitoso" in (result.result_summary or "")

    def test_commit_with_confirmation_rejected(self, handler):
        handler._confirm_fn = lambda req: ConfirmationResponse(action=ConfirmAction.REJECT)
        task = MagicMock()
        task.id = "t1"
        task.errors = []

        git_ops = MagicMock()
        result = handler.commit_with_confirmation(task, git_ops, "test")
        assert result.status == TaskStatus.CANCELLED

    def test_push_with_confirmation_rejected(self, handler):
        handler._confirm_fn = lambda req: ConfirmationResponse(action=ConfirmAction.REJECT)
        task = MagicMock()
        task.id = "t1"
        task.errors = []

        git_ops = MagicMock()
        result = handler.push_with_confirmation(task, git_ops)
        assert result.status == TaskStatus.CANCELLED

    def test_pr_with_confirmation_rejected(self, handler):
        handler._confirm_fn = lambda req: ConfirmationResponse(action=ConfirmAction.REJECT)
        task = MagicMock()
        task.id = "t1"
        task.errors = []
        task.branch = "feature/test"

        pr_mgr = MagicMock()
        result = handler.pr_with_confirmation(task, pr_mgr, "Test PR", "body")
        assert result.status == TaskStatus.CANCELLED

    def test_pr_with_confirmation_error(self, handler):
        handler._confirm_fn = lambda req: ConfirmationResponse(action=ConfirmAction.APPROVE)
        task = MagicMock()
        task.id = "t1"
        task.errors = []
        task.branch = "feature/test"
        task.metadata = {}

        pr_mgr = MagicMock()
        pr_mgr.create.side_effect = PRError("API error", 1, "error")
        result = handler.pr_with_confirmation(task, pr_mgr, "Test", "body")
        assert len(result.errors) > 0
