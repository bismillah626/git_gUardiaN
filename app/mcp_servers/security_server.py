"""
MCP Security Server for Git Guardian AI.

Exposes Semgrep, Bandit, Gitleaks, and ESLint as MCP-compatible tools
with structured JSON output. This server can be consumed by LangChain
MCP adapters or called directly by the Security Agent.

NOTE: MCP integration is optional — the Security Agent can also call
the security tools directly via app.services.security_tools.
This server exists for protocol compliance and future extensibility.
"""

import json
import logging
from typing import Any, Dict

from app.services.security_tools import (
    run_bandit,
    run_semgrep,
    run_gitleaks,
    run_eslint,
    run_all_security_tools,
    check_tool_availability,
)

logger = logging.getLogger(__name__)


# ─── MCP Tool Definitions ─────────────────────────────────────────────────────
# These follow the MCP tool schema pattern for discoverability.

MCP_TOOLS = [
    {
        "name": "run_bandit",
        "description": "Run Bandit security scanner on Python files. Returns JSON findings with severity, file, line, and rule_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": "Path to the directory or file to scan",
                }
            },
            "required": ["target_path"],
        },
    },
    {
        "name": "run_semgrep",
        "description": "Run Semgrep static analysis with auto-detected rules. Returns JSON findings with severity, file, line, and rule_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": "Path to the directory or file to scan",
                },
                "rules": {
                    "type": "string",
                    "description": "Semgrep rule config (default: 'auto')",
                    "default": "auto",
                },
            },
            "required": ["target_path"],
        },
    },
    {
        "name": "run_gitleaks",
        "description": "Run Gitleaks to detect hardcoded secrets (API keys, passwords, tokens). Returns JSON findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": "Path to the directory to scan for secrets",
                }
            },
            "required": ["target_path"],
        },
    },
    {
        "name": "run_eslint",
        "description": "Run ESLint on JavaScript/TypeScript files. Returns JSON findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": "Path to the JS/TS file to lint",
                }
            },
            "required": ["target_path"],
        },
    },
    {
        "name": "run_all_security_tools",
        "description": "Run all available security scanners (Bandit, Semgrep, Gitleaks, ESLint) and return consolidated results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_path": {
                    "type": "string",
                    "description": "Path to the directory to scan",
                }
            },
            "required": ["target_path"],
        },
    },
]


# ─── Tool Dispatcher ──────────────────────────────────────────────────────────

def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch an MCP tool call to the appropriate security tool.
    
    Returns a dict with 'result' (the findings) and 'error' (if any).
    """
    tool_map = {
        "run_bandit": lambda args: run_bandit(args["target_path"]),
        "run_semgrep": lambda args: run_semgrep(args["target_path"], args.get("rules", "auto")),
        "run_gitleaks": lambda args: run_gitleaks(args["target_path"]),
        "run_eslint": lambda args: run_eslint(args["target_path"]),
        "run_all_security_tools": lambda args: run_all_security_tools(args["target_path"]),
    }

    handler = tool_map.get(tool_name)
    if not handler:
        return {"result": None, "error": f"Unknown tool: {tool_name}"}

    try:
        result = handler(arguments)
        return {"result": result, "error": None}
    except Exception as e:
        logger.error(f"MCP tool {tool_name} failed: {e}")
        return {"result": None, "error": str(e)}


def list_tools() -> list:
    """Return the list of available MCP tools (for discovery)."""
    return MCP_TOOLS
