"""
Git Guardian AI — Streamlit Dashboard.

Displays historical review data from Postgres:
- Review history with severity breakdown
- Code health trends per repo
- Vulnerability statistics and turnaround times
"""

import json
import os
import sys
from datetime import datetime, timedelta

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import get_all_reviews, get_reviews_by_repo, init_db, SessionLocal, ReviewRecordDB


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
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid rgba(102, 126, 234, 0.3);
    }
    .stMetric > div {
        background: rgba(102, 126, 234, 0.1);
        border-radius: 8px;
        padding: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)


# ─── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.markdown("# 🛡️ Git Guardian AI")
st.sidebar.markdown("### Multi-Agent Code Review Platform")
st.sidebar.markdown("---")

# Initialize DB
try:
    init_db()
except Exception:
    pass

# Fetch all reviews
try:
    all_reviews = get_all_reviews(limit=500)
except Exception as e:
    st.sidebar.error(f"Database connection failed: {e}")
    all_reviews = []

# Get unique repos
repos = list(set(r.repo_full_name for r in all_reviews)) if all_reviews else []
repos.sort()

selected_repo = st.sidebar.selectbox(
    "📁 Filter by Repository",
    options=["All Repositories"] + repos,
    index=0,
)

if selected_repo != "All Repositories":
    reviews = [r for r in all_reviews if r.repo_full_name == selected_repo]
else:
    reviews = all_reviews

st.sidebar.markdown("---")
st.sidebar.markdown(f"**Total Reviews:** {len(all_reviews)}")
st.sidebar.markdown(f"**Repos Tracked:** {len(repos)}")


# ─── Main Content ─────────────────────────────────────────────────────────────

st.markdown('<p class="main-header">🛡️ Git Guardian AI Dashboard</p>', unsafe_allow_html=True)
st.markdown("*Multi-Agent Code Review & DevSecOps Platform*")
st.markdown("---")

if not reviews:
    st.info(
        "👋 **No reviews yet!** Trigger a PR review to see data here.\n\n"
        "1. Set up the webhook on your GitHub repo pointing to `/webhook/github`\n"
        "2. Open a Pull Request\n"
        "3. Watch Git Guardian analyze it automatically!"
    )
    st.stop()


# ─── Key Metrics ──────────────────────────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)

total_findings = sum(r.total_findings for r in reviews)
total_critical = sum(r.critical_count for r in reviews)
total_high = sum(r.high_count for r in reviews)
avg_health = sum(r.code_health_score for r in reviews) / len(reviews) if reviews else 0
avg_duration = sum(r.review_duration_seconds for r in reviews) / len(reviews) if reviews else 0

with col1:
    st.metric("Total Reviews", len(reviews))
with col2:
    st.metric("Total Findings", total_findings)
with col3:
    st.metric("🔴 Critical Issues", total_critical)
with col4:
    st.metric("Avg Health Score", f"{avg_health:.0f}/100")
with col5:
    st.metric("Avg Review Time", f"{avg_duration:.1f}s")

st.markdown("---")


# ─── Charts ───────────────────────────────────────────────────────────────────

chart_col1, chart_col2 = st.columns(2)

# Severity breakdown pie chart
with chart_col1:
    st.subheader("📊 Severity Breakdown")
    
    severity_data = {
        "Critical": total_critical,
        "High": total_high,
        "Medium": sum(r.medium_count for r in reviews),
        "Low": sum(r.low_count for r in reviews),
        "Info": sum(r.info_count for r in reviews),
    }
    severity_data = {k: v for k, v in severity_data.items() if v > 0}
    
    if severity_data:
        fig_pie = px.pie(
            names=list(severity_data.keys()),
            values=list(severity_data.values()),
            color=list(severity_data.keys()),
            color_discrete_map={
                "Critical": "#ef4444",
                "High": "#f97316",
                "Medium": "#eab308",
                "Low": "#3b82f6",
                "Info": "#9ca3af",
            },
            hole=0.4,
        )
        fig_pie.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            margin=dict(t=20, b=20, l=20, r=20),
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No findings to display")


