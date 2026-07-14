"""
CodeGuardian AI — FastAPI Entrypoint.

Webhook receiver for GitHub PR events. Validates signatures,
parses payloads, and triggers the LangGraph review pipeline.
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db, save_review
from app.core.github_client import GitHubClient
from app.models.schemas import PRWebhookPayload, Severity
from app.agents.supervisor import compile_review_graph
from app.services.rag_service import rag_service

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle events."""
    logger.info("🚀 CodeGuardian AI starting up...")
    
    # Initialize database tables
    try:
        init_db()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.warning(f"⚠️ Database init failed (will retry on first use): {e}")
    
    # Index coding standards into RAG
    try:
        count = rag_service.index_coding_standards()
        logger.info(f"✅ Indexed {count} coding standard sections into ChromaDB")
    except Exception as e:
        logger.warning(f"⚠️ RAG indexing failed: {e}")
    
    yield
    
    logger.info("👋 CodeGuardian AI shutting down")


# ─── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="CodeGuardian AI",
    description="Multi-Agent AI Code Review & DevSecOps Platform",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── Health Check ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "CodeGuardian AI",
        "status": "running",
        "version": "1.0.0",
        "description": "Multi-Agent AI Code Review & DevSecOps Platform",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ─── Webhook Endpoint ─────────────────────────────────────────────────────────

