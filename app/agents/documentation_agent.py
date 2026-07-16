"""
Documentation Agent for Git Guardian AI.

Flags missing/outdated docstrings and drafts replacements.
"""

import json
import logging
import time
from typing import List, Dict

from app.core.llm_provider import llm_provider
from app.core.diff_utils import extract_changed_lines_only, classify_file_type
from app.models.schemas import Finding, AgentResult, Severity

logger = logging.getLogger(__name__)

DOC_SYSTEM_PROMPT = """You are a documentation reviewer for a code project.
Your job:
1. Check if new/modified functions, classes, and modules have proper docstrings.
2. Flag missing or outdated documentation.
3. Draft replacement docstrings using Google style for Python, JSDoc for JS/TS.

Rules:
- Focus only on changed lines (+ lines in the diff).
- Don't flag trivial functions (getters, simple assignments).
- Draft complete docstrings including params, returns, raises where applicable.

CRITICAL OUTPUT FORMAT RULES:
- Respond with ONLY a valid JSON array. No markdown code fences, no commentary before or after.
- The "suggested_fix" value must be a single JSON string with all newlines encoded as \\n (not literal line breaks).
- Escape every double-quote inside "suggested_fix" as \\".
- Escape every backslash inside "suggested_fix" as \\\\ (e.g. a literal backslash must appear as two characters: \\\\).
- Do not include any character in "suggested_fix" that would break JSON string parsing.
- If you are unsure whether something is escaped correctly, prefer a simpler one-line docstring over a complex one.

Respond in this exact JSON format:
[{"file":"path","line":0,"severity":"low|info","message":"what's missing","suggested_fix":"the complete docstring to add, properly escaped"}]

If no issues: []
"""

async def run_documentation_agent(changed_files: List[Dict]) -> AgentResult:
    start_time = time.time()
    try:
        code_files = []
        for fi in changed_files:
            fn = fi.get("filename", "")
            patch = fi.get("patch", "")
            if not patch or fi.get("status") == "removed":
                continue
            ft = classify_file_type(fn)
            if ft in ("other", "config"):
                continue
            code_files.append(fi)

        if not code_files:
            return AgentResult(agent_name="documentation", findings=[], summary="No code files to review for docs.", execution_time_seconds=time.time() - start_time)

        combined_diff = ""
        filenames = []
        for fi in code_files:
            changed = extract_changed_lines_only(fi.get("patch", ""))
            if changed.strip():
                combined_diff += f"\n--- File: {fi['filename']} ---\n{changed}\n"
                filenames.append(fi["filename"])

        if not combined_diff.strip():
            return AgentResult(agent_name="documentation", findings=[], summary="No significant changes.", execution_time_seconds=time.time() - start_time)

        prompt = f"Review for documentation gaps.\n\n```diff\n{combined_diff}\n```\nFiles: {', '.join(filenames)}"

        try:
            response = await llm_provider.invoke(prompt=prompt, system_prompt=DOC_SYSTEM_PROMPT)
            findings = _parse_findings(response)
        except Exception as e:
            logger.warning(f"Doc review failed: {e}")
            findings = []

        elapsed = time.time() - start_time
        return AgentResult(agent_name="documentation", findings=findings, summary=f"Doc review: {len(findings)} issues in {elapsed:.1f}s", execution_time_seconds=elapsed)

    except Exception as e:
        logger.error(f"Documentation agent failed: {e}", exc_info=True)
        return AgentResult(agent_name="documentation", findings=[], error=str(e), execution_time_seconds=time.time() - start_time)


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
                source_tool="doc_review", agent="documentation",
                file=i.get("file", ""), line=i.get("line", 0),
                severity=Severity.LOW if i.get("severity") == "low" else Severity.INFO,
                message=i.get("message", ""), suggested_fix=i.get("suggested_fix"),
            ) for i in parsed
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Parse doc response failed: {e}")
        return []