# Code health trend line chart
with chart_col2:
    st.subheader("📈 Code Health Trend")
    
    if len(reviews) >= 2:
        health_data = sorted(
            [(r.created_at, r.code_health_score, r.repo_full_name) for r in reviews],
            key=lambda x: x[0],
        )
        
        fig_trend = go.Figure()
        
        # Group by repo
        repos_in_data = set(d[2] for d in health_data)
        colors = px.colors.qualitative.Set2
        
        for i, repo in enumerate(repos_in_data):
            repo_data = [(d[0], d[1]) for d in health_data if d[2] == repo]
            dates, scores = zip(*repo_data)
            fig_trend.add_trace(go.Scatter(
                x=list(dates),
                y=list(scores),
                mode="lines+markers",
                name=repo.split("/")[-1],
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=6),
            ))
        
        fig_trend.update_layout(
            yaxis_title="Health Score",
            yaxis_range=[0, 105],
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            margin=dict(t=20, b=20, l=40, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("Need at least 2 reviews for trend data")

st.markdown("---")


# ─── Review turnaround time ──────────────────────────────────────────────────

st.subheader("⏱️ Review Turnaround Time")

if reviews:
    turnaround_data = sorted(
        [(r.created_at, r.review_duration_seconds, r.repo_full_name) for r in reviews],
        key=lambda x: x[0],
    )
    
    dates, durations, repo_names = zip(*turnaround_data)
    
    fig_time = go.Figure()
    fig_time.add_trace(go.Bar(
        x=list(dates),
        y=list(durations),
        marker_color=["#667eea" if d < avg_duration else "#ef4444" for d in durations],
        text=[f"{d:.1f}s" for d in durations],
        textposition="auto",
    ))
    fig_time.update_layout(
        yaxis_title="Duration (seconds)",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="white",
        margin=dict(t=20, b=20, l=40, r=20),
    )
    st.plotly_chart(fig_time, use_container_width=True)


st.markdown("---")


# ─── Recent Reviews Table ────────────────────────────────────────────────────

st.subheader("📋 Recent Reviews")

for r in reviews[:20]:
    # Health score emoji
    if r.code_health_score >= 80:
        health_emoji = "🟢"
    elif r.code_health_score >= 60:
        health_emoji = "🟡"
    elif r.code_health_score >= 40:
        health_emoji = "🟠"
    else:
        health_emoji = "🔴"
    
    with st.expander(
        f"{health_emoji} **{r.repo_full_name}** PR #{r.pr_number} — "
        f"Score: {r.code_health_score:.0f}/100 | "
        f"{r.total_findings} findings | "
        f"{r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else 'N/A'}"
    ):
        detail_cols = st.columns(6)
        with detail_cols[0]:
            st.metric("🔴 Critical", r.critical_count)
        with detail_cols[1]:
            st.metric("🟠 High", r.high_count)
        with detail_cols[2]:
            st.metric("🟡 Medium", r.medium_count)
        with detail_cols[3]:
            st.metric("🔵 Low", r.low_count)
        with detail_cols[4]:
            st.metric("⚪ Info", r.info_count)
        with detail_cols[5]:
            st.metric("⏱️ Duration", f"{r.review_duration_seconds:.1f}s")
        
        if r.auto_fix_branch:
            st.info(f"🔧 Auto-fix branch: `{r.auto_fix_branch}`")
        
        if r.findings_json:
            try:
                findings = json.loads(r.findings_json)
                if findings:
                    st.markdown("**Findings:**")
                    for f in findings[:10]:
                        sev = f.get("severity", "info")
                        emoji_map = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
                        st.markdown(
                            f"- {emoji_map.get(sev, '⚪')} **[{sev.upper()}]** "
                            f"`{f.get('file', '?')}:{f.get('line', '?')}` — "
                            f"{f.get('message', 'N/A')}"
                        )
                    if len(findings) > 10:
                        st.caption(f"... and {len(findings) - 10} more findings")
            except (json.JSONDecodeError, TypeError):
                pass

        st.markdown(f"**Commit:** `{r.commit_sha[:8] if r.commit_sha else 'N/A'}`")


# ─── Footer ───────────────────────────────────────────────────────────────────

st.markdown("---")
st.markdown(
    "*🛡️ Git Guardian AI — Multi-Agent Code Review & DevSecOps Platform | "
    "Powered by LangGraph, Groq, ChromaDB*"
)
