"""
Security Agent for CodeGuardian AI.

Runs Semgrep/Bandit/Gitleaks on changed files, then uses the LLM
ONLY to triage and explain findings — never to invent new ones.

Key constraint: Every finding must trace back to a tool output.
A validation step rejects any finding without a source_tool reference.
"""

import json
import logging
import os
import tempfile
import time
from typing import List, Dict, Optional

from app.core.config import settings
from app.core.llm_provider import llm_provider
from app.models.schemas import Finding, AgentResult, Severity
from app.services.security_tools import run_bandit, run_semgrep, run_gitleaks, run_eslint

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """You are a senior security engineer triaging static analysis findings.
You will be given a list of findings from security tools (Bandit, Semgrep, Gitleaks, ESLint).

Your job is to:
1. Explain each finding in plain English — what the vulnerability is and why it matters.
2. Assess whether each finding is a true positive or likely false positive based on context.
3. Assign a severity: critical, high, medium, low, or info.
4. Suggest a fix if possible.

CRITICAL RULES:
- You must ONLY triage the findings given to you. Do NOT invent new findings.
- Every finding in your response must reference the original tool and rule_id.
- If you believe a finding is a false positive, mark severity as "info" and explain why.

Respond in JSON format as a list of objects:
[
  {
    "source_tool": "bandit|semgrep|gitleaks|eslint",
    "rule_id": "original rule ID",
    "file": "filepath",
    "line": line_number,
    "severity": "critical|high|medium|low|info",
    "message": "Plain English explanation of what's wrong and why it matters",
    "suggested_fix": "How to fix it (or null if unclear)"
  }
]
"""


async def run_security_agent(
    changed_files: List[Dict],
    repo_clone_path: str,
) -> AgentResult:
    """Execute the Security Agent pipeline.
    
    1. Run static analysis tools on changed files
    2. Send raw findings to LLM for triage/explanation (batched per file)
    3. Validate that every returned finding traces to a tool output
    4. Return standardized AgentResult
    """
    start_time = time.time()
    all_tool_findings: List[Dict] = []

    try:
        # ── Step 1: Run security tools on changed files ─────────────────
        with tempfile.TemporaryDirectory() as tmpdir:
            python_files_exist = False
            js_files_exist = False

            for file_info in changed_files:
                filename = file_info.get("filename", "")
                if file_info.get("status") == "removed":
                    continue

                source_path = os.path.join(repo_clone_path, filename)
                if not os.path.exists(source_path):
                    continue

                target_path = os.path.join(tmpdir, filename)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)

                with open(source_path, "r", errors="ignore") as src:
                    content = src.read()
                with open(target_path, "w") as dst:
                    dst.write(content)

                if filename.endswith(".py"):
                    python_files_exist = True
                elif filename.endswith((".js", ".jsx", ".ts", ".tsx")):
                    js_files_exist = True

            # Run appropriate tools
            if python_files_exist:
                bandit_results = run_bandit(tmpdir)
                all_tool_findings.extend(bandit_results)

                semgrep_results = run_semgrep(tmpdir)
                all_tool_findings.extend(semgrep_results)

            gitleaks_results = run_gitleaks(tmpdir)
            all_tool_findings.extend(gitleaks_results)

            if js_files_exist:
                # Run ESLint on each JS/TS file
                for file_info in changed_files:
                    fn = file_info.get("filename", "")
                    if fn.endswith((".js", ".jsx", ".ts", ".tsx")):
                        fp = os.path.join(tmpdir, fn)
                        if os.path.exists(fp):
                            all_tool_findings.extend(run_eslint(fp))

        if not all_tool_findings:
            return AgentResult(
                agent_name="security",
                findings=[],
                summary="No security issues found in changed files.",
                execution_time_seconds=time.time() - start_time,
            )

        # ── Step 2: LLM triage (batched per file to save tokens) ────────
        findings_by_file: Dict[str, List[Dict]] = {}
        for f in all_tool_findings:
            fname = f.get("file", "unknown")
            findings_by_file.setdefault(fname, []).append(f)

        triaged_findings: List[Finding] = []

        for filename, file_findings in findings_by_file.items():
            prompt = (
                f"Triage the following {len(file_findings)} security findings for file `{filename}`.\n\n"
                f"Raw tool output:\n```json\n{json.dumps(file_findings, indent=2)}\n```\n\n"
                "Respond with a JSON list of triaged findings."
            )

            try:
                response = await llm_provider.invoke(
                    prompt=prompt,
                    system_prompt=TRIAGE_SYSTEM_PROMPT,
                )

                # Parse LLM response
                parsed = _parse_llm_findings(response, file_findings)
                triaged_findings.extend(parsed)

            except Exception as e:
                logger.warning(f"LLM triage failed for {filename}: {e}. Using raw findings.")
                # Fall back to raw findings without LLM explanation
                for raw in file_findings:
                    triaged_findings.append(Finding(
                        source_tool=raw["tool"],
                        agent="security",
                        file=raw.get("file", filename),
                        line=raw.get("line", 0),
                        severity=_map_severity(raw.get("severity", "medium")),
                        message=raw.get("message", "Security issue detected"),
                        rule_id=raw.get("rule_id"),
                        context=raw.get("code"),
                    ))

        # ── Step 3: Validate — reject any finding without tool source ───
        validated = _validate_findings(triaged_findings, all_tool_findings)

        elapsed = time.time() - start_time
        summary = (
            f"Security scan complete: {len(validated)} findings "
            f"({sum(1 for f in validated if f.severity == Severity.CRITICAL)} critical, "
            f"{sum(1 for f in validated if f.severity == Severity.HIGH)} high) "
            f"in {elapsed:.1f}s"
        )

        return AgentResult(
            agent_name="security",
            findings=validated,
            summary=summary,
            execution_time_seconds=elapsed,
        )

    except Exception as e:
        logger.error(f"Security agent failed: {e}", exc_info=True)
        return AgentResult(
            agent_name="security",
            findings=[],
            summary="",
            error=str(e),
            execution_time_seconds=time.time() - start_time,
        )


