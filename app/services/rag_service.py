"""
RAG (Retrieval-Augmented Generation) service for CodeGuardian AI.

Indexes the repo's code + coding-standards document into ChromaDB.
Uses Groq-based embeddings (via the LLM to generate pseudo-embeddings)
or a lightweight hashing-based approach to avoid PyTorch/sentence-transformers.

The Quality Agent uses this to ground its feedback in the team's own standards.
"""

import hashlib
import json
import logging
import os
from typing import List, Dict, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default coding standards document that ships with CodeGuardian
DEFAULT_CODING_STANDARDS = """
# CodeGuardian Default Coding Standards

## Python Standards
1. **PEP 8 Compliance**: All Python code must follow PEP 8 style guidelines.
2. **Type Hints**: All function signatures must include type hints (PEP 484).
3. **Docstrings**: All public functions, classes, and modules must have docstrings (Google style).
4. **Maximum Line Length**: 100 characters for code, 120 for comments/docstrings.
5. **Import Order**: stdlib → third-party → local, separated by blank lines (isort compatible).
6. **No Wildcard Imports**: `from module import *` is prohibited.
7. **Error Handling**: Never use bare `except:`. Always catch specific exceptions.
8. **Naming Conventions**: snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants.

## JavaScript/TypeScript Standards
1. **ESLint Compliance**: All JS/TS code must pass ESLint with the project's config.
2. **Strict Mode**: Use `'use strict'` or TypeScript strict mode.
3. **const/let over var**: Never use `var`.
4. **Arrow Functions**: Prefer arrow functions for callbacks and short functions.
5. **Async/Await**: Prefer async/await over raw Promises or callbacks.
6. **No console.log in production**: Use a proper logging library.

## Security Standards
1. **No Hardcoded Secrets**: API keys, passwords, tokens must come from environment variables.
2. **Input Validation**: All user inputs must be validated/sanitized before use.
3. **SQL Injection Prevention**: Use parameterized queries or an ORM. Never concatenate user input into SQL.
4. **Dependency Security**: No known-vulnerable dependencies (check with safety/npm audit).
5. **HTTPS Only**: All external API calls must use HTTPS.

## Testing Standards
1. **Minimum Coverage**: Critical paths must have unit tests.
2. **Test Naming**: test_<function_name>_<scenario> pattern.
3. **No Side Effects**: Tests must be independent and idempotent.
4. **Mock External Services**: Never call real APIs in unit tests.

## Documentation Standards
1. **README Required**: Every project must have a README with setup, usage, and architecture.
2. **Changelog**: Maintain a CHANGELOG.md for notable changes.
3. **API Documentation**: All API endpoints must be documented (FastAPI auto-docs count).
4. **Inline Comments**: Explain *why*, not *what*.
"""


class RAGService:
    """ChromaDB-backed retrieval service for coding standards and repo context."""

    def __init__(self):
        self._client = None
        self._standards_collection = None
        self._code_collection = None

    @property
    def client(self) -> chromadb.ClientAPI:
        if self._client is None:
            persist_dir = settings.chroma_persist_dir
            os.makedirs(persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(path=persist_dir)
        return self._client

    @property
    def standards_collection(self):
        if self._standards_collection is None:
            self._standards_collection = self.client.get_or_create_collection(
                name="coding_standards",
                metadata={"description": "Team coding standards and best practices"},
            )
        return self._standards_collection

    @property
    def code_collection(self):
        if self._code_collection is None:
            self._code_collection = self.client.get_or_create_collection(
                name="repo_code",
                metadata={"description": "Indexed repository code for context"},
            )
        return self._code_collection

    def index_coding_standards(self, standards_text: Optional[str] = None) -> int:
        """Index the coding standards document into ChromaDB.
        
        Splits the document into sections and indexes each as a separate document.
        Returns the number of sections indexed.
        """
        text = standards_text or DEFAULT_CODING_STANDARDS
        sections = self._split_into_sections(text)
        
        if not sections:
            logger.warning("No sections found in coding standards document")
            return 0

        ids = []
        documents = []
        metadatas = []
        
        for i, section in enumerate(sections):
            section_id = f"standard_{hashlib.md5(section.encode()).hexdigest()[:12]}"
            ids.append(section_id)
            documents.append(section)
            metadatas.append({"source": "coding_standards", "section_index": i})

        # Upsert to handle re-indexing
        self.standards_collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        
        logger.info(f"Indexed {len(sections)} coding standard sections")
        return len(sections)

    def index_code_file(self, filepath: str, content: str, repo_name: str = "") -> None:
        """Index a single code file into the code collection."""
        doc_id = f"code_{hashlib.md5((repo_name + filepath).encode()).hexdigest()[:12]}"
        
        # Split large files into chunks
        chunks = self._split_code_into_chunks(content, max_lines=50)
        
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}_chunk_{i}"
            self.code_collection.upsert(
                ids=[chunk_id],
                documents=[chunk],
                metadatas=[{
                    "filepath": filepath,
                    "repo": repo_name,
                    "chunk_index": i,
                }],
            )

    def query_standards(self, query: str, n_results: int = 3) -> List[Dict]:
        """Query the coding standards collection for relevant standards.
        
        Returns list of dicts with 'document' and 'metadata' keys.
        Used by the Quality Agent to cite which standard was violated.
        """
        try:
            results = self.standards_collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            
            output = []
            if results and results["documents"]:
                for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                    output.append({"document": doc, "metadata": meta})
            return output
        except Exception as e:
            logger.error(f"Standards query failed: {e}")
            return []

    def query_code(self, query: str, n_results: int = 5) -> List[Dict]:
        """Query the code collection for relevant code context."""
        try:
            results = self.code_collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            
            output = []
            if results and results["documents"]:
                for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                    output.append({"document": doc, "metadata": meta})
            return output
        except Exception as e:
            logger.error(f"Code query failed: {e}")
            return []

    def _split_into_sections(self, text: str) -> List[str]:
        """Split a markdown document into sections by ## headers."""
        sections = []
        current_section = []
        
        for line in text.strip().split("\n"):
            if line.startswith("## ") and current_section:
                sections.append("\n".join(current_section).strip())
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append("\n".join(current_section).strip())
        
        # Filter out very short sections (just a header)
        return [s for s in sections if len(s) > 20]

    def _split_code_into_chunks(self, content: str, max_lines: int = 50) -> List[str]:
        """Split code content into manageable chunks."""
        lines = content.split("\n")
        chunks = []
        
        for i in range(0, len(lines), max_lines):
            chunk = "\n".join(lines[i : i + max_lines])
            if chunk.strip():
                chunks.append(chunk)
        
        return chunks if chunks else [content]


# Singleton
rag_service = RAGService()