@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive and process GitHub webhook events.
    
    Validates the webhook signature, parses the PR event payload,
    and triggers the review pipeline as a background task.
    """
    # ── Step 1: Signature verification ──────────────────────────────
    body = await request.body()
    
    if settings.github_webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(body, signature, settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    # ── Step 2: Parse event ─────────────────────────────────────────
    event_type = request.headers.get("X-GitHub-Event", "")
    
    if event_type != "pull_request":
        return JSONResponse(
            content={"message": f"Ignored event type: {event_type}"},
            status_code=200,
        )
    
    payload = json.loads(body)
    action = payload.get("action", "")
    
    # Only process opened/synchronized (new commits pushed) PRs
    if action not in ("opened", "synchronize", "reopened"):
        return JSONResponse(
            content={"message": f"Ignored PR action: {action}"},
            status_code=200,
        )
    
    # ── Step 3: Extract PR data ─────────────────────────────────────
    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    
    webhook_payload = PRWebhookPayload(
        action=action,
        pr_number=pr_data.get("number", 0),
        repo_full_name=repo_data.get("full_name", ""),
        head_sha=pr_data.get("head", {}).get("sha", ""),
        base_branch=pr_data.get("base", {}).get("ref", "main"),
        head_branch=pr_data.get("head", {}).get("ref", ""),
        pr_title=pr_data.get("title", ""),
        pr_url=pr_data.get("html_url", ""),
        sender=payload.get("sender", {}).get("login", ""),
    )
    
    logger.info(
        f"📨 Received PR webhook: {webhook_payload.repo_full_name}#{webhook_payload.pr_number} "
        f"({webhook_payload.action}) by {webhook_payload.sender}"
    )
    
    # ── Step 4: Trigger review pipeline in background ───────────────
    background_tasks.add_task(run_review_pipeline, webhook_payload)
    
    return JSONResponse(
        content={
            "message": "Review pipeline triggered",
            "pr": f"{webhook_payload.repo_full_name}#{webhook_payload.pr_number}",
        },
        status_code=202,
    )


# ─── Manual Trigger Endpoint ──────────────────────────────────────────────────

@app.post("/review")
async def manual_review(
    repo: str,
    pr_number: int,
    background_tasks: BackgroundTasks,
):
    """Manually trigger a review for a specific PR.
    
    Usage: POST /review?repo=owner/repo&pr_number=42
    """
    webhook_payload = PRWebhookPayload(
        action="manual",
        pr_number=pr_number,
        repo_full_name=repo,
        head_sha="",
        base_branch="main",
        head_branch="",
        pr_title="Manual review",
        pr_url="",
        sender="manual",
    )
    
    background_tasks.add_task(run_review_pipeline, webhook_payload)
    
    return JSONResponse(
        content={"message": "Manual review triggered", "pr": f"{repo}#{pr_number}"},
        status_code=202,
    )


# ─── Review Pipeline ──────────────────────────────────────────────────────────

async def run_review_pipeline(payload: PRWebhookPayload):
    """Execute the full CodeGuardian review pipeline.
    
    1. Fetch PR diff/files from GitHub
    2. Clone repo to temp dir (for security scanning)
    3. Run the LangGraph supervisor pipeline
    4. Save results to Postgres
    """
    start_time = time.time()
    
    try:
        gh = GitHubClient()
        
        # Fetch changed files
        changed_files = gh.get_pr_files(payload.repo_full_name, payload.pr_number)
        logger.info(f"PR has {len(changed_files)} changed files")
        
        # Get head SHA if not provided (manual trigger)
        head_sha = payload.head_sha
        if not head_sha:
            pr = gh.get_pr(payload.repo_full_name, payload.pr_number)
            head_sha = pr.head.sha
        
        # Clone repo for security scanning
        repo_clone_path = _clone_repo(payload.repo_full_name, head_sha)
        
        # Build the LangGraph review state
        initial_state = {
            "repo_full_name": payload.repo_full_name,
            "pr_number": payload.pr_number,
            "head_sha": head_sha,
            "base_branch": payload.base_branch,
            "head_branch": payload.head_branch,
            "changed_files": changed_files,
            "repo_clone_path": repo_clone_path,
            "security_result": None,
            "quality_result": None,
            "test_gap_result": None,
            "documentation_result": None,
            "all_findings": [],
            "code_health_score": 100.0,
            "review_summary": "",
            "pr_comment": "",
            "auto_fix_branch": None,
            "start_time": start_time,
            "errors": [],
        }
        
        # Run the LangGraph pipeline
        review_graph = compile_review_graph()
        final_state = await review_graph.ainvoke(initial_state)
        
        # Save to database
        elapsed = time.time() - start_time
        _save_review_to_db(payload, final_state, elapsed)
        
        logger.info(
            f"✅ Review complete for {payload.repo_full_name}#{payload.pr_number} "
            f"in {elapsed:.1f}s — {len(final_state.get('all_findings', []))} findings"
        )
        
        # Cleanup cloned repo
        if repo_clone_path and os.path.exists(repo_clone_path):
            import shutil
            shutil.rmtree(repo_clone_path, ignore_errors=True)
        
    except Exception as e:
        logger.error(f"❌ Review pipeline failed: {e}", exc_info=True)
        # Try to post an error comment on the PR
        try:
            gh = GitHubClient()
            gh.post_pr_comment(
                payload.repo_full_name,
                payload.pr_number,
                f"⚠️ **CodeGuardian AI** encountered an error during review:\n\n```\n{str(e)}\n```",
            )
        except Exception:
            pass


# ─── Helper Functions ──────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _clone_repo(repo_full_name: str, sha: str) -> str:
    """Clone a repo to a temporary directory for scanning."""
    clone_dir = tempfile.mkdtemp(prefix="codeguardian_")
    
    # Use token for private repos
    if settings.github_token:
        clone_url = f"https://{settings.github_token}@github.com/{repo_full_name}.git"
    else:
        clone_url = f"https://github.com/{repo_full_name}.git"
    
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, clone_dir],
            capture_output=True, text=True, timeout=120,
            check=True,
        )
        
        # Checkout specific SHA if needed
        if sha:
            subprocess.run(
                ["git", "checkout", sha],
                cwd=clone_dir,
                capture_output=True, text=True, timeout=30,
            )
        
        return clone_dir
    except Exception as e:
        logger.warning(f"Repo clone failed: {e}")
        return clone_dir


def _save_review_to_db(payload: PRWebhookPayload, state: dict, elapsed: float):
    """Save the review results to Postgres."""
    try:
        findings = state.get("all_findings", [])
        
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            sev = f.get("severity", "info")
            if sev in severity_counts:
                severity_counts[sev] += 1
        
        review_data = {
            "repo_full_name": payload.repo_full_name,
            "pr_number": payload.pr_number,
            "commit_sha": payload.head_sha or "unknown",
            "total_findings": len(findings),
            "critical_count": severity_counts["critical"],
            "high_count": severity_counts["high"],
            "medium_count": severity_counts["medium"],
            "low_count": severity_counts["low"],
            "info_count": severity_counts["info"],
            "code_health_score": state.get("code_health_score", 0),
            "review_duration_seconds": elapsed,
            "auto_fix_branch": state.get("auto_fix_branch"),
            "findings_json": json.dumps(findings),
        }
        
        save_review(review_data)
        logger.info("Saved review to database")
    except Exception as e:
        logger.warning(f"Failed to save review to DB: {e}")
