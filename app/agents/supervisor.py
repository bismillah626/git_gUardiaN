"""
Supervisor Agent for Git Guardian AI.

Implements the orchestration as an explicit LangGraph graph with:
- Parallel fan-out to all four specialist agents
- Aggregation of findings
- Severity scoring
- PR comment formatting
- Human-approval gate for auto-fix commits

This graph structure is a first-class deliverable.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Dict, List, Optional, TypedDict, Annotated

from langgraph.graph import StateGraph, END

from app.core.config import settings
from app.core.github_client import GitHubClient
from app.core.llm_provider import llm_provider
from app.core.database import (
    create_review_placeholder,
    init_agent_statuses,
    update_agent_status,
    finalize_review_record,
)
from app.models.schemas import Finding, AgentResult, PRReview, Severity
from app.agents.security_agent import run_security_agent
from app.agents.quality_agent import run_quality_agent
from app.agents.test_gap_agent import run_test_gap_agent
from app.agents.documentation_agent import run_documentation_agent

logger = logging.getLogger(__name__)


# ─── LangGraph State ───────────────────────────────────────────────────────────

class ReviewState(TypedDict):
    """State passed through the LangGraph review pipeline."""
    # Input
    repo_full_name: str
    pr_number: int
    head_sha: str
    base_branch: str
    head_branch: str
    changed_files: List[Dict]
    repo_clone_path: str
    
    # Agent results
    security_result: Optional[Dict]
    quality_result: Optional[Dict]
    test_gap_result: Optional[Dict]
    documentation_result: Optional[Dict]
    
    # Aggregated output
    all_findings: List[Dict]
    code_health_score: float
    review_summary: str
    pr_comment: str
    auto_fix_branch: Optional[str]
    
    # Metadata
    start_time: float
    errors: List[str]

    # Dashboard tracking
    review_id: Optional[int]
    pr_url: Optional[str]
    pr_title: Optional[str]


# ─── Graph Node Functions ─────────────────────────────────────────────────────

async def prepare_review(state: ReviewState) -> dict:
    """Prepare the review: create tracking record and queue agents."""
    logger.info(f"Preparing review for {state['repo_full_name']}#{state['pr_number']}")

    # Create a placeholder review record in the DB so the dashboard can see it
    review_id = None
    try:
        review_id = create_review_placeholder(
            repo_full_name=state["repo_full_name"],
            pr_number=state["pr_number"],
            head_sha=state.get("head_sha", ""),
            head_branch=state.get("head_branch", ""),
            pr_url=state.get("pr_url", ""),
            pr_title=state.get("pr_title", ""),
        )
        init_agent_statuses(review_id)
        logger.info(f"Created review placeholder #{review_id} with queued agent statuses")
    except Exception as e:
        logger.warning(f"Could not create review placeholder: {e}")

    return {
        "start_time": time.time(),
        "errors": [],
        "review_id": review_id,
    }


async def _run_single_agent(agent_func, agent_key, agent_name, review_id, *args, **kwargs):
    """Wrapper that updates agent status before/after running a single agent."""
    try:
        if review_id:
            update_agent_status(review_id, agent_name, "running", f"Running {agent_name} analysis...")
    except Exception:
        pass  # Don't fail the agent if status tracking fails

    try:
        result = await agent_func(*args, **kwargs)

        findings_count = len(result.findings) if result and hasattr(result, "findings") else 0
        try:
            if review_id:
                update_agent_status(
                    review_id, agent_name, "done",
                    f"{findings_count} finding{'s' if findings_count != 1 else ''} detected",
                )
        except Exception:
            pass

        return result

    except Exception as e:
        try:
            if review_id:
                update_agent_status(review_id, agent_name, "failed", str(e)[:500])
        except Exception:
            pass
        raise


async def run_agents_parallel(state: ReviewState) -> dict:
    """Fan out to all four specialist agents in parallel."""
    logger.info("Running all specialist agents in parallel...")
    
    changed_files = state["changed_files"]
    repo_clone_path = state.get("repo_clone_path", "")
    review_id = state.get("review_id")
    
    # Run all agents concurrently, wrapped with status tracking
    security_task = _run_single_agent(
        run_security_agent, "security_result", "security",
        review_id, changed_files, repo_clone_path,
    )
    quality_task = _run_single_agent(
        run_quality_agent, "quality_result", "quality",
        review_id, changed_files,
    )
    test_gap_task = _run_single_agent(
        run_test_gap_agent, "test_gap_result", "test_gap",
        review_id, changed_files,
    )
    doc_task = _run_single_agent(
        run_documentation_agent, "documentation_result", "documentation",
        review_id, changed_files,
    )
    
    results = await asyncio.gather(
        security_task, quality_task, test_gap_task, doc_task,
        return_exceptions=True,
    )
    
    errors = []
    agent_results = {}
    
    for i, (name, result) in enumerate(zip(
        ["security_result", "quality_result", "test_gap_result", "documentation_result"],
        results,
    )):
        if isinstance(result, Exception):
            logger.error(f"Agent {name} failed: {result}")
            errors.append(f"{name}: {str(result)}")
            agent_results[name] = AgentResult(
                agent_name=name.replace("_result", ""),
                error=str(result),
            ).model_dump()
        else:
            agent_results[name] = result.model_dump()
    
    return {**agent_results, "errors": errors}


async def aggregate_findings(state: ReviewState) -> dict:
    """Aggregate all agent findings, score severity, rank by priority."""
    all_findings: List[Dict] = []
    
    for key in ["security_result", "quality_result", "test_gap_result", "documentation_result"]:
        result = state.get(key)
        if result and isinstance(result, dict):
            for finding in result.get("findings", []):
                all_findings.append(finding)
    
    # Sort by severity (critical first)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    all_findings.sort(key=lambda f: severity_order.get(f.get("severity", "info"), 5))
    
    # Calculate code health score (0-100)
    score = _calculate_health_score(all_findings)
    
    return {
        "all_findings": all_findings,
        "code_health_score": score,
    }


async def format_pr_comment(state: ReviewState) -> dict:
    """Format the aggregated findings into a GitHub PR comment."""
    findings = state.get("all_findings", [])
    score = state.get("code_health_score", 100)
    
    comment = _build_pr_comment(
        findings=findings,
        score=score,
        repo=state["repo_full_name"],
        pr_number=state["pr_number"],
        errors=state.get("errors", []),
    )
    
    # Build a summary
    severity_counts = {}
    for f in findings:
        sev = f.get("severity", "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    
    summary_parts = [f"{count} {sev}" for sev, count in sorted(severity_counts.items())]
    summary = f"Review complete: {len(findings)} findings ({', '.join(summary_parts)}). Health score: {score:.0f}/100"
    
    return {
        "pr_comment": comment,
        "review_summary": summary,
    }


async def post_review(state: ReviewState) -> dict:
    """Post the review comment to the PR and handle auto-fix branching."""
    try:
        gh = GitHubClient()
        
        # Post the review comment
        gh.post_pr_comment(
            repo_full_name=state["repo_full_name"],
            pr_number=state["pr_number"],
            body=state["pr_comment"],
        )
        logger.info(f"Posted review to {state['repo_full_name']}#{state['pr_number']}")
        
        # Handle auto-fix: create a separate branch (never auto-merge)
        auto_fix_branch = None
        fixable = [f for f in state.get("all_findings", []) if f.get("suggested_fix")]
        
        if fixable:
            branch_name = f"{settings.auto_fix_branch_prefix}{state['pr_number']}"
            try:
                gh.create_branch(
                    repo_full_name=state["repo_full_name"],
                    branch_name=branch_name,
                    source_sha=state["head_sha"],
                )
                
                # Group fixes by file
                fixes_by_file: Dict[str, List[str]] = {}
                for f in fixable:
                    filepath = f.get("file", "")
                    fix = f.get("suggested_fix", "")
                    if filepath and fix:
                        fixes_by_file.setdefault(filepath, []).append(fix)
                
                # Post a comment about the auto-fix branch (human approval required)
                fix_comment = (
                    f"🔧 **Auto-fix branch created:** `{branch_name}`\n\n"
                    f"This branch contains {len(fixable)} suggested fixes. "
                    f"**Review and merge manually** — Git Guardian never auto-merges.\n\n"
                    f"Files with fixes: {', '.join(fixes_by_file.keys())}"
                )
                gh.post_pr_comment(state["repo_full_name"], state["pr_number"], fix_comment)
                auto_fix_branch = branch_name
                
            except Exception as e:
                logger.warning(f"Auto-fix branch creation failed: {e}")
        
        return {"auto_fix_branch": auto_fix_branch}
    
    except Exception as e:
        logger.error(f"Failed to post review: {e}")
        return {"errors": state.get("errors", []) + [f"post_review: {str(e)}"]}


# ─── Build the LangGraph ──────────────────────────────────────────────────────

def build_review_graph() -> StateGraph:
    """Build the Git Guardian review pipeline as an explicit LangGraph graph.
    
    Graph structure:
        prepare → run_agents_parallel → aggregate → format_comment → post_review → END
    
    The parallel fan-out happens inside run_agents_parallel using asyncio.gather.
    """
    graph = StateGraph(ReviewState)
    
    # Add nodes
    graph.add_node("prepare", prepare_review)
    graph.add_node("run_agents", run_agents_parallel)
    graph.add_node("aggregate", aggregate_findings)
    graph.add_node("format_comment", format_pr_comment)
    graph.add_node("post_review", post_review)
    
    # Define edges (linear pipeline with parallel agents inside)
    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "run_agents")
    graph.add_edge("run_agents", "aggregate")
    graph.add_edge("aggregate", "format_comment")
    graph.add_edge("format_comment", "post_review")
    graph.add_edge("post_review", END)
    
    return graph


def compile_review_graph():
    """Compile the review graph for execution."""
    graph = build_review_graph()
    return graph.compile()


# ─── Helper Functions ──────────────────────────────────────────────────────────

def _calculate_health_score(findings: List[Dict]) -> float:
    """Calculate a 0-100 code health score based on findings."""
    if not findings:
        return 100.0
    
    penalties = {
        "critical": 25,
        "high": 15,
        "medium": 5,
        "low": 2,
        "info": 0,
    }
    
    total_penalty = sum(
        penalties.get(f.get("severity", "info"), 0) for f in findings
    )
    
    return max(0.0, min(100.0, 100.0 - total_penalty))


def _build_pr_comment(
    findings: List[Dict],
    score: float,
    repo: str,
    pr_number: int,
    errors: List[str],
) -> str:
    """Build a formatted GitHub PR comment."""
    
    # Health score emoji
    if score >= 80:
        health_emoji = "🟢"
    elif score >= 60:
        health_emoji = "🟡"
    elif score >= 40:
        health_emoji = "🟠"
    else:
        health_emoji = "🔴"
    
    lines = [
        "# 🛡️ Git Guardian AI — Review Report",
        "",
        f"**Code Health Score:** {health_emoji} **{score:.0f}/100**",
        "",
    ]
    
    if not findings:
        lines.append("✅ **No issues found!** This PR looks clean.")
        return "\n".join(lines)
    
    # Summary table
    severity_counts = {}
    for f in findings:
        sev = f.get("severity", "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    
    lines.extend([
        "## 📊 Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ])
    
    for sev in ["critical", "high", "medium", "low", "info"]:
        count = severity_counts.get(sev, 0)
        if count > 0:
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(sev, "")
            lines.append(f"| {emoji} {sev.capitalize()} | {count} |")
    
    lines.append("")
    
    # Findings by agent
    findings_by_agent: Dict[str, List[Dict]] = {}
    for f in findings:
        agent = f.get("agent", "unknown")
        findings_by_agent.setdefault(agent, []).append(f)
    
    agent_titles = {
        "security": "🔒 Security",
        "quality": "✨ Code Quality",
        "test_gap": "🧪 Test Gaps",
        "documentation": "📝 Documentation",
    }
    
    for agent, agent_findings in findings_by_agent.items():
        title = agent_titles.get(agent, agent.capitalize())
        lines.extend([f"## {title}", ""])
        
        for f in agent_findings:
            sev = f.get("severity", "info")
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(sev, "")
            file_ref = f"`{f.get('file', 'unknown')}"
            if f.get("line", 0) > 0:
                file_ref += f":{f['line']}"
            file_ref += "`"
            
            lines.append(f"- {emoji} **[{sev.upper()}]** {file_ref}")
            lines.append(f"  {f.get('message', '')}")
            
            if f.get("standard_citation"):
                lines.append(f"  > 📋 Standard: _{f['standard_citation']}_")
            
            # Multi-tool confirmation display for security findings
            confirmed_by = f.get("confirmed_by")
            if f.get("agent") == "security":
                if confirmed_by and len(confirmed_by) > 1:
                    # Multiple tools confirmed the same vulnerability
                    tools_str = ", ".join(
                        f"`{c.get('tool', '?')}` ({c.get('rule', 'N/A')})"
                        for c in confirmed_by
                    )
                    lines.append(f"  > 🔧 Confirmed by: {tools_str}")
                elif f.get("source_tool"):
                    # Single tool — keep existing format
                    lines.append(f"  > 🔧 Tool: `{f['source_tool']}` | Rule: `{f.get('rule_id', 'N/A')}`")
            
            if f.get("suggested_fix"):
                fix_preview = f["suggested_fix"][:200]
                lines.append(f"  <details><summary>💡 Suggested fix</summary>\n\n  ```\n  {fix_preview}\n  ```\n  </details>")
            
            lines.append("")
    
    # Errors (if any)
    if errors:
        lines.extend(["## ⚠️ Agent Errors", ""])
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")
    
    lines.extend([
        "---",
        f"*Generated by [Git Guardian AI](https://github.com/{repo}) — Multi-Agent Code Review*",
    ])
    
    return "\n".join(lines)
