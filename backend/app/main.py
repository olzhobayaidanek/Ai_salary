"""AI Mentor — Attendance & Payroll: FastAPI backend.

Запуск:  uvicorn backend.app.main:app --reload
Документация API: http://localhost:8000/docs
"""
import csv
import hmac
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import rules, schemas
from .config import settings
from .db import get_db, init_db
from .models import Attendance, AuditLog, Lesson, Mentor, PeriodLock, Sighting, User
from .security import create_token, decode_token, hash_password, verify_password

ADMIN, MANAGER, MENTOR = "admin", "manager", "mentor"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Mentor — Attendance & Payroll", version="0.1.0", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------ доступ

def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(401, "Требуется вход")
    try:
        data = decode_token(creds.credentials)
        user = db.get(User, int(data["sub"]))
    except (ValueError, KeyError):
        raise HTTPException(401, "Недействительный или просроченный токен")
    if user is None:
        raise HTTPException(401, "Пользователь не найден")
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Недостаточно прав")
        return user

    return dependency


def camera_key(x_api_key: str | None = Header(default=None)) -> None:
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.camera_api_key):
        raise HTTPException(401, "Неверный ключ камеры")


def admin_or_camera(
    x_api_key: str | None = Header(default=None),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if x_api_key and hmac.compare_digest(x_api_key, settings.camera_api_key):
        return None
    user = current_user(creds, db)
    if user.role != ADMIN:
        raise HTTPException(403, "Недостаточно прав")
    return user


# ------------------------------------------------------------------ помощники

def rules_config() -> rules.Rules:
    return rules.Rules(
        presence_ratio=settings.presence_ratio,
        edge_seconds=settings.edge_seconds,
        max_gap_seconds=settings.max_gap_seconds,
        min_confidence=settings.min_confidence,
        review_confidence=settings.review_confidence,
        max_doubtful_share=settings.max_doubtful_share,
    )


def audit(db: Session, user_id: int | None, action: str, entity: str, entity_id: int | None, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity=entity, entity_id=entity_id,
                    details=json.dumps(details, ensure_ascii=False, default=str)))


def bounds_or_422(year: int, month: int):
    try:
        return rules.month_bounds(year, month)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def is_closed(db: Session, when: datetime) -> bool:
    return db.scalar(select(PeriodLock.id).where(PeriodLock.year == when.year, PeriodLock.month == when.month)) is not None


def ensure_open(db: Session, when: datetime) -> None:
    if is_closed(db, when):
        raise HTTPException(409, f"Период {when.month:02d}.{when.year} закрыт после выплаты — правки запрещены")


def mentor_out(m: Mentor) -> schemas.MentorOut:
    return schemas.MentorOut(id=m.id, full_name=m.full_name, email=m.email, pay_model=m.pay_model,
                             rate=float(m.rate), consent_given=m.consent_given, consent_at=m.consent_at,
                             is_active=m.is_active)


def lesson_out(lesson: Lesson, mentor: Mentor) -> schemas.LessonOut:
    return schemas.LessonOut(id=lesson.id, mentor_id=lesson.mentor_id, mentor_name=mentor.full_name,
                             room=lesson.room, title=lesson.title, starts_at=lesson.starts_at, ends_at=lesson.ends_at)


def attendance_out(att: Attendance, lesson: Lesson, mentor: Mentor) -> schemas.AttendanceOut:
    amount = None
    if att.status == rules.CONFIRMED and att.rate is not None:
        amount = float(rules.lesson_amount(att.pay_model, att.rate, lesson.starts_at, lesson.ends_at))
    return schemas.AttendanceOut(
        id=att.id, lesson_id=att.lesson_id, mentor_id=att.mentor_id, mentor_name=mentor.full_name,
        room=lesson.room, starts_at=lesson.starts_at, ends_at=lesson.ends_at,
        first_seen=att.first_seen, last_seen=att.last_seen, present_seconds=att.present_seconds,
        required_seconds=att.required_seconds, status=att.status, reason=att.reason, note=att.note, amount=amount)


