"""
Test-Gap Agent for Git Guardian AI.

Identifies untested functions/branches in the changed code
and drafts starter unit tests.
"""

import json
import logging
import time
from typing import List, Dict

from app.core.llm_provider import llm_provider
from app.core.diff_utils import extract_changed_lines_only, classify_file_type
from app.models.schemas import Finding, AgentResult, Severity

logger = logging.getLogger(__name__)

TEST_GAP_SYSTEM_PROMPT = """You are a senior QA engineer analyzing code changes for test coverage gaps.

Your job:
1. Identify NEW or MODIFIED functions in the diff that lack tests.
2. Explain WHY each needs tests.
3. Draft a starter unit test for each gap.

Rules:
- Focus only on changed lines (lines starting with +).
- Be pragmatic: not every 1-line change needs a test.
- Use pytest for Python, jest for JS/TS.

Respond in JSON:
[{"file":"path","line":0,"severity":"medium|low","message":"description","suggested_fix":"test code"}]

If no gaps: []
"""


async def run_test_gap_agent(changed_files: List[Dict]) -> AgentResult:
    start_time = time.time()
    try:
        code_files = []
        for fi in changed_files:
            fn = fi.get("filename", "")
            patch = fi.get("patch", "")
            if not patch or fi.get("status") == "removed":
                continue
            ft = classify_file_type(fn)
            if ft in ("other", "config", "documentation"):
                continue
            if "test" in fn.lower() or "spec" in fn.lower():
                continue
            code_files.append(fi)

        if not code_files:
            return AgentResult(agent_name="test_gap", findings=[], summary="No testable code changes.", execution_time_seconds=time.time() - start_time)

        combined_diff = ""
        filenames = []
        for fi in code_files:
            changed = extract_changed_lines_only(fi.get("patch", ""))
            if changed.strip():
                combined_diff += f"\n--- File: {fi['filename']} ---\n{changed}\n"
                filenames.append(fi["filename"])

        if not combined_diff.strip():
            return AgentResult(agent_name="test_gap", findings=[], summary="No significant changes.", execution_time_seconds=time.time() - start_time)

        prompt = f"Analyze for test gaps.\n\n```diff\n{combined_diff}\n```\nFiles: {', '.join(filenames)}"

        try:
            response = await llm_provider.invoke(prompt=prompt, system_prompt=TEST_GAP_SYSTEM_PROMPT)
            findings = _parse_findings(response)
        except Exception as e:
            logger.warning(f"Test gap analysis failed: {e}")
            findings = []

        elapsed = time.time() - start_time
        return AgentResult(agent_name="test_gap", findings=findings, summary=f"Test gap analysis: {len(findings)} gaps in {elapsed:.1f}s", execution_time_seconds=elapsed)

    except Exception as e:
        logger.error(f"Test-gap agent failed: {e}", exc_info=True)
        return AgentResult(agent_name="test_gap", findings=[], error=str(e), execution_time_seconds=time.time() - start_time)


def _parse_findings(resp: str) -> List[Finding]:
    try:
        text = resp.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            parsed = [parsed]
        return [
            Finding(
                source_tool="test_gap_analysis", agent="test_gap",
                file=i.get("file", ""), line=i.get("line", 0),
                severity=Severity.MEDIUM if i.get("severity") == "medium" else Severity.LOW,
                message=i.get("message", ""), suggested_fix=i.get("suggested_fix"),
            ) for i in parsed
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Parse test gap response failed: {e}")
        return []
