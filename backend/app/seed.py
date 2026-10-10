"""Демо-данные, воспроизводящие таблицу со слайда 7 (ставка 500 сом за урок, уроки по 2 часа).

    python -m backend.app.seed            # текущий месяц
    python -m backend.app.seed 2026 9     # конкретный месяц
"""
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from . import rules
from .db import SessionLocal, init_db
from .models import Attendance, Lesson, Mentor, User
from .security import hash_password

DEMO = [("Ментор 1", 10), ("Ментор 2", 12), ("Ментор 3", 8)]
ACCOUNTS = [("admin", "admin12345", "admin", None), ("manager", "manager12345", "manager", None)]


def main(year: int, month: int) -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == "admin")):
            print("Демо-данные уже есть — ничего не делаю.")
            return
        for username, password, role, _ in ACCOUNTS:
            db.add(User(username=username, password_hash=hash_password(password), role=role))
        for i, (name, count) in enumerate(DEMO, start=1):
            mentor = Mentor(full_name=name, pay_model="per_lesson", rate=Decimal("500"),
                            consent_given=True, consent_at=datetime.now())
            db.add(mentor)
            db.flush()
            db.add(User(username=f"mentor{i}", password_hash=hash_password(f"mentor{i}-pass"),
                        role="mentor", mentor_id=mentor.id))
            for day in range(1, count + 1):
                start = datetime(year, month, day, 10, 0)
                lesson = Lesson(mentor_id=mentor.id, room="Класс 1", title="Урок", starts_at=start,
                                ends_at=start + timedelta(hours=2))
                db.add(lesson)
                db.flush()
                db.add(Attendance(lesson_id=lesson.id, mentor_id=mentor.id, status=rules.CONFIRMED,
                                  present_seconds=7200, required_seconds=5760, reason="Демо-данные",
                                  pay_model="per_lesson", rate=Decimal("500")))
        db.commit()
    print("Готово. Логины: admin/admin12345, manager/manager12345, mentor1/mentor1-pass ... (смените пароли!)")


if __name__ == "__main__":
    now = datetime.now()
    y, m = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (now.year, now.month)
    main(y, m)