def month_rows(db: Session, year: int, month: int, mentor_id: int | None = None, status: str | None = None):
    start, end = bounds_or_422(year, month)
    q = (select(Attendance, Lesson, Mentor)
         .join(Lesson, Attendance.lesson_id == Lesson.id)
         .join(Mentor, Attendance.mentor_id == Mentor.id)
         .where(Lesson.starts_at >= start, Lesson.starts_at < end)
         .order_by(Lesson.starts_at))
    if mentor_id is not None:
        q = q.where(Attendance.mentor_id == mentor_id)
    if status:
        q = q.where(Attendance.status == status)
    return db.execute(q).all()


def build_payroll(db: Session, year: int, month: int, mentor_id: int | None = None) -> list[schemas.PayrollRow]:
    """Зарплата = подтверждённые уроки × ставка (ставка зафиксирована при подтверждении)."""
    mq = select(Mentor).where(Mentor.is_active.is_(True))
    if mentor_id is not None:
        mq = mq.where(Mentor.id == mentor_id)
    agg: dict[int, dict] = {
        m.id: {"name": m.full_name, "lessons": 0, "hours": Decimal(0), "amount": Decimal(0)}
        for m in db.scalars(mq).all()
    }
    for att, lesson, mentor in month_rows(db, year, month, mentor_id=mentor_id, status=rules.CONFIRMED):
        row = agg.setdefault(mentor.id, {"name": mentor.full_name, "lessons": 0, "hours": Decimal(0), "amount": Decimal(0)})
        row["lessons"] += 1
        row["hours"] += rules.lesson_hours(lesson.starts_at, lesson.ends_at)
        row["amount"] += rules.lesson_amount(att.pay_model, att.rate, lesson.starts_at, lesson.ends_at)
    return [
        schemas.PayrollRow(mentor_id=mid, mentor_name=r["name"], lessons=r["lessons"],
                           hours=float(r["hours"]), amount=float(r["amount"]))
        for mid, r in sorted(agg.items(), key=lambda kv: kv[1]["name"])
    ]


# ------------------------------------------------------------------ служебные

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login", response_model=schemas.LoginOut)
def login(body: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    token = create_token({"sub": str(user.id), "role": user.role})
    return schemas.LoginOut(access_token=token, username=user.username, role=user.role, mentor_id=user.mentor_id)


@app.get("/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role, "mentor_id": user.mentor_id}


@app.post("/users", status_code=201)
def create_user(body: schemas.UserIn, db: Session = Depends(get_db), admin: User = Depends(require_roles(ADMIN))):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "Такой логин уже есть")
    if body.role == MENTOR:
        if body.mentor_id is None or db.get(Mentor, body.mentor_id) is None:
            raise HTTPException(422, "Для роли mentor укажите существующий mentor_id")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role,
                mentor_id=body.mentor_id if body.role == MENTOR else None)
    db.add(user)
    db.flush()
    audit(db, admin.id, "user.create", "user", user.id, {"username": user.username, "role": user.role})
    db.commit()
    return {"id": user.id, "username": user.username, "role": user.role}


# ------------------------------------------------------------------ менторы

@app.get("/mentors", response_model=list[schemas.MentorOut])
def list_mentors(db: Session = Depends(get_db), _: User = Depends(require_roles(ADMIN, MANAGER))):
    return [mentor_out(m) for m in db.scalars(select(Mentor).order_by(Mentor.full_name)).all()]


@app.post("/mentors", response_model=schemas.MentorOut, status_code=201)
def create_mentor(body: schemas.MentorIn, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(ADMIN, MANAGER))):
    m = Mentor(full_name=body.full_name, email=body.email, pay_model=body.pay_model, rate=Decimal(str(body.rate)))
    db.add(m)
    db.flush()
    audit(db, user.id, "mentor.create", "mentor", m.id, {"full_name": m.full_name, "rate": body.rate})
    db.commit()
    return mentor_out(m)


@app.patch("/mentors/{mentor_id}", response_model=schemas.MentorOut)
def update_mentor(mentor_id: int, body: schemas.MentorPatch, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(ADMIN, MANAGER))):
    m = db.get(Mentor, mentor_id)
    if m is None:
        raise HTTPException(404, "Ментор не найден")
    changes = body.model_dump(exclude_unset=True)
    before = {k: str(getattr(m, k)) for k in changes}
    for key, value in changes.items():
        setattr(m, key, Decimal(str(value)) if key == "rate" else value)
    audit(db, user.id, "mentor.update", "mentor", m.id, {"before": before, "after": {k: str(v) for k, v in changes.items()}})
    db.commit()
    return mentor_out(m)


