"""
Database engine, session, and table definitions for Git Guardian AI.

Uses SQLAlchemy 2.0 style with psycopg2 (sync) for Streamlit dashboard
and general operations.
"""

import json
from datetime import datetime
from typing import Optional, List

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# ─── Engine & Session ──────────────────────────────────────────────────────────

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


# ─── ORM Model ────────────────────────────────────────────────────────────────

class ReviewRecordDB(Base):
    """Persisted review record for the dashboard."""
    __tablename__ = "review_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_full_name = Column(String(255), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False)
    commit_sha = Column(String(40), nullable=False)
    total_findings = Column(Integer, default=0)
    critical_count = Column(Integer, default=0)
    high_count = Column(Integer, default=0)
    medium_count = Column(Integer, default=0)
    low_count = Column(Integer, default=0)
    info_count = Column(Integer, default=0)
    code_health_score = Column(Float, default=0.0)
    review_duration_seconds = Column(Float, default=0.0)
    auto_fix_branch = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    findings_json = Column(Text, nullable=True)  # JSON blob of all findings


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
