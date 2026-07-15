"""
Seed script: creates intentionally buggy files for testing Git Guardian.

Run this to populate a test repo with known vulnerabilities, quality issues,
missing tests, and documentation gaps so the review pipeline can be validated.
"""

import os

BUGGY_FILES = {
    "vulnerable_app.py": '''"""A deliberately insecure Python application for testing Git Guardian."""

import subprocess
import sqlite3
import pickle
import hashlib

# Hardcoded secret (Gitleaks should catch this)
API_KEY = "sk-proj-FAKE1234567890abcdefghijklmnopqrstuvwxyz"
DATABASE_PASSWORD = "super_secret_password_123"

def execute_command(user_input):
    """Execute a shell command — INSECURE: shell injection."""
    # B602: subprocess with shell=True
    result = subprocess.call(user_input, shell=True)
    return result

def get_user(username):
    """Query user from database — INSECURE: SQL injection."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # SQL injection vulnerability
    query = "SELECT * FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    return cursor.fetchone()

def load_data(data_bytes):
    """Load serialized data — INSECURE: arbitrary code execution via pickle."""
    # B301: pickle.loads
    return pickle.loads(data_bytes)

def hash_password(password):
    """Hash a password — INSECURE: MD5 is broken."""
    # B303: use of insecure MD5 hash
    return hashlib.md5(password.encode()).hexdigest()

def read_file(filename):
    """Read a file with no error handling and no input validation."""
    f = open(filename, "r")
    content = f.read()
    # No f.close() — resource leak
    return content

class UserManager:
    def process(self, data):
        exec(data)  # B102: use of exec

    def eval_input(self, expr):
        return eval(expr)  # B307: use of eval
''',

    "bad_quality.py": '''# No module docstring
import os, sys, json  # Wildcard-style multi-import on one line
from os import *  # Wildcard import

x = 1  # Bad variable name
def f(a,b,c,d,e,f,g):  # Too many parameters, single-letter names, no docstring
    if a == True:  # Should use `if a:`
        if b == True:
            if c == True:
                if d == True:  # Deeply nested
                    return e + f + g
    return None

def CalculateTotal(items_list):  # Wrong naming convention (should be snake_case)
    Total = 0  # Wrong variable naming
    for Item in items_list:
        Total = Total + Item
    return Total

def duplicate_logic_1(data):
    result = []
    for item in data:
        if item > 0:
            result.append(item * 2)
    return result

def duplicate_logic_2(data):
    result = []
    for item in data:
        if item > 0:
            result.append(item * 2)
    return result

try:
    risky_operation = 1 / 0
except:  # Bare except
    pass  # Swallowed exception
''',

    "no_tests_module.py": '''"""Module with functions that have no tests."""

def calculate_tax(amount, rate=0.1):
    """Calculate tax on an amount."""
    if amount < 0:
        raise ValueError("Amount cannot be negative")
    return amount * rate

def validate_email(email):
    """Check if an email address is valid."""
    if "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    if "." not in parts[1]:
        return False
    return True

def merge_configs(base, override):
    """Deep merge two configuration dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    return result

def parse_csv_line(line, delimiter=","):
    """Parse a single CSV line handling quoted fields."""
    fields = []
    current = ""
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == delimiter and not in_quotes:
            fields.append(current.strip())
            current = ""
        else:
            current += char
    fields.append(current.strip())
    return fields
''',

    "undocumented.py": '''import re

class DataProcessor:
    def __init__(self, config):
        self.config = config
        self.results = []

    def process(self, raw_data):
        cleaned = self._clean(raw_data)
        validated = self._validate(cleaned)
        transformed = self._transform(validated)
        self.results.append(transformed)
        return transformed

    def _clean(self, data):
        if isinstance(data, str):
            return data.strip().lower()
        return data

    def _validate(self, data):
        if not data:
            raise ValueError("Empty data")
        if isinstance(data, str) and len(data) > 10000:
            raise ValueError("Data too large")
        return data

    def _transform(self, data):
        if isinstance(data, str):
            return re.sub(r"[^a-z0-9\\s]", "", data)
        return str(data)

    def get_summary(self):
        return {
            "total": len(self.results),
            "latest": self.results[-1] if self.results else None,
        }

def connect_database(host, port, username, password):
    return {"host": host, "port": port, "user": username}

def retry_operation(func, max_retries=3, delay=1):
    last_error = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_error = e
    raise last_error
''',
}


def create_test_repo(output_dir: str = "test_repo"):
    """Create a directory with intentionally buggy files."""
    os.makedirs(output_dir, exist_ok=True)

    for filename, content in BUGGY_FILES.items():
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            f.write(content)
        print(f"  Created: {filepath}")

    print(f"\n✅ Test repo created at: {output_dir}/")
    print(f"   Files: {len(BUGGY_FILES)}")
    print("   Known issues: SQL injection, shell injection, hardcoded secrets,")
    print("   pickle deserialization, MD5 hashing, bare except, no docstrings,")
    print("   duplicate logic, naming violations, untested functions")


if __name__ == "__main__":
    create_test_repo()
