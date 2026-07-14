"""
Tests for CodeGuardian AI core utilities.
"""

import pytest
from app.core.diff_utils import (
    parse_unified_diff,
    chunk_diff_for_llm,
    classify_file_type,
    extract_changed_lines_only,
)
from app.models.schemas import Finding, AgentResult, Severity, PRWebhookPayload


# ─── Diff Utils Tests ─────────────────────────────────────────────────────────

class TestClassifyFileType:
    def test_python(self):
        assert classify_file_type("app/main.py") == "python"
        assert classify_file_type("utils.pyi") == "python"

    def test_javascript(self):
        assert classify_file_type("index.js") == "javascript"
        assert classify_file_type("App.tsx") == "javascript"

    def test_config(self):
        assert classify_file_type("config.yaml") == "config"
        assert classify_file_type("package.json") == "config"
        assert classify_file_type("Dockerfile") == "config"

    def test_documentation(self):
        assert classify_file_type("README.md") == "documentation"
        assert classify_file_type("CHANGELOG.rst") == "documentation"

    def test_other(self):
        assert classify_file_type("image.png") == "other"
        assert classify_file_type("binary.so") == "other"


class TestExtractChangedLines:
    def test_extracts_additions(self):
        patch = """@@ -1,3 +1,4 @@
 existing line
+new line
 another existing
+another new
"""
        result = extract_changed_lines_only(patch)
        assert "+new line" in result
        assert "+another new" in result

    def test_empty_patch(self):
        assert extract_changed_lines_only("") == ""
        assert extract_changed_lines_only(None) == ""


class TestParseUnifiedDiff:
    def test_single_file(self):
        diff = """diff --git a/test.py b/test.py
@@ -1,3 +1,4 @@
 line1
+added
 line2
"""
        result = parse_unified_diff(diff)
        assert len(result) == 1
        assert result[0]["filename"] == "test.py"
        assert len(result[0]["hunks"]) == 1

    def test_multiple_files(self):
        diff = """diff --git a/a.py b/a.py
@@ -1 +1 @@
-old
+new
diff --git a/b.py b/b.py
@@ -1 +1 @@
-old2
+new2
"""
        result = parse_unified_diff(diff)
        assert len(result) == 2


class TestChunkDiff:
    def test_small_diff_single_chunk(self):
        files = [{"filename": "test.py", "hunks": [{"header": "@@ -1 +1 @@", "lines": ["+new"]}]}]
        chunks = chunk_diff_for_llm(files, max_lines_per_chunk=100)
        assert len(chunks) == 1
        assert chunks[0]["filename"] == "test.py"

    def test_large_diff_splits(self):
        large_lines = [f"+line{i}" for i in range(100)]
        files = [{"filename": "big.py", "hunks": [{"header": "@@", "lines": large_lines}]}]
        chunks = chunk_diff_for_llm(files, max_lines_per_chunk=30)
        assert len(chunks) > 1


# ─── Schema Tests ──────────────────────────────────────────────────────────────

class TestFinding:
    def test_creation(self):
        f = Finding(
            source_tool="bandit",
            agent="security",
            file="test.py",
            line=42,
            severity=Severity.HIGH,
            message="SQL injection risk",
        )
        assert f.source_tool == "bandit"
        assert f.severity == Severity.HIGH
        assert f.line == 42

    def test_optional_fields(self):
        f = Finding(source_tool="test", agent="test", file="f.py", message="msg")
        assert f.suggested_fix is None
        assert f.rule_id is None
        assert f.line == 0


class TestAgentResult:
    def test_with_findings(self):
        finding = Finding(source_tool="t", agent="a", file="f", message="m")
        result = AgentResult(agent_name="security", findings=[finding], summary="done")
        assert len(result.findings) == 1
        assert result.error is None

    def test_with_error(self):
        result = AgentResult(agent_name="test", error="failed")
        assert result.error == "failed"
        assert len(result.findings) == 0


class TestPRWebhookPayload:
    def test_creation(self):
        p = PRWebhookPayload(
            action="opened",
            pr_number=1,
            repo_full_name="owner/repo",
            head_sha="abc123",
            base_branch="main",
            head_branch="feature",
            pr_title="Test PR",
            pr_url="https://github.com/owner/repo/pull/1",
            sender="user",
        )
        assert p.pr_number == 1
        assert p.repo_full_name == "owner/repo"


# ─── Health Score Tests ────────────────────────────────────────────────────────

class TestHealthScore:
    def test_perfect_score(self):
        from app.agents.supervisor import _calculate_health_score
        assert _calculate_health_score([]) == 100.0

    def test_critical_penalty(self):
        from app.agents.supervisor import _calculate_health_score
        findings = [{"severity": "critical"}]
        score = _calculate_health_score(findings)
        assert score == 75.0

    def test_floor_at_zero(self):
        from app.agents.supervisor import _calculate_health_score
        findings = [{"severity": "critical"}] * 10
        score = _calculate_health_score(findings)
        assert score == 0.0
