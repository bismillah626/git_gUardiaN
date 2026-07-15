"""
Security tools runner for Git Guardian AI.

Runs Bandit, Semgrep, and Gitleaks on target code and returns structured JSON output.
This module is used by the Security Agent (and exposed via the MCP security server).

Key constraint: The LLM never invents findings — every finding traces back to
actual tool output from this module.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def check_tool_availability() -> Dict[str, bool]:
    """Check which security tools are available on PATH.
    
    Call this at startup to log tool availability diagnostics.
    """
    tools = {
        "bandit": shutil.which("bandit") is not None,
        "semgrep": shutil.which("semgrep") is not None,
        "gitleaks": shutil.which("gitleaks") is not None,
        "eslint": shutil.which("eslint") is not None,
    }
    for name, available in tools.items():
        status = "✅ AVAILABLE" if available else "❌ NOT FOUND"
        path = shutil.which(name) or "N/A"
        logger.info(f"[SECURITY-TOOLS] {name}: {status} (path: {path})")
    return tools


def run_bandit(target_path: str) -> List[Dict]:
    """Run Bandit security scanner on Python files.
    
    Returns list of findings in standardized format:
        {
            "tool": "bandit",
            "file": str,
            "line": int,
            "severity": str,
            "confidence": str,
            "rule_id": str,
            "message": str,
            "code": str,
        }
    """
    logger.info(f"[SECURITY-TOOLS] 🔍 BANDIT EXECUTION STARTED on: {target_path}")
    try:
        result = subprocess.run(
            [
                "bandit",
                "-r", target_path,
                "-f", "json",
                "-ll",  # Low and above
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        # Bandit returns exit code 1 when findings exist (not an error)
        if result.stderr:
            logger.warning(f"[SECURITY-TOOLS] Bandit stderr: {result.stderr[:500]}")
        
        output = result.stdout
        if not output:
            logger.info("[SECURITY-TOOLS] ✅ BANDIT COMPLETED — no findings")
            return []
        
        data = json.loads(output)
        findings = []
        
        for issue in data.get("results", []):
            findings.append({
                "tool": "bandit",
                "file": issue.get("filename", ""),
                "line": issue.get("line_number", 0),
                "severity": issue.get("issue_severity", "MEDIUM").lower(),
                "confidence": issue.get("issue_confidence", "MEDIUM").lower(),
                "rule_id": issue.get("test_id", ""),
                "message": issue.get("issue_text", ""),
                "code": issue.get("code", ""),
            })
        
        logger.info(f"[SECURITY-TOOLS] ✅ BANDIT COMPLETED — {len(findings)} findings")
        return findings
        
    except FileNotFoundError:
        logger.error("[SECURITY-TOOLS] ❌ BANDIT NOT INSTALLED — pip install bandit")
        return []
    except subprocess.TimeoutExpired:
        logger.error("[SECURITY-TOOLS] ❌ BANDIT TIMED OUT")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"[SECURITY-TOOLS] ❌ BANDIT JSON PARSE FAILED: {e}")
        return []
    except Exception as e:
        logger.error(f"[SECURITY-TOOLS] ❌ BANDIT ERROR: {e}")
        return []


def run_semgrep(target_path: str, rules: str = "auto") -> List[Dict]:
    """Run Semgrep security/quality scanner.
    
    Returns list of findings in standardized format.
    """
    logger.info(f"[SECURITY-TOOLS] 🔍 SEMGREP EXECUTION STARTED on: {target_path} (rules={rules})")
    try:
        cmd = [
            "semgrep",
            "--config", rules,
            "--json",
            "--quiet",
            target_path,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        
        if result.stderr:
            logger.warning(f"[SECURITY-TOOLS] Semgrep stderr: {result.stderr[:500]}")
        
        output = result.stdout
        if not output:
            logger.info("[SECURITY-TOOLS] ✅ SEMGREP COMPLETED — no findings")
            return []
        
        data = json.loads(output)
        findings = []
        
        for match in data.get("results", []):
            severity_map = {
                "ERROR": "high",
                "WARNING": "medium",
                "INFO": "low",
            }
            
            findings.append({
                "tool": "semgrep",
                "file": match.get("path", ""),
                "line": match.get("start", {}).get("line", 0),
                "severity": severity_map.get(
                    match.get("extra", {}).get("severity", "WARNING"), "medium"
                ),
                "rule_id": match.get("check_id", ""),
                "message": match.get("extra", {}).get("message", ""),
                "code": match.get("extra", {}).get("lines", ""),
            })
        
        logger.info(f"[SECURITY-TOOLS] ✅ SEMGREP COMPLETED — {len(findings)} findings")
        return findings
        
    except FileNotFoundError:
        logger.error("[SECURITY-TOOLS] ❌ SEMGREP NOT INSTALLED — pip install semgrep")
        return []
    except subprocess.TimeoutExpired:
        logger.error("[SECURITY-TOOLS] ❌ SEMGREP TIMED OUT")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"[SECURITY-TOOLS] ❌ SEMGREP JSON PARSE FAILED: {e}")
        return []
    except Exception as e:
        logger.error(f"[SECURITY-TOOLS] ❌ SEMGREP ERROR: {e}")
        return []


def run_gitleaks(target_path: str) -> List[Dict]:
    """Run Gitleaks to detect hardcoded secrets.
    
    Falls back gracefully if Gitleaks is not installed.
    """
    logger.info(f"[SECURITY-TOOLS] 🔍 GITLEAKS EXECUTION STARTED on: {target_path}")
    try:
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--source", target_path,
                "--report-format", "json",
                "--report-path", "/dev/stdout",
                "--no-git",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.stderr:
            logger.info(f"[SECURITY-TOOLS] Gitleaks stderr: {result.stderr[:500]}")
        
        output = result.stdout
        if not output or output.strip() == "null":
            logger.info("[SECURITY-TOOLS] ✅ GITLEAKS COMPLETED — no secrets found")
            return []
        
        data = json.loads(output)
        if not isinstance(data, list):
            logger.info("[SECURITY-TOOLS] ✅ GITLEAKS COMPLETED — no secrets found")
            return []
        
        findings = []
        for leak in data:
            findings.append({
                "tool": "gitleaks",
                "file": leak.get("File", ""),
                "line": leak.get("StartLine", 0),
                "severity": "critical",  # Leaked secrets are always critical
                "rule_id": leak.get("RuleID", ""),
                "message": f"Potential secret detected: {leak.get('Description', 'Unknown')}",
                "code": leak.get("Match", "")[:200],  # Truncate to avoid exposing full secret
            })
        
        logger.info(f"[SECURITY-TOOLS] ✅ GITLEAKS COMPLETED — {len(findings)} secrets detected")
        return findings
        
    except FileNotFoundError:
        logger.error("[SECURITY-TOOLS] ❌ GITLEAKS NOT INSTALLED — skipping secret detection")
        return []
    except subprocess.TimeoutExpired:
        logger.error("[SECURITY-TOOLS] ❌ GITLEAKS TIMED OUT")
        return []
    except Exception as e:
        logger.error(f"[SECURITY-TOOLS] ❌ GITLEAKS ERROR: {e}")
        return []


def run_eslint(target_path: str) -> List[Dict]:
    """Run ESLint on JavaScript/TypeScript files.
    
    Returns list of findings in standardized format.
    """
    try:
        result = subprocess.run(
            [
                "eslint",
                target_path,
                "--format", "json",
                "--no-eslintrc",
                "--rule", '{"no-eval": "error", "no-implied-eval": "error", "no-new-func": "error"}',
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        output = result.stdout
        if not output:
            return []
        
        data = json.loads(output)
        findings = []
        
        for file_result in data:
            filepath = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                severity_map = {1: "low", 2: "medium"}
                findings.append({
                    "tool": "eslint",
                    "file": filepath,
                    "line": msg.get("line", 0),
                    "severity": severity_map.get(msg.get("severity", 1), "low"),
                    "rule_id": msg.get("ruleId", ""),
                    "message": msg.get("message", ""),
                    "code": "",
                })
        
        logger.info(f"[SECURITY-TOOLS] ✅ ESLINT COMPLETED — {len(findings)} findings")
        return findings
        
    except FileNotFoundError:
        logger.warning("[SECURITY-TOOLS] ❌ ESLINT NOT INSTALLED — skipping JS/TS linting")
        return []
    except Exception as e:
        logger.error(f"[SECURITY-TOOLS] ❌ ESLINT ERROR: {e}")
        return []


def run_all_security_tools(target_path: str) -> Dict[str, List[Dict]]:
    """Run all available security tools and return consolidated results.
    
    Returns dict keyed by tool name, each containing a list of findings.
    """
    results = {
        "bandit": run_bandit(target_path),
        "semgrep": run_semgrep(target_path),
        "gitleaks": run_gitleaks(target_path),
        "eslint": run_eslint(target_path),
    }
    
    total = sum(len(v) for v in results.values())
    logger.info(f"All security tools found {total} total issues in {target_path}")
    
    return results


def scan_diff_files(
    file_patches: List[Dict],
    repo_clone_path: str,
) -> Dict[str, List[Dict]]:
    """Scan only the files that changed in a PR diff.
    
    Writes changed file contents to a temp directory and runs tools on them.
    This avoids scanning the entire repo (cost/time savings).
    """
    all_findings: Dict[str, List[Dict]] = {
        "bandit": [],
        "semgrep": [],
        "gitleaks": [],
        "eslint": [],
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write changed files to temp dir for scanning
        python_files = []
        js_files = []
        
        for file_info in file_patches:
            filename = file_info.get("filename", "")
            if file_info.get("status") == "removed":
                continue
                
            # Get file content from the repo clone path
            source_path = os.path.join(repo_clone_path, filename)
            if not os.path.exists(source_path):
                continue
            
            target_path = os.path.join(tmpdir, filename)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            # Copy file
            with open(source_path, "r", errors="ignore") as src:
                content = src.read()
            with open(target_path, "w") as dst:
                dst.write(content)
            
            if filename.endswith(".py"):
                python_files.append(target_path)
            elif filename.endswith((".js", ".jsx", ".ts", ".tsx")):
                js_files.append(target_path)
        
        # Run tools on the temp directory
        if python_files:
            all_findings["bandit"] = run_bandit(tmpdir)
            all_findings["semgrep"] = run_semgrep(tmpdir)
        
        all_findings["gitleaks"] = run_gitleaks(tmpdir)
        
        if js_files:
            for js_file in js_files:
                all_findings["eslint"].extend(run_eslint(js_file))
    
    return all_findings
