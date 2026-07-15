"""
Quality Agent for Git Guardian AI.

Checks code style, complexity, duplicate logic, and naming conventions.
Grounds all feedback via RAG over the team's coding-standards document,
citing which specific standard was violated.
"""

import json
import logging
import time
from typing import List, Dict

from app.core.config import settings
from app.core.llm_provider import llm_provider
from app.core.diff_utils import extract_changed_lines_only, classify_file_type
from app.models.schemas import Finding, AgentResult, Severity
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

QUALITY_SYSTEM_PROMPT = """You are a senior code reviewer checking code quality.
You have access to the team's coding standards (provided as context below).

Your job is to:
1. Check the code diff for style violations, complexity issues, duplicate logic, and poor naming.
2. For EACH issue found, cite the specific coding standard that was violated.
3. Suggest concrete fixes.

CRITICAL RULES:
- Ground every finding in a specific coding standard from the provided context.
- Focus only on the CHANGED lines (lines starting with + in the diff).
- Be pragmatic: only flag real quality issues, not trivial style nitpicks.
- Do NOT invent coding standards — only cite ones from the provided context.

Respond in JSON format:
[
  {
    "file": "filepath",
    "line": line_number,
    "severity": "medium|low|info",
    "message": "What's wrong and why, citing the specific standard",
    "suggested_fix": "How to fix it",
    "standard_citation": "The exact standard that was violated (quoted from context)"
  }
]

If no quality issues found, respond with an empty list: []
"""


async def run_quality_agent(
    changed_files: List[Dict],
) -> AgentResult:
    """Execute the Quality Agent pipeline.
    
    1. Initialize RAG with coding standards (if not already done)
    2. For each changed file, retrieve relevant standards
    3. Send changed lines + retrieved standards to LLM
    4. Return standardized AgentResult with cited standards
    """
    start_time = time.time()

    try:
        # ── Step 1: Ensure coding standards are indexed ─────────────────
        rag_service.index_coding_standards()

        all_findings: List[Finding] = []

        # ── Step 2: Process each changed file ──────────────────────────
        # Batch files to reduce API calls
        file_batches = _batch_files(changed_files, max_batch_size=3)

        for batch in file_batches:
            batch_diff = ""
            batch_filenames = []

            for file_info in batch:
                filename = file_info.get("filename", "")
                patch = file_info.get("patch", "")
                if not patch or file_info.get("status") == "removed":
                    continue

                file_type = classify_file_type(filename)
                if file_type in ("other", "config"):
                    continue  # Skip config/binary files for quality review

                changed_only = extract_changed_lines_only(patch)
                if not changed_only.strip():
                    continue

                batch_diff += f"\n--- File: {filename} ---\n{changed_only}\n"
                batch_filenames.append(filename)

            if not batch_diff.strip():
                continue

            # ── Step 3: Retrieve relevant standards ─────────────────────
            # Query standards based on the type of code being reviewed
            query = f"code quality standards for {', '.join(batch_filenames)}"
            relevant_standards = rag_service.query_standards(query, n_results=3)

            standards_context = "\n\n".join(
                [s["document"] for s in relevant_standards]
            ) if relevant_standards else "No specific standards found. Apply general best practices."

            # ── Step 4: LLM review ──────────────────────────────────────
            prompt = (
                f"Review the following code changes for quality issues.\n\n"
                f"## Team Coding Standards (cite these in your findings):\n"
                f"{standards_context}\n\n"
                f"## Code Diff:\n```diff\n{batch_diff}\n```\n\n"
                f"Files being reviewed: {', '.join(batch_filenames)}\n"
                f"Respond with a JSON list of quality findings."
            )

            try:
                response = await llm_provider.invoke(
                    prompt=prompt,
                    system_prompt=QUALITY_SYSTEM_PROMPT,
                )
                parsed = _parse_quality_findings(response)
                all_findings.extend(parsed)

            except Exception as e:
                logger.warning(f"Quality review failed for batch: {e}")

        elapsed = time.time() - start_time
        summary = (
            f"Quality review complete: {len(all_findings)} issues found in {elapsed:.1f}s"
        )

        return AgentResult(
            agent_name="quality",
            findings=all_findings,
            summary=summary,
            execution_time_seconds=elapsed,
        )

    except Exception as e:
        logger.error(f"Quality agent failed: {e}", exc_info=True)
        return AgentResult(
            agent_name="quality",
            findings=[],
            summary="",
            error=str(e),
            execution_time_seconds=time.time() - start_time,
        )


def _parse_quality_findings(llm_response: str) -> List[Finding]:
    """Parse LLM quality review response into Finding objects."""
    try:
        text = llm_response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            parsed = [parsed]

        findings = []
        for item in parsed:
            findings.append(Finding(
                source_tool="quality_review",
                agent="quality",
                file=item.get("file", ""),
                line=item.get("line", 0),
                severity=_map_quality_severity(item.get("severity", "low")),
                message=item.get("message", ""),
                suggested_fix=item.get("suggested_fix"),
                standard_citation=item.get("standard_citation"),
            ))
        return findings

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse quality review response: {e}")
        return []


def _map_quality_severity(sev_str: str) -> Severity:
    """Map quality severity strings."""
    mapping = {
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }
    return mapping.get(sev_str.lower(), Severity.LOW)


def _batch_files(files: List[Dict], max_batch_size: int = 3) -> List[List[Dict]]:
    """Group files into batches to reduce the number of LLM calls."""
    batches = []
    current_batch = []
    for f in files:
        current_batch.append(f)
        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)
    return batches
