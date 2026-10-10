from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Mentor(Base):
    __tablename__ = "mentors"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pay_model: Mapped[str] = mapped_column(String(20), default="per_lesson")  # per_lesson | per_hour
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("500.00"))
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)  # согласие на распознавание лица
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))  # mentor | admin | manager
    mentor_id: Mapped[int | None] = mapped_column(ForeignKey("mentors.id"), nullable=True)


class Lesson(Base):
    """Урок по расписанию: кто, где, когда. Время — локальное время академии."""
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    mentor_id: Mapped[int] = mapped_column(ForeignKey("mentors.id"))
    room: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (Index("ix_lessons_mentor_start", "mentor_id", "starts_at"),)


class Sighting(Base):
    """Отметка камеры: ментор замечен."""
    __tablename__ = "sightings"
    id: Mapped[int] = mapped_column(primary_key=True)
    mentor_id: Mapped[int] = mapped_column(ForeignKey("mentors.id"))
    room: Mapped[str] = mapped_column(String(100))
    seen_at: Mapped[datetime] = mapped_column(DateTime)
    confidence: Mapped[float] = mapped_column(Float)
    liveness_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True, index=True)


class Attendance(Base):
    """Запись об уроке. Один урок — одна запись (UNIQUE lesson_id)."""
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id"), unique=True)
    mentor_id: Mapped[int] = mapped_column(ForeignKey("mentors.id"), index=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    present_seconds: Mapped[int] = mapped_column(Integer, default=0)
    required_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    # ставка «замораживается» в момент подтверждения — смена ставки не меняет прошлые начисления
    pay_model: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PeriodLock(Base):
    """Закрытый (выплаченный) месяц: правки запрещены."""
    __tablename__ = "period_locks"
    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    __table_args__ = (UniqueConstraint("year", "month", name="uq_period"),)


class AuditLog(Base):
    """Журнал изменений: кто и что исправил."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