@app.post("/mentors/{mentor_id}/consent", response_model=schemas.MentorOut)
def set_consent(mentor_id: int, body: schemas.ConsentIn, db: Session = Depends(get_db),
                user: User = Depends(require_roles(ADMIN, MANAGER))):
    m = db.get(Mentor, mentor_id)
    if m is None:
        raise HTTPException(404, "Ментор не найден")
    m.consent_given = body.given
    m.consent_at = datetime.now() if body.given else None
    audit(db, user.id, "mentor.consent", "mentor", m.id, {"given": body.given})
    db.commit()
    return mentor_out(m)


# ------------------------------------------------------------------ расписание

@app.post("/lessons", response_model=schemas.LessonOut, status_code=201)
def create_lesson(body: schemas.LessonIn, db: Session = Depends(get_db),
                  user: User = Depends(require_roles(ADMIN, MANAGER))):
    mentor = db.get(Mentor, body.mentor_id)
    if mentor is None:
        raise HTTPException(404, "Ментор не найден")
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "Урок должен заканчиваться позже начала")
    ensure_open(db, body.starts_at)
    lesson = Lesson(mentor_id=mentor.id, room=body.room, title=body.title, starts_at=body.starts_at, ends_at=body.ends_at)
    db.add(lesson)
    db.flush()
    audit(db, user.id, "lesson.create", "lesson", lesson.id, {"mentor_id": mentor.id, "room": body.room, "starts_at": body.starts_at})
    db.commit()
    return lesson_out(lesson, mentor)


