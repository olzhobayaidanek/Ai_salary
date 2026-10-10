from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["mentor", "admin", "manager"]
PayModel = Literal["per_lesson", "per_hour"]


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: Role
    mentor_id: int | None = None


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    role: Role
    mentor_id: int | None = None


class MentorIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: str | None = None
    pay_model: PayModel = "per_lesson"
    rate: float = Field(default=500, gt=0)


class MentorPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: str | None = None
    pay_model: PayModel | None = None
    rate: float | None = Field(default=None, gt=0)
    is_active: bool | None = None


class ConsentIn(BaseModel):
    given: bool


class MentorOut(BaseModel):
    id: int
    full_name: str
    email: str | None
    pay_model: str
    rate: float
    consent_given: bool
    consent_at: datetime | None
    is_active: bool


class LessonIn(BaseModel):
    mentor_id: int
    room: str = Field(min_length=1, max_length=100)
    title: str = ""
    starts_at: datetime
    ends_at: datetime


class LessonOut(BaseModel):
    id: int
    mentor_id: int
    mentor_name: str
    room: str
    title: str
    starts_at: datetime
    ends_at: datetime


class SightingIn(BaseModel):
    mentor_id: int
    room: str
    seen_at: datetime
    confidence: float = Field(ge=0, le=1)
    liveness_ok: bool = True


class AttendanceOut(BaseModel):
    id: int
    lesson_id: int
    mentor_id: int
    mentor_name: str
    room: str
    starts_at: datetime
    ends_at: datetime
    first_seen: datetime | None
    last_seen: datetime | None
    present_seconds: int
    required_seconds: int
    status: str
    reason: str
    note: str
    amount: float | None  # только для подтверждённых


class ReviewIn(BaseModel):
    decision: Literal["confirm", "reject"]
    note: str = ""


class ManualIn(BaseModel):
    lesson_id: int
    note: str = Field(min_length=3)


class PayrollRow(BaseModel):
    mentor_id: int
    mentor_name: str
    lessons: int
    hours: float
    amount: float


class PayrollReport(BaseModel):
    year: int
    month: int
    closed: bool
    rows: list[PayrollRow]
    total_lessons: int
    total_hours: float
    total_amount: float


class MySummary(BaseModel):
    year: int
    month: int
    lessons: int
    hours: float
    amount: float
    records: list[AttendanceOut]


class AuditOut(BaseModel):
    id: int
    user_id: int | None
    action: str
    entity: str
    entity_id: int | None
    details: str
    created_at: datetime
