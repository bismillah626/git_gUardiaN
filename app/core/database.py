"""
Database engine, session, and table definitions for Git Guardian AI.

Uses SQLAlchemy 2.0 style with psycopg2 (sync) for Streamlit dashboard
and general operations.
"""

import json
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Text,
    ForeignKey, Enum as SAEnum,
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

from app.core.config import settings

# ─── Engine & Session ──────────────────────────────────────────────────────────

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


# ─── ORM Models ───────────────────────────────────────────────────────────────

class ReviewRecordDB(Base):
    """Persisted review record for the dashboard."""
    __tablename__ = "review_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_full_name = Column(String(255), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False)
    commit_sha = Column(String(40), nullable=False)
    head_branch = Column(String(255), nullable=True)
    pr_url = Column(String(512), nullable=True)
    pr_title = Column(String(512), nullable=True)
    total_findings = Column(Integer, default=0)
    critical_count = Column(Integer, default=0)
    high_count = Column(Integer, default=0)
    medium_count = Column(Integer, default=0)
    low_count = Column(Integer, default=0)
    info_count = Column(Integer, default=0)
    code_health_score = Column(Float, default=0.0)
    review_duration_seconds = Column(Float, default=0.0)
    review_summary = Column(Text, nullable=True)
    auto_fix_branch = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    findings_json = Column(Text, nullable=True)  # JSON blob of all findings

    # Relationship to agent statuses
    agent_statuses = relationship("AgentRunStatusDB", back_populates="review", cascade="all, delete-orphan")


class AgentRunStatusDB(Base):
    """Tracks per-agent status during an in-progress review."""
    __tablename__ = "agent_run_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    review_id = Column(Integer, ForeignKey("review_records.id"), nullable=False, index=True)
    agent_name = Column(String(50), nullable=False)  # security, quality, test_gap, documentation
    status = Column(String(20), nullable=False, default="queued")  # queued / running / done / failed
    status_message = Column(String(512), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    review = relationship("ReviewRecordDB", back_populates="agent_statuses")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Yield a database session (for FastAPI dependency injection)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_review(review_data: dict) -> ReviewRecordDB:
    """Persist a review record to Postgres."""
    db = SessionLocal()
    try:
        record = ReviewRecordDB(**review_data)
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    finally:
        db.close()


def get_all_reviews(limit: int = 100) -> list:
    """Fetch recent review records for the dashboard."""
    db = SessionLocal()
    try:
        return (
            db.query(ReviewRecordDB)
            .order_by(ReviewRecordDB.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def get_reviews_by_repo(repo_full_name: str, limit: int = 50) -> list:
    """Fetch review records for a specific repo."""
    db = SessionLocal()
    try:
        return (
            db.query(ReviewRecordDB)
            .filter(ReviewRecordDB.repo_full_name == repo_full_name)
            .order_by(ReviewRecordDB.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


# ─── Live Agent Status Helpers ─────────────────────────────────────────────────

def create_review_placeholder(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    head_branch: str = "",
    pr_url: str = "",
    pr_title: str = "",
) -> int:
    """Create a placeholder review record at the start of a pipeline run.

    Returns the review_id for tracking agent statuses.
    """
    db = SessionLocal()
    try:
        record = ReviewRecordDB(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_sha=head_sha or "pending",
            head_branch=head_branch,
            pr_url=pr_url,
            pr_title=pr_title,
            code_health_score=0.0,
            total_findings=0,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record.id
    finally:
        db.close()


def init_agent_statuses(review_id: int) -> None:
    """Create initial 'queued' status rows for all four agents."""
    db = SessionLocal()
    try:
        for agent_name in ["security", "quality", "test_gap", "documentation"]:
            status_row = AgentRunStatusDB(
                review_id=review_id,
                agent_name=agent_name,
                status="queued",
                status_message="Waiting in queue",
            )
            db.add(status_row)
        db.commit()
    finally:
        db.close()


def update_agent_status(
    review_id: int,
    agent_name: str,
    status: str,
    status_message: str = "",
) -> None:
    """Update an agent's status row (upsert-style)."""
    db = SessionLocal()
    try:
        row = (
            db.query(AgentRunStatusDB)
            .filter_by(review_id=review_id, agent_name=agent_name)
            .first()
        )
        if row:
            row.status = status
            row.status_message = status_message
            if status == "running" and row.started_at is None:
                row.started_at = datetime.utcnow()
            if status in ("done", "failed"):
                row.completed_at = datetime.utcnow()
        else:
            row = AgentRunStatusDB(
                review_id=review_id,
                agent_name=agent_name,
                status=status,
                status_message=status_message,
                started_at=datetime.utcnow() if status == "running" else None,
                completed_at=datetime.utcnow() if status in ("done", "failed") else None,
            )
            db.add(row)
        db.commit()
    finally:
        db.close()


def finalize_review_record(review_id: int, review_data: dict) -> None:
    """Update the placeholder review record with final results."""
    db = SessionLocal()
    try:
        record = db.query(ReviewRecordDB).filter_by(id=review_id).first()
        if record:
            for key, value in review_data.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            db.commit()
    finally:
        db.close()


def get_agent_statuses(review_id: int) -> List[AgentRunStatusDB]:
    """Get all agent status rows for a given review."""
    db = SessionLocal()
    try:
        return (
            db.query(AgentRunStatusDB)
            .filter_by(review_id=review_id)
            .order_by(AgentRunStatusDB.agent_name)
            .all()
        )
    finally:
        db.close()


def get_latest_in_progress_review() -> Optional[ReviewRecordDB]:
    """Get the most recent review that still has agents not in done/failed state."""
    db = SessionLocal()
    try:
        from sqlalchemy import select, exists, and_
        # Use a proper exists() subquery
        subq = (
            select(AgentRunStatusDB.id)
            .where(
                and_(
                    AgentRunStatusDB.review_id == ReviewRecordDB.id,
                    AgentRunStatusDB.status.in_(["queued", "running"]),
                )
            )
            .exists()
        )
        result = (
            db.query(ReviewRecordDB)
            .filter(subq)
            .order_by(ReviewRecordDB.created_at.desc())
            .first()
        )
        return result
    except Exception:
        return None
    finally:
        db.close()


def get_review_by_id(review_id: int) -> Optional[ReviewRecordDB]:
    """Get a single review record by ID."""
    db = SessionLocal()
    try:
        return db.query(ReviewRecordDB).filter_by(id=review_id).first()
    finally:
        db.close()
