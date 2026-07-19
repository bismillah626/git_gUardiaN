"""
Git Guardian AI — Streamlit Dashboard (Dark Mode).

Views:
  A) Live Pipeline Monitor — real-time agent status
  B) Review History & Branch Context — past reviews with trends
"""

import json, os, sys, logging, time as _time
from datetime import datetime, timedelta

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import (
    get_all_reviews, get_reviews_by_repo, init_db, SessionLocal,
    ReviewRecordDB, AgentRunStatusDB,
    get_agent_statuses, get_latest_in_progress_review, get_review_by_id,
)

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────

SEV_COLORS = {"critical":"#ef4444","high":"#f97316","medium":"#eab308","low":"#3b82f6","info":"#64748b"}
SEV_EMOJI = {"critical":"🔴","high":"🟠","medium":"🟡","low":"🔵","info":"⚪"}
STATUS_COLORS = {"queued":"#475569","running":"#6366f1","done":"#22c55e","failed":"#ef4444"}
STATUS_ICONS = {"queued":"⏳","running":"⚡","done":"✅","failed":"❌"}
AGENT_DISPLAY = {
    "security":("🔒","Security"),
    "quality":("✨","Quality"),
    "test_gap":("🧪","Test Gaps"),
    "documentation":("📝","Docs"),
}

# ─── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Git Guardian AI", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

# ─── Dark Theme CSS ────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ── Global ─────────────────────────────────────────── */
html, body, [class*="css"] { font-family: 'Inter', sans-serif !important; }
.stApp { background: #0a0a0f; }
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f0f1a 0%, #0a0a14 100%) !important;
    border-right: 1px solid rgba(124,58,237,0.15);
}
.block-container { padding: 2rem 3rem 3rem !important; max-width: 1400px; }

