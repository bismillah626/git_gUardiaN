# 🛡️ CodeGuardian AI

**A team of cooperating AI agents that reviews every pull request like a senior engineer** — checking code quality, security vulnerabilities, missing tests, and documentation — then posts a single, prioritized report and can optionally open an auto-fix PR behind a human-approval gate.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-green)
![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-purple)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 🧠 Why CodeGuardian?

Manual code review doesn't scale. Security flaws — SQL injection, hardcoded secrets, insecure dependencies — slip through, and small teams can't afford a dedicated AppSec engineer. Traditional linters catch syntax issues but can't reason about intent, business logic, or cross-file risk.

CodeGuardian solves this with:

- **True multi-agent architecture** (supervisor + 4 specialists), not a single chatbot wrapper
- **Deterministic static analysis + LLM reasoning** — the LLM triages and explains tool findings, it never invents issues from scratch
- **MCP (Model Context Protocol)** for connecting agents to GitHub/filesystem/shell
- **RAG-grounded quality feedback** citing your team's own coding standards
- **Human-in-the-loop auto-fixes** — suggested fixes go to a separate branch, never auto-merged

---

## 🏗️ Architecture

```
Developer opens PR → GitHub Webhook → FastAPI Endpoint
                                          │
                                    ┌─────┴─────┐
                                    │ Supervisor │ (LangGraph)
                                    │   Agent    │
                                    └─────┬─────┘
                          ┌───────────────┼───────────────┐
                    ┌─────┴─────┐  ┌──────┴──────┐  ┌────┴────┐  ┌─────┴─────┐
                    │ Security  │  │  Quality    │  │Test-Gap │  │   Docs    │
                    │  Agent    │  │   Agent     │  │  Agent  │  │  Agent    │
                    └─────┬─────┘  └──────┬──────┘  └────┬────┘  └─────┬─────┘
                          │               │              │             │
                    Bandit/Semgrep    RAG + LLM       LLM           LLM
                    Gitleaks/ESLint   (ChromaDB)
                          │               │              │             │
                          └───────────────┼──────────────┘─────────────┘
                                    ┌─────┴─────┐
                                    │ Aggregator │
                                    └─────┬─────┘
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                         PR Comment   Auto-fix    Dashboard
                         (GitHub)     Branch      (Streamlit)
                                    (separate,
                                   human-gated)
```

### Agent Details

| Agent | What it does | Tools |
|-------|-------------|-------|
| **Security** | Runs static analysis, LLM triages/explains findings | Bandit, Semgrep, Gitleaks, ESLint |
| **Quality** | Checks style, complexity, naming, duplicates | RAG over coding standards + LLM |
| **Test-Gap** | Finds untested functions, drafts starter tests | LLM analysis of diff |
| **Documentation** | Flags missing docstrings, drafts replacements | LLM analysis of diff |
| **Supervisor** | Orchestrates all agents, aggregates, posts report | LangGraph graph |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Orchestration | LangGraph (explicit graph with parallel fan-out) |
| LLM Backend | Groq (primary) + Claude (fallback) via API |
| Tool Connectivity | MCP-compatible tool definitions |
| Static Analysis | Semgrep, Bandit, ESLint, Gitleaks |
| RAG | ChromaDB (API-based embeddings, no PyTorch) |
| Backend | FastAPI (webhook receiver + orchestration) |
| Dashboard | Streamlit + Plotly |
| Database | PostgreSQL |
| Infrastructure | Docker, docker-compose |

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- A GitHub token with repo access
- A Groq API key (free tier: [console.groq.com](https://console.groq.com))

### Setup

```bash
# Clone the repo
git clone https://github.com/bismillah626/git_gUardiaN.git
cd git_gUardiaN

# Create your .env file
cp .env.example .env
# Edit .env with your actual tokens

# Start everything
docker-compose up --build
```

This starts:
- **FastAPI API** on `http://localhost:8000`
- **Streamlit Dashboard** on `http://localhost:8501`
- **PostgreSQL** on port `5432`

### Configure GitHub Webhook

1. Go to your GitHub repo → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://your-server.com/webhook/github`
3. **Content type:** `application/json`
4. **Secret:** Same as `GITHUB_WEBHOOK_SECRET` in your `.env`
5. **Events:** Select "Pull requests"
6. Save

### Manual Trigger

You can also trigger a review manually:

```bash
curl -X POST "http://localhost:8000/review?repo=owner/repo&pr_number=1"
```

---

## 🔒 Security Design

### Hallucination Guardrail
The LLM **never invents findings from scratch**. Every security finding must trace back to actual Semgrep/Bandit/Gitleaks tool output. A validation step rejects any finding without a matching tool source.

### Cost Control
- Only changed lines + minimal context sent to LLM (never full files)
- Large diffs chunked to stay within token budgets
- Findings batched per file (not per line) to reduce API calls
- RAG retrievals cached within a single PR run

### Rate-Limit Resilience
- Tenacity-based retry with exponential backoff on 429s
- Automatic fallback from Groq → Claude if one provider is throttled
- Graceful degradation (raw tool output if LLM triage fails)

### Auto-Fix Safety
- Fix commits always go to a **separate branch** (`codeguardian/auto-fix/<PR>`)
- **Never auto-merges** — requires explicit human approval
- Fix PR clearly labeled with what was changed and why

---

## 📊 Dashboard

The Streamlit dashboard at `localhost:8501` shows:

- **Severity breakdown** (pie chart) across all reviews
- **Code health trend lines** per repository over time
- **Review turnaround time** (bar chart)
- **Detailed review history** with expandable findings

---

## 📁 Project Structure

```
git_guardian/
├── app/
│   ├── agents/
│   │   ├── supervisor.py          # LangGraph orchestration graph
│   │   ├── security_agent.py      # Bandit/Semgrep/Gitleaks + LLM triage
│   │   ├── quality_agent.py       # RAG-grounded code quality review
│   │   ├── test_gap_agent.py      # Test coverage gap analysis
│   │   └── documentation_agent.py # Missing docstring detection
│   ├── core/
│   │   ├── config.py              # Centralized settings (pydantic-settings)
│   │   ├── database.py            # SQLAlchemy + Postgres
│   │   ├── github_client.py       # PyGithub wrapper
│   │   ├── llm_provider.py        # Groq/Claude with retry/fallback
│   │   └── diff_utils.py          # Diff parsing, chunking, classification
│   ├── dashboard/
│   │   └── app.py                 # Streamlit dashboard
│   ├── mcp_servers/
│   │   └── security_server.py     # MCP tool definitions
│   ├── models/
│   │   └── schemas.py             # Pydantic data contracts
│   ├── services/
│   │   ├── rag_service.py         # ChromaDB RAG for coding standards
│   │   └── security_tools.py      # Static analysis tool runners
│   └── main.py                    # FastAPI entrypoint
├── tests/
│   ├── test_core.py               # Unit tests for utilities & schemas
│   └── test_security_tools.py     # Integration tests for scanners
├── scripts/
│   └── seed_test_repo.py          # Generate buggy test files
├── docker-compose.yml             # API + Postgres + Dashboard
├── Dockerfile                     # Python 3.11 + Node.js + security tools
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🧪 Testing

```bash
# Run unit tests
pip install pytest
pytest tests/ -v

# Seed a test repo with known vulnerabilities
python scripts/seed_test_repo.py
```

---

## 📝 License

MIT

---

*Built with LangGraph, Groq, ChromaDB, FastAPI, and Streamlit.*
