from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.db_url, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    gem_entries: Mapped[list["GemPrice"]] = relationship(back_populates="snapshot")
    market_entries: Mapped[list["MarketPrice"]] = relationship(back_populates="snapshot")


class GemPrice(Base):
    """
    gem_profit.py 결과를 스냅샷 단위로 저장
    """

    __tablename__ = "gem_prices"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "name", "sell_level", name="uq_gem_snapshot_name_level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id", ondelete="CASCADE"))

    name: Mapped[str] = mapped_column(String(255), index=True)
    sell_level: Mapped[int] = mapped_column(Integer)

    buy_chaos: Mapped[float] = mapped_column(Float)
    sell_chaos: Mapped[float] = mapped_column(Float)
    profit_chaos: Mapped[float] = mapped_column(Float)
    profit_divine: Mapped[float] = mapped_column(Float)

    buy_divine: Mapped[float] = mapped_column(Float)
    sell_divine: Mapped[float] = mapped_column(Float)

    buy_listing: Mapped[int] = mapped_column(Integer)
    sell_listing: Mapped[int] = mapped_column(Integer)

    snapshot: Mapped[Snapshot] = relationship(back_populates="gem_entries")


class MarketPrice(Base):
    """
    화폐 / 스카라브 / 카드 가격 스냅샷
    """

    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "category", "name", name="uq_market_snapshot_cat_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("snapshots.id", ondelete="CASCADE"))

    # currency / scarab / divination-card
    category: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)

    chaos_value: Mapped[float] = mapped_column(Float)
    divine_value: Mapped[float] = mapped_column(Float)

    # poe.ninja 의 상세 id (있으면)
    icon: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    details_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 베이스 타입 전용 (levelRequired)
    item_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    snapshot: Mapped[Snapshot] = relationship(back_populates="market_entries")


class Threshold(Base):
    """
    가격 변동 임계값 관리
    - category: global / currency / scarab / divination-card / gem
    - name: 옵션 (None 이면 카테고리 전체에 적용)
    """

    __tablename__ = "thresholds"
    __table_args__ = (
        UniqueConstraint("category", "name", name="uq_threshold_category_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(50))
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    threshold_percent: Mapped[float] = mapped_column(Float)
    # 절대값 임계값 (카오스) — None 이면 전역 기본값 사용
    chaos_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class User(Base):
    """관리자 계정"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))


def init_db() -> None:
    from sqlalchemy import text
    Base.metadata.create_all(bind=engine)
    # 기존 DB에 item_level 컬럼 없으면 추가 (마이그레이션)
    with engine.connect() as conn:
        for ddl in [
            "ALTER TABLE market_prices ADD COLUMN item_level INTEGER",
            "ALTER TABLE thresholds ADD COLUMN chaos_threshold REAL",
        ]:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                pass  # 이미 존재하는 경우 무시


def init_admin() -> None:
    """admin 계정이 없으면 기본값으로 생성"""
    from werkzeug.security import generate_password_hash

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").one_or_none()
        if not existing:
            db.add(User(username="admin", password_hash=generate_password_hash("6301")))
            db.commit()
    finally:
        db.close()

