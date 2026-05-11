"""SQLAlchemy models and database setup for MariaDB."""
import os
import secrets
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from contextlib import contextmanager

from config import settings


# --- engine ----------------------------------------------------------------

# Subdirectories used for stored captures and user-uploaded photos
os.makedirs(settings.data_dir, exist_ok=True)
os.makedirs(os.path.join(settings.data_dir, "captures"), exist_ok=True)
os.makedirs(os.path.join(settings.data_dir, "images"), exist_ok=True)
os.makedirs(os.path.join(settings.data_dir, "sessions"), exist_ok=True)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


# --- models ----------------------------------------------------------------

class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(String(40), primary_key=True)
    title = Column(String(500), nullable=False)
    category = Column(String(50), nullable=False, default="other", index=True)
    url = Column(String(2000), nullable=True)

    ingredients = Column(Text, nullable=False, default="")
    instructions = Column(Text, nullable=False, default="")
    notes = Column(Text, nullable=False, default="")

    # User-uploaded photo (separate from auto-captures)
    image_filename = Column(String(255), nullable=True)

    # Auto-captures (filenames inside DATA_DIR/captures/<recipe_id>/)
    pdf_filename = Column(String(255), nullable=True)         # e.g. "page.pdf"
    screenshot_filename = Column(String(255), nullable=True)  # e.g. "page.png"
    captured_at = Column(DateTime, nullable=True)
    capture_source_url = Column(String(2000), nullable=True)

    added_by = Column(String(100), nullable=False, default="")
    share_token = Column(String(80), nullable=True, unique=True, index=True)
    date = Column(String(20), nullable=False,
                  default=lambda: datetime.utcnow().strftime("%Y-%m-%d"))

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "url": self.url,
            "ingredients": self.ingredients or "",
            "instructions": self.instructions or "",
            "notes": self.notes or "",
            "image_filename": self.image_filename,
            "image_url": (f"/api/images/{self.image_filename}"
                          if self.image_filename else None),
            "has_pdf": bool(self.pdf_filename),
            "has_screenshot": bool(self.screenshot_filename),
            "pdf_url": f"/api/recipes/{self.id}/pdf" if self.pdf_filename else None,
            "screenshot_url": f"/api/recipes/{self.id}/screenshot" if self.screenshot_filename else None,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "added_by": self.added_by or "",
            "share_token": self.share_token,
            "date": self.date,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        return d


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def new_share_token() -> str:
    return secrets.token_urlsafe(16)
