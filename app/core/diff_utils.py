"""
Diff parsing and chunking utilities for Git Guardian AI.

Key constraint: Only send changed lines + minimal surrounding context to the LLM,
never full files. Chunk large diffs to stay within token budgets.
"""

import re
from typing import List, Dict, Tuple


def parse_unified_diff(diff_text: str) -> List[Dict]:
    """Parse a unified diff into per-file change records.
    
    Returns list of dicts:
        {
            "filename": str,
            "hunks": [{"header": str, "lines": [str]}],
            "added_lines": [(line_num, text)],
            "removed_lines": [(line_num, text)],
        }
    """
    files = []
    current_file = None
    current_hunk = None

    for line in diff_text.split("\n"):
        # New file header
        if line.startswith("diff --git"):
            if current_file:
                if current_hunk:
                    current_file["hunks"].append(current_hunk)
                files.append(current_file)
            # Extract filename from "diff --git a/path b/path"
            parts = line.split(" b/")
            filename = parts[-1] if len(parts) > 1 else "unknown"
            current_file = {
                "filename": filename,
                "hunks": [],
                "added_lines": [],
                "removed_lines": [],
            }
            current_hunk = None

        elif line.startswith("@@") and current_file:
            if current_hunk:
                current_file["hunks"].append(current_hunk)
            current_hunk = {"header": line, "lines": []}

            # Parse line numbers from @@ -a,b +c,d @@
            match = re.search(r"\+(\d+)", line)
            if match:
                current_hunk["start_line"] = int(match.group(1))

        elif current_hunk is not None:
            current_hunk["lines"].append(line)
            if line.startswith("+") and not line.startswith("+++"):
                line_num = current_hunk.get("start_line", 0)
                current_file["added_lines"].append((line_num, line[1:]))
            elif line.startswith("-") and not line.startswith("---"):
                current_file["removed_lines"].append((0, line[1:]))

    # Don't forget the last file/hunk
    if current_file:
        if current_hunk:
            current_file["hunks"].append(current_hunk)
        files.append(current_file)

    return files


def chunk_diff_for_llm(
    file_changes: List[Dict],
    max_lines_per_chunk: int = 500,
    context_lines: int = 3,
) -> List[Dict]:
    """Split file changes into LLM-friendly chunks.
    
    Each chunk contains:
        {
            "filename": str,
            "chunk_index": int,
            "diff_text": str,     # The actual diff lines
            "line_count": int,
        }
    
    This ensures we never send more than max_lines_per_chunk to the LLM
    in a single call, conserving API quota.
    """
    chunks = []

    for file_change in file_changes:
        filename = file_change["filename"]
        all_lines = []
        for hunk in file_change.get("hunks", []):
            all_lines.append(hunk["header"])
            all_lines.extend(hunk["lines"])

        if not all_lines:
            continue

        # Split into chunks if too large
        if len(all_lines) <= max_lines_per_chunk:
            chunks.append({
                "filename": filename,
                "chunk_index": 0,
                "diff_text": "\n".join(all_lines),
                "line_count": len(all_lines),
            })
        else:
            chunk_idx = 0
            for i in range(0, len(all_lines), max_lines_per_chunk):
                chunk_lines = all_lines[i : i + max_lines_per_chunk]
                chunks.append({
                    "filename": filename,
                    "chunk_index": chunk_idx,
                    "diff_text": "\n".join(chunk_lines),
                    "line_count": len(chunk_lines),
                })
                chunk_idx += 1

    return chunks


def classify_file_type(filename: str) -> str:
    """Classify a file by its extension for routing to appropriate agents."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    
    python_exts = {"py", "pyi"}
    js_ts_exts = {"js", "jsx", "ts", "tsx", "mjs", "cjs"}
    config_exts = {"json", "yaml", "yml", "toml", "ini", "cfg", "env"}
    doc_exts = {"md", "rst", "txt"}
    docker_names = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}

    fname_lower = filename.lower().split("/")[-1]

    if ext in python_exts:
        return "python"
    elif ext in js_ts_exts:
        return "javascript"
    elif ext in config_exts or fname_lower in docker_names:
        return "config"
    elif ext in doc_exts:
        return "documentation"
    elif fname_lower == "dockerfile":
        return "config"
    else:
        return "other"


def extract_changed_lines_only(patch: str) -> str:
    """Extract only the added/modified lines from a patch, with minimal context.
    
    This is the key cost-control measure: we send only changed lines + 
    a few lines of surrounding context, never the full file.
    """
    if not patch:
        return ""
    
    lines = patch.split("\n")
    result = []
    
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            result.append(line)
        elif line.startswith("+") or line.startswith("-"):
            result.append(line)
        elif line.startswith(" "):
            # Context line — only include if adjacent to a change
            nearby_change = False
            for j in range(max(0, i - 2), min(len(lines), i + 3)):
                if j != i and lines[j].startswith(("+", "-")) and not lines[j].startswith(("+++", "---")):
                    nearby_change = True
                    break
            if nearby_change:
                result.append(line)
    
    return "\n".join(result)
