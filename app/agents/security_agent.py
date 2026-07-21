"""
Security Agent for Git Guardian AI.

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
Findings have already been deduplicated — if multiple tools flagged the same issue, they
have been merged into a single entry with a `confirmed_by` list showing all tools that agreed.

Your job is to:
1. Explain each finding in plain English — what the vulnerability is and why it matters.
2. Assess whether each finding is a true positive or likely false positive based on context.
3. Assign a severity: critical, high, medium, low, or info.
4. Suggest a fix if possible.
5. If a finding has multiple entries in `confirmed_by`, note that it was confirmed by
   multiple independent tools (this increases confidence it's a true positive).

SEVERITY RULES (you must follow these minimums):
- Hardcoded credentials, API keys, passwords, tokens → CRITICAL (minimum)
- eval(), exec(), os.system() with unsanitized input → HIGH (minimum)
- SQL injection patterns (string concatenation in queries) → HIGH (minimum)
- Command injection patterns → HIGH (minimum)
- Only downgrade from these minimums if you can prove it's a false positive (mark as "info")

CRITICAL RULES:
- You must ONLY triage the findings given to you. Do NOT invent new findings.
- Every finding in your response must reference the original tool and rule_id.
- If you believe a finding is a false positive, mark severity as "info" and explain why.
- Preserve the `confirmed_by` list exactly as provided — do not drop tool entries.

Respond in JSON format as a list of objects:
[
  {
    "source_tool": "bandit|semgrep|gitleaks|eslint",
    "rule_id": "original rule ID",
    "file": "filepath",
    "line": line_number,
    "severity": "critical|high|medium|low|info",
    "message": "Plain English explanation of what's wrong and why it matters",
    "suggested_fix": "How to fix it (or null if unclear)",
    "confirmed_by": [{"tool": "tool_name", "rule": "rule_id"}, ...]
  }
]
"""

# ── Severity floor rules ──────────────────────────────────────────────────────
# These enforce minimum severity for certain categories of findings,
# preventing the LLM from under-ranking dangerous patterns.

# Gitleaks rule IDs that indicate credential/secret exposure → CRITICAL floor
_CREDENTIAL_RULE_IDS = {
    "generic-api-key", "private-key", "aws-access-key-id", "aws-secret-access-key",
    "github-pat", "github-oauth", "slack-token", "stripe-api-key",
    "google-api-key", "heroku-api-key", "mailchimp-api-key",
    "twilio-api-key", "sendgrid-api-key", "npm-access-token",
    "pypi-upload-token", "telegram-bot-api-token", "discord-client-secret",
    "jwt", "password-in-url",
}

# Bandit test IDs for dangerous patterns → HIGH floor
_DANGEROUS_BANDIT_IDS = {
    "B102",  # exec_used
    "B307",  # eval
    "B301",  # pickle
    "B602",  # subprocess_popen_with_shell_equals_true
    "B603",  # subprocess_without_shell_equals_true (command injection)
    "B604",  # any_other_function_with_shell_equals_true
    "B605",  # start_process_with_a_shell
    "B606",  # start_process_with_no_shell
    "B607",  # start_process_with_partial_path
    "B608",  # hardcoded_sql_expressions
    "B609",  # wildcard_injection
    "B610",  # django_extra_used
    "B611",  # django_rawsql_used
    "B105",  # hardcoded_password_string
    "B106",  # hardcoded_password_funcarg
    "B107",  # hardcoded_password_default
}

# Keywords in messages that indicate credential exposure
_CREDENTIAL_KEYWORDS = {"password", "secret", "api_key", "api-key", "apikey", "token", "credential"}


# ── Vulnerability Category Map (for cross-tool deduplication) ─────────────────
# Maps tool-specific rule IDs to shared vulnerability categories.
# When two tools flag the same file/line and their rule IDs map to the same
# category, we merge them into a single finding instead of reporting duplicates.
# Extend this dict as new overlaps are observed — unmapped IDs fall back to
# being their own category, so single-tool findings are never broken.