/* ── Header ─────────────────────────────────────────── */
.hero { margin-bottom: 2rem; }
.hero h1 {
    font-size: 2.4rem; font-weight: 800; margin: 0;
    background: linear-gradient(135deg, #a78bfa 0%, #7c3aed 40%, #6d28d9 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    letter-spacing: -0.03em;
}
.hero p { color: #94a3b8; font-size: 1rem; margin: 0.3rem 0 0; }

/* ── Divider ────────────────────────────────────────── */
.divider {
    height: 1px; margin: 1.5rem 0;
    background: linear-gradient(90deg, transparent, rgba(124,58,237,0.3), transparent);
}

/* ── Agent Card ─────────────────────────────────────── */
.agent-card {
    background: linear-gradient(145deg, #12121f 0%, #0e0e1a 100%);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 1.4rem 1.2rem; min-height: 160px;
    transition: all 0.25s cubic-bezier(.4,0,.2,1);
    position: relative; overflow: hidden;
}
.agent-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    border-radius: 16px 16px 0 0;
}
.agent-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 40px rgba(0,0,0,0.5);
    border-color: rgba(124,58,237,0.25);
}
.agent-card .icon { font-size: 1.6rem; margin-bottom: 0.4rem; display: block; }
.agent-card .name { font-size: 0.95rem; font-weight: 700; color: #e2e8f0; margin-bottom: 0.7rem; }
.agent-card .badge {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 12px; border-radius: 20px;
    font-size: 0.72rem; font-weight: 700; color: #fff;
    letter-spacing: 0.05em; text-transform: uppercase;
}
.agent-card .msg { font-size: 0.82rem; color: #94a3b8; margin-top: 0.6rem; line-height: 1.4; }
.agent-card .elapsed { font-size: 0.75rem; color: #64748b; margin-top: 0.4rem; }

/* Status-specific card borders */
.card-queued::before { background: #475569; }
.card-running::before { background: linear-gradient(90deg, #6366f1, #a78bfa); }
.card-done::before { background: #22c55e; }
.card-failed::before { background: #ef4444; }

/* Running pulse */
.card-running { animation: pulse-border 2s ease-in-out infinite; }
@keyframes pulse-border {
    0%,100% { border-color: rgba(99,102,241,0.15); box-shadow: 0 0 0 rgba(99,102,241,0); }
    50% { border-color: rgba(99,102,241,0.35); box-shadow: 0 0 20px rgba(99,102,241,0.08); }
}

/* ── Summary Strip ──────────────────────────────────── */
.summary-strip {
    background: linear-gradient(135deg, rgba(34,197,94,0.08) 0%, rgba(99,102,241,0.08) 100%);
    border: 1px solid rgba(34,197,94,0.2); border-radius: 16px;
    padding: 1.2rem 1.8rem; color: #e2e8f0; font-size: 0.92rem;
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem;
}
.summary-strip a { color: #a78bfa; text-decoration: none; font-weight: 600; }
.summary-strip a:hover { text-decoration: underline; }

/* ── Metric Cards ───────────────────────────────────── */
[data-testid="stMetric"] {
    background: linear-gradient(145deg, #12121f, #0e0e1a) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 14px !important; padding: 1rem 1.2rem !important;
    transition: all 0.2s ease;
}
[data-testid="stMetric"]:hover {
    border-color: rgba(124,58,237,0.25) !important;
    transform: translateY(-2px);
}
[data-testid="stMetricLabel"] { color: #94a3b8 !important; font-size: 0.82rem !important; }
[data-testid="stMetricValue"] { color: #e2e8f0 !important; font-weight: 700 !important; }

/* ── Expanders ──────────────────────────────────────── */
.streamlit-expanderHeader {
    background: #12121f !important; border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    font-size: 0.9rem !important; padding: 0.8rem 1rem !important;
    transition: all 0.2s ease;
}
.streamlit-expanderHeader:hover {
    border-color: rgba(124,58,237,0.3) !important;
    background: #161625 !important;
}
.streamlit-expanderContent {
    background: #0c0c16 !important; border-radius: 0 0 12px 12px !important;
    border: 1px solid rgba(255,255,255,0.04) !important; border-top: none !important;
}

/* ── Progress Bar ───────────────────────────────────── */
.stProgress > div > div {
    background: linear-gradient(90deg, #6366f1, #a78bfa) !important;
    border-radius: 8px !important;
}
.stProgress > div { background: #1a1a2e !important; border-radius: 8px !important; }

/* ── Selectbox & Radio ──────────────────────────────── */
.stSelectbox > div > div { background: #12121f !important; border-color: rgba(255,255,255,0.08) !important; }
.stRadio > div { gap: 0.3rem; }

/* ── Sidebar polish ─────────────────────────────────── */
section[data-testid="stSidebar"] .stMarkdown h1 {
    font-size: 1.3rem !important; font-weight: 800 !important;
    background: linear-gradient(135deg, #a78bfa, #7c3aed);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}

/* ── Plotly dark ────────────────────────────────────── */
.js-plotly-plot { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Init ──────────────────────────────────────────────────────────────────────

try:
    init_db()
except Exception:
    pass

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("# 🛡️ Git Guardian AI")
    st.caption("Multi-Agent Code Review Platform")
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    view = st.radio("Navigation", ["🔴  Live Pipeline", "📋  Review History"], index=0, label_visibility="collapsed")

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    try:
        all_reviews = get_all_reviews(limit=500)
    except Exception as e:
        st.error(f"DB: {e}")
        all_reviews = []

    repos = sorted(set(r.repo_full_name for r in all_reviews)) if all_reviews else []

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Reviews", len(all_reviews))
    with c2:
        st.metric("Repos", len(repos))


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW A — LIVE PIPELINE MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

def _agent_card_html(agent_name, status_row):
    icon, name = AGENT_DISPLAY.get(agent_name, ("🤖", agent_name))
    status = status_row.status if status_row else "queued"
    msg = (status_row.status_message if status_row else "Waiting...") or "—"
    color = STATUS_COLORS.get(status, "#475569")
    s_icon = STATUS_ICONS.get(status, "⏳")
    elapsed = ""
    if status_row and status_row.started_at:
        end = status_row.completed_at or datetime.utcnow()
        elapsed = f'<div class="elapsed">⏱ {(end - status_row.started_at).total_seconds():.1f}s</div>'
    return f"""
    <div class="agent-card card-{status}">
        <span class="icon">{icon}</span>
        <div class="name">{name}</div>
        <span class="badge" style="background:{color};">{s_icon} {status.upper()}</span>
        <div class="msg">{msg}</div>
        {elapsed}
    </div>"""


def render_live_monitor():
    st.markdown('<div class="hero"><h1>🔴 Live Pipeline Monitor</h1><p>Watch agents analyze your PR in real-time</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    in_progress = get_latest_in_progress_review()
    recent = get_all_reviews(limit=20)
    opts = []
    for r in recent:
        lbl = f"#{r.id} — {r.repo_full_name} PR #{r.pr_number}"
        if in_progress and r.id == in_progress.id:
            lbl += "  🔴 LIVE"
        opts.append((lbl, r.id))

    if not opts:
        st.info("👋 **No reviews yet.** Trigger one via `POST /review` to see live progress.")
        return

    default_idx = 0
    if in_progress:
        for i, (_, rid) in enumerate(opts):
            if rid == in_progress.id:
                default_idx = i
                break

    sel = st.selectbox("🎯 Select Review", [o[0] for o in opts], index=default_idx)
    sel_id = opts[[o[0] for o in opts].index(sel)][1]

    review = get_review_by_id(sel_id)
    if not review:
        st.error("Review not found.")
        return

    statuses = get_agent_statuses(sel_id)
    smap = {s.agent_name: s for s in statuses}
    agents = ["security", "quality", "test_gap", "documentation"]
    terminal = {"done", "failed"}
    done_count = sum(1 for a in agents if smap.get(a) and smap[a].status in terminal)
    is_running = done_count < len(agents) and len(statuses) > 0

    # Context bar
    ctx_parts = [f"**{review.repo_full_name}** PR #{review.pr_number}"]
    if review.head_branch:
        ctx_parts.append(f"`{review.head_branch}`")
    if review.pr_title and review.pr_title != "Manual review":
        ctx_parts.append(f"*{review.pr_title}*")
    st.markdown(" · ".join(ctx_parts))

    # Progress
    if statuses:
        lbl = "🔄 Running..." if is_running else "✅ Complete"
        st.markdown(f"**{lbl}** — {done_count}/{len(agents)} agents finished")
        st.progress(done_count / len(agents))
    else:
        st.warning("No agent tracking data for this review (created before live tracking).")

    st.markdown("")

    # Agent cards
    cols = st.columns(4, gap="medium")
    for i, a in enumerate(agents):
        with cols[i]:
            st.markdown(_agent_card_html(a, smap.get(a)), unsafe_allow_html=True)

    # Completion summary
    if not is_running and statuses and review.total_findings is not None:
        score = review.code_health_score or 0
        he = "🟢" if score >= 80 else "🟡" if score >= 60 else "🟠" if score >= 40 else "🔴"
        link = f'<a href="{review.pr_url}" target="_blank">View PR ↗</a>' if review.pr_url else ""
        st.markdown(f"""
        <div class="summary-strip" style="margin-top:1.5rem;">
            <span>✅ <strong>Review Complete</strong></span>
            <span>│</span>
            <span>Health: {he} <strong>{score:.0f}/100</strong></span>
            <span>│</span>
            <span>🔴 {review.critical_count or 0} &nbsp; 🟠 {review.high_count or 0} &nbsp; 🟡 {review.medium_count or 0} &nbsp; 🔵 {review.low_count or 0} &nbsp; ⚪ {review.info_count or 0}</span>
            <span>│</span>
            <span>⏱ {review.review_duration_seconds:.1f}s</span>
            {f'<span>│</span><span>{link}</span>' if link else ''}
        </div>""", unsafe_allow_html=True)

        # Show findings inline
        if review.findings_json:
            st.markdown("")
            with st.expander("📋 View Findings Breakdown", expanded=False):
                _render_findings(review.findings_json)

    if is_running:
        _time.sleep(3)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# VIEW B — REVIEW HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def _get_autofix_state(repo, branch):
    try:
        from app.core.github_client import GitHubClient
        gh = GitHubClient()
        r = gh.get_repo(repo)
        pulls = r.get_pulls(state="all", head=f"{repo.split('/')[0]}:{branch}")
        for pr in pulls:
            if pr.merged: return "merged", pr.html_url
            elif pr.state == "closed": return "closed", pr.html_url
            else: return "open", pr.html_url
        try:
            r.get_branch(branch)
            return "branch_only", None
        except Exception:
            return "deleted", None
    except Exception:
        return "unknown", None


def _render_findings(findings_json):
    try:
        findings = json.loads(findings_json) if findings_json else []
    except (json.JSONDecodeError, TypeError):
        findings = []
    if not findings:
        st.info("No findings.")
        return

    by_agent = {}
    for f in findings:
        by_agent.setdefault(f.get("agent", "unknown"), []).append(f)

    for agent, af in by_agent.items():
        icon, title = AGENT_DISPLAY.get(agent, ("🤖", agent.capitalize()))
        st.markdown(f"#### {icon} {title}  `{len(af)}`")
        for f in af:
            sev = f.get("severity", "info")
            color = SEV_COLORS.get(sev, "#64748b")
            loc = f.get("file", "?")
            if f.get("line", 0) > 0:
                loc += f":{f['line']}"
            st.markdown(
                f'<span style="color:{color};font-weight:700;">[{sev.upper()}]</span> '
                f'`{loc}` — {f.get("message", "")}',
                unsafe_allow_html=True,
            )
            if f.get("source_tool") and agent == "security":
                st.caption(f"🔧 `{f['source_tool']}` · Rule: `{f.get('rule_id','N/A')}`")
            if f.get("suggested_fix"):
                with st.expander("💡 Suggested fix", expanded=False):
                    st.code(f["suggested_fix"][:500])


def render_history():
    st.markdown('<div class="hero"><h1>📋 Review History</h1><p>Browse past reviews, track code health, and monitor branches</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    repo_filter = st.selectbox("📁 Repository", ["All"] + repos, index=0)
    reviews = [r for r in all_reviews if repo_filter == "All" or r.repo_full_name == repo_filter]

    if not reviews:
        st.info("👋 **No reviews yet.** Trigger a PR review to see data here.")
        return

    # ── Metrics row ────────────────────────────────────────────────────────
    total_f = sum(r.total_findings for r in reviews)
    total_c = sum(r.critical_count for r in reviews)
    avg_h = sum(r.code_health_score for r in reviews) / len(reviews)
    avg_d = sum(r.review_duration_seconds for r in reviews) / len(reviews)

    m = st.columns(5, gap="medium")
    m[0].metric("Reviews", len(reviews))
    m[1].metric("Findings", total_f)
    m[2].metric("🔴 Critical", total_c)
    m[3].metric("Avg Health", f"{avg_h:.0f}/100")
    m[4].metric("Avg Time", f"{avg_d:.1f}s")

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Charts ─────────────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["📊 Severity", "📈 Health Trend", "⏱️ Turnaround"])

    plot_layout = dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#94a3b8", family="Inter"), margin=dict(t=30, b=30, l=40, r=20),
    )

    with tab1:
        sd = {"Critical": total_c, "High": sum(r.high_count for r in reviews),
              "Medium": sum(r.medium_count for r in reviews), "Low": sum(r.low_count for r in reviews),
              "Info": sum(r.info_count for r in reviews)}
        sd = {k: v for k, v in sd.items() if v > 0}
        if sd:
            fig = px.pie(names=list(sd.keys()), values=list(sd.values()), color=list(sd.keys()),
                         color_discrete_map={"Critical":"#ef4444","High":"#f97316","Medium":"#eab308","Low":"#3b82f6","Info":"#64748b"},
                         hole=0.45)
            fig.update_layout(**plot_layout)
            fig.update_traces(textfont_color="#e2e8f0")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No findings to display")

    with tab2:
        if len(reviews) >= 2:
            hd = sorted([(r.created_at, r.code_health_score, r.repo_full_name) for r in reviews], key=lambda x: x[0])
            fig = go.Figure()
            colors = ["#a78bfa","#22c55e","#f97316","#3b82f6","#ec4899"]
            for i, repo in enumerate(set(d[2] for d in hd)):
                rd = [(d[0], d[1]) for d in hd if d[2] == repo]
                dates, scores = zip(*rd)
                fig.add_trace(go.Scatter(x=list(dates), y=list(scores), mode="lines+markers",
                    name=repo.split("/")[-1], line=dict(color=colors[i%len(colors)], width=2.5),
                    marker=dict(size=7)))
            fig.update_layout(yaxis_title="Score", yaxis_range=[0, 105],
                              legend=dict(orientation="h", yanchor="bottom", y=1.02), **plot_layout)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Need ≥ 2 reviews for a trend")

    with tab3:
        if reviews:
            td = sorted([(r.created_at, r.review_duration_seconds, r.repo_full_name) for r in reviews], key=lambda x: x[0])
            dates, durs, _ = zip(*td)
            fig = go.Figure(go.Bar(x=list(dates), y=list(durs),
                marker_color=["#6366f1" if d < avg_d else "#ef4444" for d in durs],
                text=[f"{d:.1f}s" for d in durs], textposition="auto", textfont=dict(color="#e2e8f0")))
            fig.update_layout(yaxis_title="Seconds", **plot_layout)
            st.plotly_chart(fig, use_container_width=True)

    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

    # ── Reviews list ───────────────────────────────────────────────────────
    st.markdown("### 📋 Recent Reviews")

    for r in reviews[:30]:
        he = "🟢" if r.code_health_score >= 80 else "🟡" if r.code_health_score >= 60 else "🟠" if r.code_health_score >= 40 else "🔴"
        branch = f" · `{r.head_branch}`" if r.head_branch else ""
        title = f" — {r.pr_title}" if r.pr_title else ""
        ts = r.created_at.strftime('%b %d, %H:%M') if r.created_at else '?'

        with st.expander(f"{he} **{r.repo_full_name}** #{r.pr_number}{title} · {r.code_health_score:.0f}/100 · {r.total_findings} findings{branch} · {ts}"):
            dc = st.columns(7, gap="small")
            dc[0].metric("🔴 Critical", r.critical_count)
            dc[1].metric("🟠 High", r.high_count)
            dc[2].metric("🟡 Medium", r.medium_count)
            dc[3].metric("🔵 Low", r.low_count)
            dc[4].metric("⚪ Info", r.info_count)
            dc[5].metric("⏱ Duration", f"{r.review_duration_seconds:.1f}s")
            with dc[6]:
                if r.pr_url:
                    st.link_button("View PR ↗", r.pr_url)
                st.caption(f"`{r.commit_sha[:8] if r.commit_sha else '?'}`")

            if r.auto_fix_branch:
                state, pr_url = _get_autofix_state(r.repo_full_name, r.auto_fix_branch)
                labels = {"open":"🟢 Open","merged":"✅ Merged","closed":"🔴 Closed",
                          "branch_only":"🟡 No PR","deleted":"⚪ Deleted","unknown":"❓ Unknown"}
                link = f" — [View ↗]({pr_url})" if pr_url else ""
                st.info(f"🔧 `{r.auto_fix_branch}` → {labels.get(state, state)}{link}")

            if r.findings_json:
                _render_findings(r.findings_json)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

if "Live" in view:
    render_live_monitor()
else:
    render_history()

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
st.caption("🛡️ Git Guardian AI — Powered by LangGraph, Groq, ChromaDB")