def _parse_llm_findings(
    llm_response: str,
    original_findings: List[Dict],
) -> List[Finding]:
    """Parse the LLM's triaged JSON response into Finding objects.
    
    Falls back to raw findings if parsing fails.
    """
    try:
        # Extract JSON from response (handle markdown code blocks)
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
                source_tool=item.get("source_tool", "unknown"),
                agent="security",
                file=item.get("file", ""),
                line=item.get("line", 0),
                severity=_map_severity(item.get("severity", "medium")),
                message=item.get("message", ""),
                suggested_fix=item.get("suggested_fix"),
                rule_id=item.get("rule_id"),
            ))
        return findings

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse LLM triage response: {e}")
        # Return raw findings as fallback
        return [
            Finding(
                source_tool=f["tool"],
                agent="security",
                file=f.get("file", ""),
                line=f.get("line", 0),
                severity=_map_severity(f.get("severity", "medium")),
                message=f.get("message", ""),
                rule_id=f.get("rule_id"),
                context=f.get("code"),
            )
            for f in original_findings
        ]


def _validate_findings(
    triaged: List[Finding],
    original_tool_output: List[Dict],
) -> List[Finding]:
    """Validate that every finding traces back to actual tool output.
    
    This is the hallucination guardrail: reject any finding the LLM
    might have invented without a corresponding tool output.
    """
    # Build a set of (tool, rule_id, file) tuples from original output
    valid_sources = set()
    for raw in original_tool_output:
        key = (
            raw.get("tool", ""),
            raw.get("rule_id", ""),
            os.path.basename(raw.get("file", "")),
        )
        valid_sources.add(key)
        # Also add just tool+file for less strict matching
        valid_sources.add((raw.get("tool", ""), "", os.path.basename(raw.get("file", ""))))

    validated = []
    for finding in triaged:
        key = (
            finding.source_tool,
            finding.rule_id or "",
            os.path.basename(finding.file),
        )
        loose_key = (finding.source_tool, "", os.path.basename(finding.file))

        if key in valid_sources or loose_key in valid_sources:
            validated.append(finding)
        else:
            logger.warning(
                f"Rejected hallucinated finding: {finding.source_tool}/{finding.rule_id} "
                f"in {finding.file} — no matching tool output"
            )

    return validated


def _map_severity(sev_str: str) -> Severity:
    """Map various severity strings to our Severity enum."""
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
        "warning": Severity.MEDIUM,
        "error": Severity.HIGH,
    }
    return mapping.get(sev_str.lower(), Severity.MEDIUM)