VULN_CATEGORY_MAP = {
    # Weak/broken hash used for passwords
    "B303": "weak_hash",
    "B324": "weak_hash_password",
    "python.lang.security.audit.md5-used-as-password.md5-used-as-password": "weak_hash_password",
    "python.lang.security.audit.insecure-hash-algorithms.insecure-hash-algorithm-md5": "weak_hash",
    "python.lang.security.audit.insecure-hash-algorithms.insecure-hash-algorithm-sha1": "weak_hash",

    # Shell injection via subprocess shell=True
    "B602": "shell_injection",
    "python.lang.security.audit.subprocess-shell-true.subprocess-shell-true": "shell_injection",

    # Insecure deserialization (pickle)
    "B301": "insecure_deserialization",
    "python.lang.security.deserialization.pickle.avoid-pickle": "insecure_deserialization",
    "python.lang.security.deserialization.avoid-pickle.avoid-pickle": "insecure_deserialization",

    # SQL injection via string concatenation/formatting
    "B608": "sql_injection",
    "python.lang.security.audit.formatted-sql-query.formatted-sql-query": "sql_injection",
    "python.lang.security.audit.string-concat-in-sql-query.string-concat-in-sql-query": "sql_injection",

    # eval / exec usage
    "B307": "eval_usage",
    "B102": "exec_usage",
    "python.lang.security.audit.eval-detected.eval-detected": "eval_usage",
    "python.lang.security.audit.exec-detected.exec-detected": "exec_usage",

    # Hardcoded passwords
    "B105": "hardcoded_password",
    "B106": "hardcoded_password",
    "B107": "hardcoded_password",
    "python.lang.security.audit.hardcoded-password.hardcoded-password": "hardcoded_password",

    # os.system usage
    "B605": "os_system",
    "python.lang.security.audit.dangerous-system-call.dangerous-system-call": "os_system",

    # YAML unsafe load
    "B506": "yaml_unsafe_load",
    "python.lang.security.deserialization.avoid-unsafe-yaml.avoid-unsafe-yaml": "yaml_unsafe_load",

    # Binding to all interfaces
    "B104": "bind_all_interfaces",

    # Insecure TLS/SSL
    "B502": "insecure_ssl",
    "B503": "insecure_ssl",
}

# Severity ranking for merging — lower number = higher severity
_SEVERITY_RANK = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}


def deduplicate_findings(raw_findings: List[Dict]) -> List[Dict]:
    """Deduplicate overlapping findings across security tools.

    Groups findings by (file, line, normalized_vulnerability_category).
    When multiple tools flag the same issue, merges into a single finding that:
    - Keeps the most detailed message
    - Uses the highest (most severe) severity
    - Records all contributing tools in a `confirmed_by` list

    Findings with rule IDs not in VULN_CATEGORY_MAP are left as-is — the
    fallback category is the rule_id itself, so unmapped findings never
    accidentally merge with unrelated findings.
    """
    from collections import defaultdict

    groups: dict[tuple, List[Dict]] = defaultdict(list)

    for finding in raw_findings:
        file_path = finding.get("file", "unknown")
        line = finding.get("line", 0)
        rule_id = finding.get("rule_id", "")
        # Normalize to a vulnerability category; fall back to rule_id itself
        category = VULN_CATEGORY_MAP.get(rule_id, rule_id)
        key = (file_path, line, category)
        groups[key].append(finding)

    deduped: List[Dict] = []

    for (file_path, line, category), group in groups.items():
        if len(group) == 1:
            # Single finding — pass through with confirmed_by for consistency
            f = dict(group[0])  # shallow copy
            f["confirmed_by"] = [{"tool": f.get("tool", ""), "rule": f.get("rule_id", "")}]
            deduped.append(f)
        else:
            # Multiple tools found the same issue — merge
            confirmed_by = [
                {"tool": f.get("tool", ""), "rule": f.get("rule_id", "")}
                for f in group
            ]

            # Pick highest severity
            best_sev = min(
                group,
                key=lambda f: _SEVERITY_RANK.get(f.get("severity", "medium"), 3),
            )
            merged_severity = best_sev.get("severity", "medium")

            # Pick the longest/most detailed message
            best_msg = max(group, key=lambda f: len(f.get("message", "")))
            merged_message = best_msg.get("message", "")

            # Pick the best suggested fix (longest)
            fixes = [f.get("code", "") for f in group if f.get("code")]
            merged_code = max(fixes, key=len) if fixes else ""

            # Use the primary tool (first in the group) as source_tool
            primary = group[0]
            merged = {
                "tool": primary.get("tool", ""),
                "rule_id": primary.get("rule_id", ""),
                "file": file_path,
                "line": line,
                "severity": merged_severity,
                "message": merged_message,
                "code": merged_code,
                "confirmed_by": confirmed_by,
            }
            deduped.append(merged)

            tools_str = ", ".join(f"{c['tool']}({c['rule']})" for c in confirmed_by)
            logger.info(
                f"[SECURITY-AGENT] Deduped: {file_path}:{line} [{category}] — "
                f"merged {len(group)} findings from: {tools_str}"
            )

    logger.info(
        f"[SECURITY-AGENT] Deduplication: {len(raw_findings)} raw → {len(deduped)} unique findings"
    )
    return deduped


