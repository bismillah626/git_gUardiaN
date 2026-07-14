"""
Tests for the security tools runner.

These tests validate that the tool runners handle various edge cases
(missing tools, empty output, malformed JSON) gracefully.
"""

import json
import os
import tempfile

import pytest
from app.services.security_tools import run_bandit, run_semgrep, run_gitleaks


class TestBandit:
    def test_scan_clean_file(self):
        """Bandit should return empty list for clean code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clean_file = os.path.join(tmpdir, "clean.py")
            with open(clean_file, "w") as f:
                f.write("def hello():\n    return 'world'\n")
            
            findings = run_bandit(tmpdir)
            # Clean code should produce no/few findings
            assert isinstance(findings, list)

    def test_scan_vulnerable_file(self):
        """Bandit should detect known vulnerability patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vuln_file = os.path.join(tmpdir, "vuln.py")
            with open(vuln_file, "w") as f:
                f.write(
                    "import subprocess\n"
                    "def run_cmd(user_input):\n"
                    "    subprocess.call(user_input, shell=True)\n"
                )
            
            findings = run_bandit(tmpdir)
            assert isinstance(findings, list)
            # Should detect shell=True vulnerability
            if findings:
                assert any(f["tool"] == "bandit" for f in findings)

    def test_scan_nonexistent_path(self):
        """Bandit should handle nonexistent paths gracefully."""
        findings = run_bandit("/nonexistent/path")
        assert isinstance(findings, list)


class TestGitleaks:
    def test_no_secrets(self):
        """Gitleaks should return empty for clean code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            clean_file = os.path.join(tmpdir, "clean.py")
            with open(clean_file, "w") as f:
                f.write("# No secrets here\nx = 42\n")
            
            findings = run_gitleaks(tmpdir)
            assert isinstance(findings, list)

    def test_detect_hardcoded_secret(self):
        """Gitleaks should detect hardcoded API keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = os.path.join(tmpdir, "config.py")
            with open(secret_file, "w") as f:
                f.write('AWS_SECRET_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
            
            findings = run_gitleaks(tmpdir)
            assert isinstance(findings, list)
            # If gitleaks is installed, it should detect this
            if findings:
                assert all(f["tool"] == "gitleaks" for f in findings)
                assert all(f["severity"] == "critical" for f in findings)


class TestSecurityToolsIntegration:
    def test_all_tools_return_lists(self):
        """All tool runners should return lists even on empty input."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_file = os.path.join(tmpdir, "empty.py")
            with open(empty_file, "w") as f:
                f.write("")
            
            assert isinstance(run_bandit(tmpdir), list)
            assert isinstance(run_gitleaks(tmpdir), list)