@app.get("/lessons", response_model=list[schemas.LessonOut])
def list_lessons(year: int, month: int, mentor_id: int | None = Query(default=None),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    start, end = bounds_or_422(year, month)
    if user.role == MENTOR:
        mentor_id = user.mentor_id  # ментор видит только своё
    q = (select(Lesson, Mentor).join(Mentor, Lesson.mentor_id == Mentor.id)
         .where(Lesson.starts_at >= start, Lesson.starts_at < end).order_by(Lesson.starts_at))
    if mentor_id is not None:
        q = q.where(Lesson.mentor_id == mentor_id)
    return [lesson_out(l, m) for l, m in db.execute(q).all()]


# ------------------------------------------------------------------ камера

@app.get("/camera/mentors", dependencies=[Depends(camera_key)])
def camera_mentors(db: Session = Depends(get_db)):
    """Кого можно распознавать: только активные менторы с согласием."""
    q = select(Mentor).where(Mentor.is_active.is_(True), Mentor.consent_given.is_(True))
    return [{"id": m.id, "full_name": m.full_name} for m in db.scalars(q).all()]


@app.post("/camera/sightings", status_code=201, dependencies=[Depends(camera_key)])
def post_sighting(body: schemas.SightingIn, db: Session = Depends(get_db)):
    mentor = db.get(Mentor, body.mentor_id)
    if mentor is None or not mentor.is_active:
        raise HTTPException(404, "Ментор не найден")
    if not mentor.consent_given:
        raise HTTPException(403, "Нет согласия ментора на распознавание")
    edge = timedelta(seconds=settings.edge_seconds)
    lesson = db.scalar(
        select(Lesson)
        .where(Lesson.mentor_id == mentor.id, Lesson.room == body.room,
               Lesson.starts_at <= body.seen_at + edge, Lesson.ends_at >= body.seen_at - edge)
        .order_by(Lesson.starts_at))
    s = Sighting(mentor_id=mentor.id, room=body.room, seen_at=body.seen_at, confidence=body.confidence,
                 liveness_ok=body.liveness_ok, lesson_id=lesson.id if lesson else None)
    db.add(s)
    db.commit()
    return {"id": s.id, "lesson_id": s.lesson_id}


# ------------------------------------------------------------------ отметки об уроках

@app.post("/attendance/evaluate-finished")
def evaluate_finished(db: Session = Depends(get_db), _=Depends(admin_or_camera)):
    """Создаёт записи для завершённых уроков, у которых ещё нет записи (один урок — одна запись)."""
    cutoff = datetime.now() - timedelta(seconds=settings.evaluate_delay_seconds)
    lessons = db.scalars(
        select(Lesson).outerjoin(Attendance, Attendance.lesson_id == Lesson.id)
        .where(Lesson.ends_at <= cutoff, Attendance.id.is_(None))).all()
    cfg = rules_config()
    for lesson in lessons:
        sightings = db.scalars(select(Sighting).where(Sighting.lesson_id == lesson.id)).all()
        ev = rules.evaluate_lesson(
            lesson.starts_at, lesson.ends_at,
            [rules.Sighting(s.seen_at, s.confidence, s.liveness_ok) for s in sightings], cfg)
        db.add(Attendance(lesson_id=lesson.id, mentor_id=lesson.mentor_id, first_seen=ev.first_seen,
                          last_seen=ev.last_seen, present_seconds=ev.present_seconds,
                          required_seconds=ev.required_seconds, status=rules.initial_status(ev), reason=ev.reason))
    db.commit()
    return {"evaluated": len(lessons)}


@app.get("/attendance", response_model=list[schemas.AttendanceOut])
def list_attendance(year: int, month: int, status: str | None = None, mentor_id: int | None = None,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role == MENTOR:
        mentor_id = user.mentor_id
    return [attendance_out(a, l, m) for a, l, m in month_rows(db, year, month, mentor_id=mentor_id, status=status)]


@app.post("/attendance/{attendance_id}/review", response_model=schemas.AttendanceOut)
def review_attendance(attendance_id: int, body: schemas.ReviewIn, db: Session = Depends(get_db),
                      user: User = Depends(require_roles(ADMIN))):
    att = db.get(Attendance, attendance_id)
    if att is None:
        raise HTTPException(404, "Запись не найдена")
    lesson, mentor = db.get(Lesson, att.lesson_id), db.get(Mentor, att.mentor_id)
    ensure_open(db, lesson.starts_at)
    note = body.note.strip()
    # отклонение и ручное подтверждение «не выполнено» требуют причины
    if (body.decision == "reject" or att.status == rules.NOT_MET) and len(note) < 3:
        raise HTTPException(422, "Укажите причину решения в комментарии")
    old_status = att.status
    if body.decision == "confirm":
        att.status, att.pay_model, att.rate = rules.CONFIRMED, mentor.pay_model, mentor.rate
    else:
        att.status, att.pay_model, att.rate = rules.REJECTED, None, None
    if note:
        att.note = note
    att.reviewed_by, att.reviewed_at = user.id, datetime.now()
    audit(db, user.id, "attendance.review", "attendance", att.id, {"from": old_status, "to": att.status, "note": note})
    db.commit()
    return attendance_out(att, lesson, mentor)


@app.post("/attendance/manual", response_model=schemas.AttendanceOut, status_code=201)
def manual_attendance(body: schemas.ManualIn, db: Session = Depends(get_db), user: User = Depends(require_roles(ADMIN))):
    """Ручная запись (например, камера не работала). Только если записи об уроке ещё нет."""
    lesson = db.get(Lesson, body.lesson_id)
    if lesson is None:
        raise HTTPException(404, "Урок не найден")
    ensure_open(db, lesson.starts_at)
    if db.scalar(select(Attendance.id).where(Attendance.lesson_id == lesson.id)):
        raise HTTPException(409, "Запись об уроке уже есть — используйте проверку записи")
    mentor = db.get(Mentor, lesson.mentor_id)
    required = int((lesson.ends_at - lesson.starts_at).total_seconds() * settings.presence_ratio)
    att = Attendance(lesson_id=lesson.id, mentor_id=mentor.id, present_seconds=0, required_seconds=required,
                     status=rules.CONFIRMED, reason="Добавлено администратором вручную", note=body.note.strip(),
                     pay_model=mentor.pay_model, rate=mentor.rate, reviewed_by=user.id, reviewed_at=datetime.now())
    db.add(att)
    db.flush()
    audit(db, user.id, "attendance.manual", "attendance", att.id, {"lesson_id": lesson.id, "note": body.note})
    db.commit()
    return attendance_out(att, lesson, mentor)


# ------------------------------------------------------------------ зарплата и отчёты

@app.get("/payroll", response_model=schemas.PayrollReport)
def payroll(year: int, month: int, db: Session = Depends(get_db), _: User = Depends(require_roles(ADMIN, MANAGER))):
    bounds_or_422(year, month)
    rows = build_payroll(db, year, month)
    return schemas.PayrollReport(
        year=year, month=month, closed=is_closed(db, datetime(year, month, 1)), rows=rows,
        total_lessons=sum(r.lessons for r in rows), total_hours=round(sum(r.hours for r in rows), 2),
        total_amount=round(sum(r.amount for r in rows), 2))


@app.get("/payroll/export.csv")
def payroll_csv(year: int, month: int, db: Session = Depends(get_db), _: User = Depends(require_roles(ADMIN, MANAGER))):
    bounds_or_422(year, month)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Ментор", "Уроков", "Часов", "Сумма, сом"])
    rows = build_payroll(db, year, month)
    for r in rows:
        writer.writerow([r.mentor_name, r.lessons, f"{r.hours:.2f}", f"{r.amount:.2f}"])
    writer.writerow(["Итого", sum(r.lessons for r in rows), f"{sum(r.hours for r in rows):.2f}",
                     f"{sum(r.amount for r in rows):.2f}"])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="payroll_{year}_{month:02d}.csv"'})


@app.post("/payroll/{year}/{month}/close")
def close_period(year: int, month: int, db: Session = Depends(get_db), user: User = Depends(require_roles(MANAGER))):
    """Закрывает месяц после выплаты. Нельзя закрыть, пока есть непроверенные отметки или необработанные уроки."""
    start, end = bounds_or_422(year, month)
    if is_closed(db, start):
        raise HTTPException(409, "Период уже закрыт")
    unchecked = db.scalar(
        select(func.count()).select_from(Attendance).join(Lesson, Attendance.lesson_id == Lesson.id)
        .where(Lesson.starts_at >= start, Lesson.starts_at < end,
               Attendance.status.in_([rules.PENDING, rules.NEEDS_REVIEW])))
    if unchecked:
        raise HTTPException(409, f"Есть непроверенные отметки: {unchecked}")
    unprocessed = db.scalar(
        select(func.count()).select_from(Lesson).outerjoin(Attendance, Attendance.lesson_id == Lesson.id)
        .where(Lesson.starts_at >= start, Lesson.starts_at < end, Lesson.ends_at <= datetime.now(), Attendance.id.is_(None)))
    if unprocessed:
        raise HTTPException(409, f"Есть необработанные завершённые уроки: {unprocessed}. Запустите обработку.")
    db.add(PeriodLock(year=year, month=month, closed_by=user.id))
    audit(db, user.id, "period.close", "period", None, {"year": year, "month": month})
    db.commit()
    return {"closed": True, "year": year, "month": month}


@app.get("/me/summary", response_model=schemas.MySummary)
def my_summary(year: int, month: int, db: Session = Depends(get_db), user: User = Depends(require_roles(MENTOR))):
    if user.mentor_id is None:
        raise HTTPException(400, "Аккаунт не привязан к ментору")
    rows = month_rows(db, year, month, mentor_id=user.mentor_id)
    mine = [r for r in build_payroll(db, year, month, mentor_id=user.mentor_id)]
    lessons = sum(r.lessons for r in mine)
    return schemas.MySummary(
        year=year, month=month, lessons=lessons, hours=round(sum(r.hours for r in mine), 2),
        amount=round(sum(r.amount for r in mine), 2),
        records=[attendance_out(a, l, m) for a, l, m in rows])


@app.get("/audit", response_model=list[schemas.AuditOut])
def audit_log(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db),
              _: User = Depends(require_roles(ADMIN, MANAGER))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [schemas.AuditOut(id=a.id, user_id=a.user_id, action=a.action, entity=a.entity,
                             entity_id=a.entity_id, details=a.details, created_at=a.created_at) for a in rows]