async def run_security_agent(
    changed_files: List[Dict],
    repo_clone_path: str,
) -> AgentResult:
    """Execute the Security Agent pipeline.
    
    1. Run static analysis tools on changed files
    2. Send raw findings to LLM for triage/explanation (batched per file)
    3. Validate that every returned finding traces to a tool output
    4. Enforce severity floors for dangerous patterns
    5. Return standardized AgentResult
    """
    start_time = time.time()
    all_tool_findings: List[Dict] = []
    tools_executed: List[str] = []
    tools_skipped: List[str] = []

    try:
        # ── Step 1: Run security tools on changed files ─────────────────
        logger.info(f"[SECURITY-AGENT] Starting security scan on {len(changed_files)} changed files")
        
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
                logger.info("[SECURITY-AGENT] Python files detected — running Bandit + Semgrep")
                bandit_results = run_bandit(tmpdir)
                all_tool_findings.extend(bandit_results)
                tools_executed.append(f"bandit({len(bandit_results)} findings)")

                semgrep_results = run_semgrep(tmpdir)
                all_tool_findings.extend(semgrep_results)
                tools_executed.append(f"semgrep({len(semgrep_results)} findings)")
            else:
                tools_skipped.extend(["bandit (no .py files)", "semgrep (no .py files)"])

            logger.info("[SECURITY-AGENT] Running Gitleaks on all changed files")
            gitleaks_results = run_gitleaks(tmpdir)
            all_tool_findings.extend(gitleaks_results)
            tools_executed.append(f"gitleaks({len(gitleaks_results)} findings)")

            if js_files_exist:
                # Run ESLint on each JS/TS file
                eslint_count = 0
                for file_info in changed_files:
                    fn = file_info.get("filename", "")
                    if fn.endswith((".js", ".jsx", ".ts", ".tsx")):
                        fp = os.path.join(tmpdir, fn)
                        if os.path.exists(fp):
                            eslint_results = run_eslint(fp)
                            all_tool_findings.extend(eslint_results)
                            eslint_count += len(eslint_results)
                tools_executed.append(f"eslint({eslint_count} findings)")
            else:
                tools_skipped.append("eslint (no JS/TS files)")

        logger.info(
            f"[SECURITY-AGENT] Tool execution complete. "
            f"Ran: [{', '.join(tools_executed)}]. "
            f"Skipped: [{', '.join(tools_skipped)}]. "
            f"Total raw findings: {len(all_tool_findings)}"
        )

        # ── Normalize file paths: strip temp dir prefix ─────────────────
        # Tools report absolute paths like /tmp/tmpXXX/services/admin_tools.py
        # We need to normalize to relative paths like services/admin_tools.py
        for finding in all_tool_findings:
            fpath = finding.get("file", "")
            if tmpdir and fpath.startswith(tmpdir):
                finding["file"] = fpath[len(tmpdir):].lstrip("/")

        if not all_tool_findings:
            return AgentResult(
                agent_name="security",
                findings=[],
                summary=f"No security issues found. Tools executed: {', '.join(tools_executed)}.",
                execution_time_seconds=time.time() - start_time,
            )

        # ── Step 1.5: Deduplicate cross-tool overlaps ───────────────────
        # Must happen BEFORE LLM triage so the model doesn't see/explain
        # the same vulnerability twice. Also saves tokens.
        deduped_findings = deduplicate_findings(all_tool_findings)

        # ── Step 2: LLM triage (batched per file to save tokens) ────────
        findings_by_file: Dict[str, List[Dict]] = {}
        for f in deduped_findings:
            fname = f.get("file", "unknown")
            findings_by_file.setdefault(fname, []).append(f)

        triaged_findings: List[Finding] = []

        for filename, file_findings in findings_by_file.items():
            prompt = (
                f"Triage the following {len(file_findings)} security findings for file `{filename}`.\n"
                f"Findings have been deduplicated across tools. If a finding has multiple entries in "
                f"`confirmed_by`, it was independently flagged by multiple scanners — treat this as "
                f"higher confidence that it is a true positive.\n\n"
                f"Raw tool output:\n```json\n{json.dumps(file_findings, indent=2)}\n```\n\n"
                "Respond with a JSON list of triaged findings. Preserve the `confirmed_by` list."
            )

            try:
                response = await llm_provider.invoke(
                    prompt=prompt,
                    system_prompt=TRIAGE_SYSTEM_PROMPT,
                )

                # Parse LLM response, passing file_findings so confirmed_by is preserved
                parsed = _parse_llm_findings(response, file_findings)
                triaged_findings.extend(parsed)

            except Exception as e:
                logger.warning(f"LLM triage failed for {filename}: {e}. Using raw findings.")
                # Fall back to raw findings without LLM explanation
                for raw in file_findings:
                    triaged_findings.append(Finding(
                        source_tool=raw.get("tool", "unknown"),
                        agent="security",
                        file=raw.get("file", filename),
                        line=raw.get("line", 0),
                        severity=_map_severity(raw.get("severity", "medium")),
                        message=raw.get("message", "Security issue detected"),
                        rule_id=raw.get("rule_id"),
                        context=raw.get("code"),
                        confirmed_by=raw.get("confirmed_by"),
                    ))

        # ── Step 3: Validate — reject any finding without tool source ───
        validated = _validate_findings(triaged_findings, all_tool_findings)

        # ── Step 4: Enforce severity floors ─────────────────────────────
        enforced = _enforce_severity_floors(validated)

        elapsed = time.time() - start_time
        summary = (
            f"Security scan complete: {len(enforced)} findings "
            f"({sum(1 for f in enforced if f.severity == Severity.CRITICAL)} critical, "
            f"{sum(1 for f in enforced if f.severity == Severity.HIGH)} high) "
            f"in {elapsed:.1f}s. Tools: {', '.join(tools_executed)}"
        )

        logger.info(f"[SECURITY-AGENT] {summary}")

        return AgentResult(
            agent_name="security",
            findings=enforced,
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
    
    Preserves `confirmed_by` from the deduplicated input. If the LLM
    doesn't return it, we recover it from the original findings via a
    lookup index keyed on (file, line, rule_id).
    
    Falls back to raw findings if parsing fails.
    """
    # Build a lookup index to recover confirmed_by if LLM drops it
    _confirmed_by_index: Dict[tuple, list] = {}
    for f in original_findings:
        key = (f.get("file", ""), f.get("line", 0), f.get("rule_id", ""))
        _confirmed_by_index[key] = f.get("confirmed_by")
        # Also index by (file, line, tool) for looser matching
        tool_key = (f.get("file", ""), f.get("line", 0), f.get("tool", ""))
        _confirmed_by_index[tool_key] = f.get("confirmed_by")

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
            # Try to get confirmed_by from LLM response first, then fall back to index
            confirmed_by = item.get("confirmed_by")
            if not confirmed_by:
                key = (item.get("file", ""), item.get("line", 0), item.get("rule_id", ""))
                confirmed_by = _confirmed_by_index.get(key)
            if not confirmed_by:
                tool_key = (item.get("file", ""), item.get("line", 0), item.get("source_tool", ""))
                confirmed_by = _confirmed_by_index.get(tool_key)

            findings.append(Finding(
                source_tool=item.get("source_tool", "unknown"),
                agent="security",
                file=item.get("file", ""),
                line=item.get("line", 0),
                severity=_map_severity(item.get("severity", "medium")),
                message=item.get("message", ""),
                suggested_fix=item.get("suggested_fix"),
                rule_id=item.get("rule_id"),
                confirmed_by=confirmed_by,
            ))
        return findings

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse LLM triage response: {e}")
        # Return raw findings as fallback
        return [
            Finding(
                source_tool=f.get("tool", "unknown"),
                agent="security",
                file=f.get("file", ""),
                line=f.get("line", 0),
                severity=_map_severity(f.get("severity", "medium")),
                message=f.get("message", ""),
                rule_id=f.get("rule_id"),
                context=f.get("code"),
                confirmed_by=f.get("confirmed_by"),
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
                f"[SECURITY-AGENT] Rejected hallucinated finding: {finding.source_tool}/{finding.rule_id} "
                f"in {finding.file} — no matching tool output"
            )

    return validated


def _enforce_severity_floors(findings: List[Finding]) -> List[Finding]:
    """Enforce minimum severity levels for dangerous pattern categories.
    
    Prevents the LLM from under-ranking critical security issues.
    - Credential/secret exposure (gitleaks) → CRITICAL minimum
    - Dangerous function usage (eval, exec, os.system) → HIGH minimum
    - SQL injection patterns → HIGH minimum
    - Hardcoded passwords (bandit B105/B106/B107) → HIGH minimum
    """
    severity_rank = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }
    
    enforced = []
    for finding in findings:
        original_severity = finding.severity
        floor = None
        
        # Rule 1: Gitleaks findings (secrets) → CRITICAL floor
        if finding.source_tool == "gitleaks":
            floor = Severity.CRITICAL
        
        # Rule 2: Known credential-related rule IDs → CRITICAL floor
        if finding.rule_id and finding.rule_id.lower() in _CREDENTIAL_RULE_IDS:
            floor = Severity.CRITICAL
        
        # Rule 3: Credential keywords in message → HIGH floor (minimum)
        if any(kw in (finding.message or "").lower() for kw in _CREDENTIAL_KEYWORDS):
            if floor is None or severity_rank.get(floor, 99) > severity_rank[Severity.HIGH]:
                floor = Severity.HIGH
        
        # Rule 4: Dangerous Bandit rule IDs → HIGH floor
        if finding.rule_id and finding.rule_id.upper() in _DANGEROUS_BANDIT_IDS:
            if floor is None or severity_rank.get(floor, 99) > severity_rank[Severity.HIGH]:
                floor = Severity.HIGH
        
        # Rule 5: Hardcoded password bandit rules → CRITICAL floor
        if finding.rule_id and finding.rule_id.upper() in {"B105", "B106", "B107"}:
            floor = Severity.CRITICAL
        
        # Apply the floor (only escalate, never downgrade)
        if floor is not None:
            if severity_rank.get(finding.severity, 99) > severity_rank[floor]:
                finding = finding.model_copy(update={"severity": floor})
                logger.info(
                    f"[SECURITY-AGENT] Severity escalated: {finding.source_tool}/{finding.rule_id} "
                    f"{original_severity.value} → {floor.value}"
                )
        
        enforced.append(finding)
    
    return enforced


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
