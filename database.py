"""
Database engine and session factory.
Uses PostgreSQL when DATABASE_URL is set, SQLite otherwise.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_DATABASE_URL = os.getenv("DATABASE_URL", "")

if _DATABASE_URL:
    # Render provides postgres:// — SQLAlchemy needs postgresql://
    if _DATABASE_URL.startswith("postgres://"):
        _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
else:
    _db_path = os.path.join(os.path.dirname(__file__), "local.db")
    engine = create_engine(
        f"sqlite:///{_db_path}",
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
