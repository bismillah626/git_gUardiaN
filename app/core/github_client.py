"""
GitHub API integration for CodeGuardian AI.

Handles: authentication, fetching PR diffs, posting comments,
creating branches, and committing auto-fix files.
"""

import logging
from typing import Optional, List, Dict

from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from app.core.config import settings

logger = logging.getLogger(__name__)


class GitHubClient:
    """Wrapper around PyGithub for all GitHub operations."""

    def __init__(self, token: Optional[str] = None):
        self._token = token or settings.github_token
        if not self._token:
            raise ValueError("GITHUB_TOKEN is required. Set it in .env or environment.")
        self._gh = Github(self._token)

    @property
    def gh(self) -> Github:
        return self._gh

    def get_repo(self, full_name: str) -> Repository:
        """Get a repository by owner/name."""
        return self._gh.get_repo(full_name)

    def get_pr(self, repo_full_name: str, pr_number: int) -> PullRequest:
        """Get a specific pull request."""
        repo = self.get_repo(repo_full_name)
        return repo.get_pull(pr_number)

    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the unified diff for a PR.
        
        Returns the diff as a string (unified diff format).
        """
        import httpx
        
        pr = self.get_pr(repo_full_name, pr_number)
        # Use the diff URL provided by GitHub
        diff_url = pr.diff_url
        
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        
        response = httpx.get(diff_url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def get_pr_files(self, repo_full_name: str, pr_number: int) -> List[Dict]:
        """Get list of changed files with their patches.
        
        Returns list of dicts with keys: filename, status, patch, additions, deletions.
        """
        pr = self.get_pr(repo_full_name, pr_number)
        files = []
        for f in pr.get_files():
            files.append({
                "filename": f.filename,
                "status": f.status,  # added, removed, modified, renamed
                "patch": f.patch or "",
                "additions": f.additions,
                "deletions": f.deletions,
                "sha": f.sha,
            })
        return files

    def get_file_content(self, repo_full_name: str, path: str, ref: str = "main") -> str:
        """Get the content of a single file from the repo at a given ref."""
        repo = self.get_repo(repo_full_name)
        try:
            content = repo.get_contents(path, ref=ref)
            if isinstance(content, list):
                raise ValueError(f"{path} is a directory, not a file")
            return content.decoded_content.decode("utf-8")
        except GithubException as e:
            logger.warning(f"Could not fetch {path}@{ref}: {e}")
            return ""

    def post_pr_comment(self, repo_full_name: str, pr_number: int, body: str) -> None:
        """Post a comment on a pull request."""
        pr = self.get_pr(repo_full_name, pr_number)
        pr.create_issue_comment(body)
        logger.info(f"Posted review comment on {repo_full_name}#{pr_number}")

    def create_branch(self, repo_full_name: str, branch_name: str, source_sha: str) -> None:
        """Create a new branch from a specific commit SHA."""
        repo = self.get_repo(repo_full_name)
        try:
            repo.create_git_ref(f"refs/heads/{branch_name}", source_sha)
            logger.info(f"Created branch {branch_name} on {repo_full_name}")
        except GithubException as e:
            if e.status == 422:
                logger.warning(f"Branch {branch_name} already exists")
            else:
                raise

    def commit_file(
        self,
        repo_full_name: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        """Create or update a file on a branch (for auto-fix commits)."""
        repo = self.get_repo(repo_full_name)
        try:
            # Try to get existing file to update
            existing = repo.get_contents(path, ref=branch)
            repo.update_file(
                path=path,
                message=message,
                content=content,
                sha=existing.sha,
                branch=branch,
            )
        except GithubException:
            # File doesn't exist yet — create it
            repo.create_file(
                path=path,
                message=message,
                content=content,
                branch=branch,
            )
        logger.info(f"Committed fix to {path} on {branch}")

    def create_pr(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> int:
        """Open a pull request. Returns PR number."""
        repo = self.get_repo(repo_full_name)
        pr = repo.create_pull(title=title, body=body, head=head, base=base)
        logger.info(f"Created auto-fix PR #{pr.number} on {repo_full_name}")
        return pr.number
