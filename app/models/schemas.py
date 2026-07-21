"""
Pydantic schemas for Git Guardian AI.

These models define the data contracts between agents, the API, and the database.
"""

import re
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from enum import Enum
from datetime import datetime


# ─── Severity Levels ───────────────────────────────────────────────────────────

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# ─── Agent Finding (standardized across all agents) ───────────────────────────

class Finding(BaseModel):
    """A single finding produced by a specialist agent.
    
    Every finding MUST trace back to a specific tool output (source_tool).
    The LLM may add explanation/triage but never invents findings from scratch.
    """
    source_tool: str = Field(..., description="Tool that produced this finding (e.g. 'bandit', 'semgrep', 'eslint')")
    agent: str = Field(..., description="Agent that produced this finding (e.g. 'security', 'quality')")
    file: str = Field(..., description="File path relative to repo root")
    line: int = Field(default=0, description="Line number (0 if not applicable)")
    severity: Severity = Field(default=Severity.MEDIUM)
    message: str = Field(..., description="Human-readable description of the finding")
    suggested_fix: Optional[str] = Field(default=None, description="Optional suggested fix")
    rule_id: Optional[str] = Field(default=None, description="Rule ID from the static analysis tool")
    context: Optional[str] = Field(default=None, description="Code snippet or context")
    standard_citation: Optional[str] = Field(default=None, description="Coding standard that was violated (Quality Agent)")
    confirmed_by: Optional[List[dict]] = Field(default=None, description="List of {tool, rule} dicts when multiple tools detected the same issue")


class AgentResult(BaseModel):
    """Standardized result from any specialist agent."""
    agent_name: str
    findings: List[Finding] = Field(default_factory=list)
    summary: str = Field(default="")
    error: Optional[str] = Field(default=None, description="Error message if agent failed")
    execution_time_seconds: float = Field(default=0.0)


# ─── PR Review (aggregated by Supervisor) ──────────────────────────────────────

class PRReview(BaseModel):
    """Aggregated review posted to a PR."""
    repo_full_name: str
    pr_number: int
    commit_sha: str
    findings: List[Finding] = Field(default_factory=list)
    summary: str = Field(default="")
    code_health_score: float = Field(default=0.0, ge=0, le=100, description="0-100 health score")
    auto_fix_branch: Optional[str] = Field(default=None, description="Branch name if auto-fix was generated")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    agent_results: List[AgentResult] = Field(default_factory=list)


# ─── Webhook Payload (subset of GitHub PR event) ──────────────────────────────

class PRWebhookPayload(BaseModel):
    """Parsed GitHub pull_request webhook payload."""
    action: str
    pr_number: int
    repo_full_name: str
    head_sha: str
    base_branch: str
    head_branch: str
    pr_title: str
    pr_url: str
    sender: str


# ─── GitHub URL Validation (Scan Repo Feature) ────────────────────────────────

# Strict regex: only matches github.com repos, no query strings, fragments, or path traversal
_GITHUB_REPO_PATTERN = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[a-zA-Z0-9](?:[a-zA-Z0-9._-]{0,37}[a-zA-Z0-9])?)/"
    r"(?P<repo>[a-zA-Z0-9._-]{1,100}?)(?:\.git)?/?$"
)


class ScanRepoRequest(BaseModel):
    """Validated request to scan a GitHub repository for open PRs.

    Security measures:
    - Strict regex enforces only `https://github.com/owner/repo` format.
    - No query strings, fragments, path traversal, or encoded characters.
    - Owner/repo names sanitized to GitHub-legal character sets.
    - URL length hard-capped to prevent payload abuse.
    """

    github_url: str = Field(
        ...,
        min_length=19,      # https://github.com/a/b
        max_length=250,     # Hard cap to block oversized payloads
        description="Full HTTPS GitHub repository URL",
        examples=["https://github.com/owner/repo"],
    )

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """Enforce strict GitHub URL format with multiple security checks."""
        # Strip whitespace
        v = v.strip()

        # Block encoded characters (prevent URL-encoding bypass)
        if "%" in v or "\\" in v:
            raise ValueError("URL must not contain encoded or escaped characters.")

        # Block obvious injection patterns
        dangerous = [";", "&&", "|", "`", "$(", "${", "<", ">", "\n", "\r"]
        for char in dangerous:
            if char in v:
                raise ValueError("URL contains illegal characters.")

        # Must match strict GitHub pattern
        match = _GITHUB_REPO_PATTERN.match(v)
        if not match:
            raise ValueError(
                "Invalid GitHub URL. Expected format: https://github.com/owner/repo"
            )

        owner = match.group("owner")
        repo = match.group("repo")

        # Block reserved / dangerous names
        blocked = {".", "..", "_", "-", "login", "settings", "api", "graphql", "raw"}
        if owner.lower() in blocked or repo.lower() in blocked:
            raise ValueError("Repository owner or name is invalid.")

        # Normalize: return clean canonical form
        return f"https://github.com/{owner}/{repo}"

    @property
    def repo_full_name(self) -> str:
        """Extract 'owner/repo' from the validated URL."""
        # Safe: URL is already validated by the field_validator above
        match = _GITHUB_REPO_PATTERN.match(self.github_url)
        return f"{match.group('owner')}/{match.group('repo')}"


class ScanRepoResponse(BaseModel):
    """Response listing open PRs for a repository."""
    repo_full_name: str
    open_prs: List[dict] = Field(default_factory=list)
    message: str = ""


class TriggerPRScanRequest(BaseModel):
    """Request to trigger a security pipeline on a specific PR."""
    github_url: str = Field(..., min_length=19, max_length=250)
    pr_number: int = Field(..., gt=0, le=999999, description="PR number to scan")

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        """Reuse the same strict validation."""
        return ScanRepoRequest.validate_github_url(v)


# ─── Dashboard / DB Models ────────────────────────────────────────────────────

class ReviewRecord(BaseModel):
    """Record stored in Postgres for dashboard display."""
    id: Optional[int] = None
    repo_full_name: str
    pr_number: int
    commit_sha: str
    total_findings: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    code_health_score: float = 0.0
    review_duration_seconds: float = 0.0
    auto_fix_branch: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    findings_json: Optional[str] = None  # JSON-serialized findings for detail view
