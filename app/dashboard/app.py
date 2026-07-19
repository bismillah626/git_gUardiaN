"""
Git Guardian AI — Streamlit Dashboard.

Two views:
  A) Live Pipeline Monitor — watch agents run in real-time
  B) Review History & Branch Context — browse past reviews with trends
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import (
    get_all_reviews, get_reviews_by_repo, init_db, SessionLocal,
    ReviewRecordDB, AgentRunStatusDB,
    get_agent_statuses, get_latest_in_progress_review, get_review_by_id,
)

logger = logging.getLogger(__name__)

# ─── Severity color map (consistent with PR comment) ──────────────────────────

SEV_COLORS = {
    "critical": "#ef4444", "high": "#f97316", "medium": "#eab308",
    "low": "#3b82f6", "info": "#9ca3af",
}
SEV_EMOJI = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
}
STATUS_COLORS = {
    "queued": "#6b7280", "running": "#3b82f6", "done": "#22c55e", "failed": "#ef4444",
}
STATUS_ICONS = {
    "queued": "⏳", "running": "🔄", "done": "✅", "failed": "❌",
}
AGENT_DISPLAY = {
    "security": ("🔒", "Security"),
    "quality": ("✨", "Code Quality"),
    "test_gap": ("🧪", "Test Gaps"),
    "documentation": ("📝", "Documentation"),
}

# ─── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Git Guardian AI Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem; font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .agent-card {
        border-radius: 12px; padding: 1.2rem; min-height: 140px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(26, 26, 46, 0.7);
        backdrop-filter: blur(8px);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .agent-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
    .agent-card h4 { margin: 0 0 0.5rem 0; font-size: 1rem; }
    .agent-card .status-badge {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        font-size: 0.8rem; font-weight: 600; color: #fff;
    }
    .agent-card .status-msg { font-size: 0.85rem; color: #aaa; margin-top: 0.4rem; }
    .agent-card .elapsed { font-size: 0.78rem; color: #888; margin-top: 0.3rem; }
    .summary-strip {
        border-radius: 12px; padding: 1rem 1.5rem;
        background: linear-gradient(135deg, rgba(34,197,94,0.12) 0%, rgba(102,126,234,0.12) 100%);
        border: 1px solid rgba(34,197,94,0.25);
    }
    .stMetric > div { background: rgba(102, 126, 234, 0.1); border-radius: 8px; padding: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ─── Init DB ───────────────────────────────────────────────────────────────────

try:
    init_db()
except Exception:
    pass

# ─── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.markdown("# 🛡️ Git Guardian AI")
st.sidebar.markdown("### Live Dashboard")
st.sidebar.markdown("---")

view = st.sidebar.radio(
    "📌 Navigation",
    ["🔴 Live Pipeline Monitor", "📋 Review History"],
    index=0,
)

st.sidebar.markdown("---")

# Quick stats
try:
    all_reviews = get_all_reviews(limit=500)
except Exception as e:
    st.sidebar.error(f"DB error: {e}")
    all_reviews = []

repos = sorted(set(r.repo_full_name for r in all_reviews)) if all_reviews else []
st.sidebar.markdown(f"**Total Reviews:** {len(all_reviews)}")
st.sidebar.markdown(f"**Repos Tracked:** {len(repos)}")


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW A — LIVE PIPELINE MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

def render_agent_card(agent_name: str, status_row):
    """Render a single agent status card."""
    icon, display_name = AGENT_DISPLAY.get(agent_name, ("🤖", agent_name))
    status = status_row.status if status_row else "queued"
    msg = status_row.status_message if status_row else "Waiting..."
    color = STATUS_COLORS.get(status, "#6b7280")
    status_icon = STATUS_ICONS.get(status, "⏳")

    # Elapsed time
    elapsed_str = ""
    if status_row and status_row.started_at:
        end = status_row.completed_at or datetime.utcnow()
        secs = (end - status_row.started_at).total_seconds()
        elapsed_str = f"{secs:.1f}s"

    st.markdown(f"""
    <div class="agent-card" style="border-left: 4px solid {color};">
        <h4>{icon} {display_name}</h4>
        <span class="status-badge" style="background:{color};">
            {status_icon} {status.upper()}
        </span>
        <div class="status-msg">{msg or '—'}</div>
        {'<div class="elapsed">⏱ ' + elapsed_str + '</div>' if elapsed_str else ''}
    </div>
    """, unsafe_allow_html=True)


def render_live_monitor():
    st.markdown('<p class="main-header">🔴 Live Pipeline Monitor</p>', unsafe_allow_html=True)
    st.markdown("*Watch agents analyze your PR in real-time*")
    st.markdown("---")

    # Pick which review to watch
    in_progress = get_latest_in_progress_review()
    review_options = []

    # Build dropdown options from recent reviews
    recent = get_all_reviews(limit=20)
    for r in recent:
        label = f"#{r.id} — {r.repo_full_name} PR #{r.pr_number}"
        if r == in_progress:
            label += " 🔴 IN PROGRESS"
        review_options.append((label, r.id))

    if not review_options:
        st.info("👋 **No reviews yet!** Trigger a review via `/review` to see live agent progress here.")
        return

    # Default to in-progress if available
    default_idx = 0
    if in_progress:
        for i, (_, rid) in enumerate(review_options):
            if rid == in_progress.id:
                default_idx = i
                break

    selected_label = st.selectbox(
        "🎯 Select Review to Monitor",
        [opt[0] for opt in review_options],
        index=default_idx,
    )
    selected_id = review_options[[opt[0] for opt in review_options].index(selected_label)][1]

    review = get_review_by_id(selected_id)
    if not review:
        st.error("Review not found.")
        return

    statuses = get_agent_statuses(selected_id)
    status_map = {s.agent_name: s for s in statuses}

    # Determine if still running
    terminal = {"done", "failed"}
    agent_names = ["security", "quality", "test_gap", "documentation"]
    done_count = sum(1 for a in agent_names if status_map.get(a) and status_map[a].status in terminal)
    is_running = done_count < len(agent_names) and len(statuses) > 0

    # Overall progress
    if statuses:
        st.markdown(f"### {'🔄' if is_running else '✅'} {done_count} of {len(agent_names)} agents complete")
        st.progress(done_count / len(agent_names))
    else:
        st.warning("No agent status data for this review (may have been created before live tracking was added).")

    # Agent cards grid
    cols = st.columns(4)
    for i, agent in enumerate(agent_names):
        with cols[i]:
            render_agent_card(agent, status_map.get(agent))

    st.markdown("")

    # Completion summary strip
    if not is_running and review.total_findings is not None and review.total_findings >= 0 and statuses:
        score = review.code_health_score or 0
        if score >= 80:
            h_emoji = "🟢"
        elif score >= 60:
            h_emoji = "🟡"
        elif score >= 40:
            h_emoji = "🟠"
        else:
            h_emoji = "🔴"

        st.markdown("---")
        st.markdown(f"""
        <div class="summary-strip">
            <strong>✅ Review Complete</strong> &nbsp;|&nbsp;
            Health: {h_emoji} <strong>{score:.0f}/100</strong> &nbsp;|&nbsp;
            🔴 {review.critical_count or 0} Critical &nbsp;
            🟠 {review.high_count or 0} High &nbsp;
            🟡 {review.medium_count or 0} Medium &nbsp;
            🔵 {review.low_count or 0} Low &nbsp;
            ⚪ {review.info_count or 0} Info &nbsp;|&nbsp;
            ⏱ {review.review_duration_seconds:.1f}s
            {'&nbsp;|&nbsp; <a href="' + review.pr_url + '" target="_blank">View PR ↗</a>' if review.pr_url else ''}
        </div>
        """, unsafe_allow_html=True)

    # Auto-refresh if still running
    if is_running:
        import time as _time
        _time.sleep(3)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW B — REVIEW HISTORY & BRANCH CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

def _get_autofix_branch_state(repo_full_name: str, branch_name: str):
    """Query GitHub for the current state of an auto-fix branch/PR."""
    try:
        from app.core.github_client import GitHubClient
        gh = GitHubClient()
        repo = gh.get_repo(repo_full_name)

        # Look for PRs from this branch
        pulls = repo.get_pulls(state="all", head=f"{repo_full_name.split('/')[0]}:{branch_name}")
        for pr in pulls:
            if pr.merged:
                return "merged", pr.html_url
            elif pr.state == "closed":
                return "closed", pr.html_url
            else:
                return "open", pr.html_url

        # No PR found — check if branch exists
        try:
            repo.get_branch(branch_name)
            return "branch_only", None
        except Exception:
            return "deleted", None
    except Exception as e:
        logger.warning(f"GitHub lookup failed for {branch_name}: {e}")
        return "unknown", None


def render_findings_detail(findings_json: str):
    """Render finding breakdown matching the PR comment structure."""
    try:
        findings = json.loads(findings_json) if findings_json else []
    except (json.JSONDecodeError, TypeError):
        findings = []

    if not findings:
        st.info("No findings for this review.")
        return

    # Group by agent
    by_agent = {}
    for f in findings:
        agent = f.get("agent", "unknown")
        by_agent.setdefault(agent, []).append(f)

    for agent, agent_findings in by_agent.items():
        icon, title = AGENT_DISPLAY.get(agent, ("🤖", agent.capitalize()))
        st.markdown(f"**{icon} {title}** ({len(agent_findings)} findings)")

        for f in agent_findings:
            sev = f.get("severity", "info")
            emoji = SEV_EMOJI.get(sev, "⚪")
            file_ref = f.get("file", "?")
            line = f.get("line", 0)
            loc = f"`{file_ref}:{line}`" if line else f"`{file_ref}`"
            st.markdown(
                f"- {emoji} **[{sev.upper()}]** {loc} — {f.get('message', 'N/A')}"
            )
            if f.get("source_tool") and agent == "security":
                st.caption(f"   🔧 Tool: `{f['source_tool']}` | Rule: `{f.get('rule_id', 'N/A')}`")
            if f.get("suggested_fix"):
                with st.expander("💡 Suggested fix"):
                    st.code(f["suggested_fix"][:500])


def render_history():
    st.markdown('<p class="main-header">📋 Review History & Branch Context</p>', unsafe_allow_html=True)
    st.markdown("*Browse past reviews, track code health, and monitor auto-fix branches*")
    st.markdown("---")

    # Repo filter
    selected_repo = st.selectbox(
        "📁 Filter by Repository",
        options=["All Repositories"] + repos,
        index=0,
    )

    if selected_repo != "All Repositories":
        reviews = [r for r in all_reviews if r.repo_full_name == selected_repo]
    else:
        reviews = all_reviews

    if not reviews:
        st.info("👋 **No reviews yet!** Trigger a PR review to see data here.")
        return

    # ── Key Metrics ────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    total_findings = sum(r.total_findings for r in reviews)
    total_critical = sum(r.critical_count for r in reviews)
    avg_health = sum(r.code_health_score for r in reviews) / len(reviews) if reviews else 0
    avg_duration = sum(r.review_duration_seconds for r in reviews) / len(reviews) if reviews else 0

    with col1:
        st.metric("Total Reviews", len(reviews))
    with col2:
        st.metric("Total Findings", total_findings)
    with col3:
        st.metric("🔴 Critical", total_critical)
    with col4:
        st.metric("Avg Health", f"{avg_health:.0f}/100")
    with col5:
        st.metric("Avg Time", f"{avg_duration:.1f}s")

    st.markdown("---")

    # ── Charts ─────────────────────────────────────────────────────────────
    chart_col1, chart_col2 = st.columns(2)

    # Severity breakdown
    with chart_col1:
        st.subheader("📊 Severity Breakdown")
        sev_data = {
            "Critical": total_critical,
            "High": sum(r.high_count for r in reviews),
            "Medium": sum(r.medium_count for r in reviews),
            "Low": sum(r.low_count for r in reviews),
            "Info": sum(r.info_count for r in reviews),
        }
        sev_data = {k: v for k, v in sev_data.items() if v > 0}
        if sev_data:
            fig_pie = px.pie(
                names=list(sev_data.keys()), values=list(sev_data.values()),
                color=list(sev_data.keys()),
                color_discrete_map={
                    "Critical": "#ef4444", "High": "#f97316", "Medium": "#eab308",
                    "Low": "#3b82f6", "Info": "#9ca3af",
                },
                hole=0.4,
            )
            fig_pie.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font_color="white", margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No findings to display")

    # Health trend
    with chart_col2:
        st.subheader("📈 Code Health Trend")
        if len(reviews) >= 2:
            health_data = sorted(
                [(r.created_at, r.code_health_score, r.repo_full_name) for r in reviews],
                key=lambda x: x[0],
            )
            fig_trend = go.Figure()
            repos_in_data = set(d[2] for d in health_data)
            colors = px.colors.qualitative.Set2
            for i, repo in enumerate(repos_in_data):
                repo_data = [(d[0], d[1]) for d in health_data if d[2] == repo]
                dates, scores = zip(*repo_data)
                fig_trend.add_trace(go.Scatter(
                    x=list(dates), y=list(scores),
                    mode="lines+markers", name=repo.split("/")[-1],
                    line=dict(color=colors[i % len(colors)], width=2),
                    marker=dict(size=6),
                ))
            fig_trend.update_layout(
                yaxis_title="Health Score", yaxis_range=[0, 105],
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font_color="white", margin=dict(t=20, b=20, l=40, r=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("Need at least 2 reviews for trend data")

    st.markdown("---")

    # ── Review Table ───────────────────────────────────────────────────────
    st.subheader("📋 Recent Reviews")

    for r in reviews[:30]:
        if r.code_health_score >= 80:
            h_emoji = "🟢"
        elif r.code_health_score >= 60:
            h_emoji = "🟡"
        elif r.code_health_score >= 40:
            h_emoji = "🟠"
        else:
            h_emoji = "🔴"

        branch_info = f" | `{r.head_branch}`" if r.head_branch else ""
        title_info = f" — {r.pr_title}" if r.pr_title else ""
        ts = r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else 'N/A'

        with st.expander(
            f"{h_emoji} **{r.repo_full_name}** PR #{r.pr_number}{title_info} | "
            f"Score: {r.code_health_score:.0f}/100 | "
            f"{r.total_findings} findings{branch_info} | {ts}"
        ):
            # Detail metrics row
            dc = st.columns(7)
            with dc[0]:
                st.metric("🔴 Critical", r.critical_count)
            with dc[1]:
                st.metric("🟠 High", r.high_count)
            with dc[2]:
                st.metric("🟡 Medium", r.medium_count)
            with dc[3]:
                st.metric("🔵 Low", r.low_count)
            with dc[4]:
                st.metric("⚪ Info", r.info_count)
            with dc[5]:
                st.metric("⏱️ Duration", f"{r.review_duration_seconds:.1f}s")
            with dc[6]:
                if r.pr_url:
                    st.markdown(f"[View PR ↗]({r.pr_url})")
                st.caption(f"Commit: `{r.commit_sha[:8] if r.commit_sha else 'N/A'}`")

            # Auto-fix branch status
            if r.auto_fix_branch:
                state, pr_url = _get_autofix_branch_state(r.repo_full_name, r.auto_fix_branch)
                state_labels = {
                    "open": "🟢 Open (awaiting review)",
                    "merged": "✅ Merged",
                    "closed": "🔴 Closed",
                    "branch_only": "🟡 Branch exists (no PR)",
                    "deleted": "⚪ Deleted",
                    "unknown": "❓ Unknown",
                }
                label = state_labels.get(state, state)
                link = f" — [View PR ↗]({pr_url})" if pr_url else ""
                st.info(f"🔧 Auto-fix branch: `{r.auto_fix_branch}` → {label}{link}")

            # Full findings breakdown
            if r.findings_json:
                render_findings_detail(r.findings_json)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

if view == "🔴 Live Pipeline Monitor":
    render_live_monitor()
else:
    render_history()

# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "*🛡️ Git Guardian AI — Multi-Agent Code Review & DevSecOps Platform | "
    "Powered by LangGraph, Groq, ChromaDB*"
)
