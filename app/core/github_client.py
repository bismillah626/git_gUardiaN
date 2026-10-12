"""
GitHub API integration for Git Guardian AI.

Handles: authentication, fetching PR diffs, posting comments,
creating branches, and committing auto-fix files.
"""

import asyncio
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Optional, List, Dict, Callable, Any

from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from app.core.config import settings

logger = logging.getLogger(__name__)
_request_github_token: ContextVar[Optional[str]] = ContextVar(
    "request_github_token", default=None
)


class GitHubRateLimiter:
    """Token bucket rate limiter for GitHub API requests."""
    
    def __init__(self, requests_per_minute: int):
        self.rate = requests_per_minute / 60.0  # requests per second
        self.capacity = requests_per_minute
        self.tokens = float(requests_per_minute)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                wait_time = (1.0 - self.tokens) / self.rate

            # Sleep OUTSIDE the lock so other coroutines can proceed
            await asyncio.sleep(wait_time)


def github_rate_limited(func: Callable) -> Callable:
    """Decorator to add GitHub rate limiting to async methods."""
    @wraps(func)
    async def wrapper(self, *args, **kwargs) -> Any:
        await self._rate_limiter.acquire()
        return await func(self, *args, **kwargs)
    return wrapper


@contextmanager
def github_token_context(token: Optional[str]):
    """Make a request-scoped token available to pipeline GitHub clients."""
    context_token = _request_github_token.set(token)
    try:
        yield
    finally:
        _request_github_token.reset(context_token)


class GitHubClient:
    """Wrapper around PyGithub for all GitHub operations."""
    
    # Shared rate limiter across all instances
    _rate_limiter = GitHubRateLimiter(settings.github_requests_per_minute)

    def __init__(self, token: Optional[str] = None):
        self._token = token or _request_github_token.get() or settings.github_token
        if not self._token:
            raise ValueError("GITHUB_TOKEN is required. Set it in .env or environment.")
        self._gh = Github(self._token)

    @property
    def gh(self) -> Github:
        return self._gh

    async def get_repo(self, full_name: str) -> Repository:
        """Get a repository by owner/name."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._gh.get_repo(full_name))

    async def get_pr(self, repo_full_name: str, pr_number: int) -> PullRequest:
        """Get a specific pull request."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        repo = await loop.run_in_executor(None, lambda: self._gh.get_repo(repo_full_name))
        return await loop.run_in_executor(None, lambda: repo.get_pull(pr_number))

    async def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the unified diff for a PR.
        
        Returns the diff as a string (unified diff format).
        """
        import httpx
        
        await self._rate_limiter.acquire()
        pr = await self.get_pr(repo_full_name, pr_number)
        # Use the diff URL provided by GitHub
        diff_url = pr.diff_url
        
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(diff_url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            return response.text

    async def get_pr_files(self, repo_full_name: str, pr_number: int) -> List[Dict]:
        """Get list of changed files with their patches.
        
        Returns list of dicts with keys: filename, status, patch, additions, deletions.
        """
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        pr = await self.get_pr(repo_full_name, pr_number)
        files = []
        gh_files = await loop.run_in_executor(None, lambda: list(pr.get_files()))
        for f in gh_files:
            files.append({
                "filename": f.filename,
                "status": f.status,  # added, removed, modified, renamed
                "patch": f.patch or "",
                "additions": f.additions,
                "deletions": f.deletions,
                "sha": f.sha,
            })
        return files

    async def get_file_content(self, repo_full_name: str, path: str, ref: str = "main") -> str:
        """Get the content of a single file from the repo at a given ref."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        repo = await loop.run_in_executor(None, lambda: self._gh.get_repo(repo_full_name))
        try:
            content = await loop.run_in_executor(None, lambda: repo.get_contents(path, ref=ref))
            if isinstance(content, list):
                raise ValueError(f"{path} is a directory, not a file")
            return content.decoded_content.decode("utf-8")
        except GithubException as e:
            logger.warning(f"Could not fetch {path}@{ref}: {e}")
            return ""

    async def post_pr_comment(self, repo_full_name: str, pr_number: int, body: str) -> None:
        """Post a comment on a pull request."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        pr = await self.get_pr(repo_full_name, pr_number)
        await loop.run_in_executor(None, lambda: pr.create_issue_comment(body))
        logger.info(f"Posted review comment on {repo_full_name}#{pr_number}")

    async def create_branch(self, repo_full_name: str, branch_name: str, source_sha: str) -> None:
        """Create a new branch from a specific commit SHA."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        repo = await loop.run_in_executor(None, lambda: self._gh.get_repo(repo_full_name))
        try:
            await loop.run_in_executor(
                None, lambda: repo.create_git_ref(f"refs/heads/{branch_name}", source_sha)
            )
            logger.info(f"Created branch {branch_name} on {repo_full_name}")
        except GithubException as e:
            if e.status == 422:
                logger.warning(f"Branch {branch_name} already exists")
            else:
                raise

    async def commit_file(
        self,
        repo_full_name: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        """Create or update a file on a branch (for auto-fix commits)."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        repo = await loop.run_in_executor(None, lambda: self._gh.get_repo(repo_full_name))
        try:
            # Try to get existing file to update
            existing = await loop.run_in_executor(None, lambda: repo.get_contents(path, ref=branch))
            await loop.run_in_executor(
                None,
                lambda: repo.update_file(
                    path=path,
                    message=message,
                    content=content,
                    sha=existing.sha,
                    branch=branch,
                ),
            )
        except GithubException:
            # File doesn't exist yet — create it
            await loop.run_in_executor(
                None,
                lambda: repo.create_file(
                    path=path,
                    message=message,
                    content=content,
                    branch=branch,
                ),
            )
        logger.info(f"Committed fix to {path} on {branch}")

    async def create_pr(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> int:
        """Open a pull request. Returns PR number."""
        await self._rate_limiter.acquire()
        loop = asyncio.get_event_loop()
        repo = await loop.run_in_executor(None, lambda: self._gh.get_repo(repo_full_name))
        pr = await loop.run_in_executor(
            None,
            lambda: repo.create_pull(title=title, body=body, head=head, base=base)
        )
        logger.info(f"Created auto-fix PR #{pr.number} on {repo_full_name}")
        return pr.number
