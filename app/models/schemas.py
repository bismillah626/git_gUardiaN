"""
Pydantic schemas for Git Guardian AI.

These models define the data contracts between agents, the API, and the database.
"""

from pydantic import BaseModel, Field
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
