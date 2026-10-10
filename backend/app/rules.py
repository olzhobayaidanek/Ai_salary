"""Бизнес-правила без зависимостей от БД и веб-фреймворка (легко тестируются).

Урок засчитывается, если ментор:
  1) замечен в начале урока (первая отметка не позже start + edge),
  2) замечен в конце урока (последняя отметка не раньше end - edge),
  3) суммарное время присутствия >= presence_ratio * длительность урока.
Но засчитанным урок становится только после подтверждения администратором.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

# статусы записи об уроке
PENDING = "pending"            # правило выполнено, ждёт подтверждения
NEEDS_REVIEW = "needs_review"  # правило выполнено, но отметки сомнительные
NOT_MET = "not_met"            # правило не выполнено (в зарплату не идёт)
CONFIRMED = "confirmed"        # подтверждён администратором -> идёт в зарплату
REJECTED = "rejected"          # отклонён администратором

PER_LESSON = "per_lesson"
PER_HOUR = "per_hour"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Sighting:
    seen_at: datetime
    confidence: float
    liveness_ok: bool = True


@dataclass(frozen=True)
class Rules:
    presence_ratio: float = 0.8
    edge_seconds: int = 600
    max_gap_seconds: int = 180
    min_confidence: float = 0.6
    review_confidence: float = 0.75
    max_doubtful_share: float = 0.2


@dataclass(frozen=True)
class Evaluation:
    meets_rules: bool
    needs_review: bool
    first_seen: datetime | None
    last_seen: datetime | None
    present_seconds: int
    required_seconds: int
    reason: str


def evaluate_lesson(starts_at: datetime, ends_at: datetime, sightings, rules: Rules = Rules()) -> Evaluation:
    duration = (ends_at - starts_at).total_seconds()
    if duration <= 0:
        raise ValueError("Урок должен заканчиваться позже начала")
    required = math.ceil(duration * rules.presence_ratio)
    edge = timedelta(seconds=rules.edge_seconds)

    usable = sorted(
        (
            s for s in sightings
            if s.confidence >= rules.min_confidence
            and starts_at - edge <= s.seen_at <= ends_at + edge
        ),
        key=lambda s: s.seen_at,
    )
    if not usable:
        return Evaluation(False, False, None, None, 0, required, "Ментор не замечен камерой во время урока")

    first_seen, last_seen = usable[0].seen_at, usable[-1].seen_at
    # для подсчёта времени обрезаем отметки границами урока
    clipped = [min(max(s.seen_at, starts_at), ends_at) for s in usable]
    present = 0.0
    for a, b in zip(clipped, clipped[1:]):
        gap = (b - a).total_seconds()
        if gap <= rules.max_gap_seconds:  # большой разрыв = ментор выходил, не считаем
            present += gap
    present_seconds = int(present)

    problems = []
    if first_seen > starts_at + edge:
        problems.append("не замечен в начале урока")
    if last_seen < ends_at - edge:
        problems.append("не замечен в конце урока")
    if present_seconds < required:
        problems.append(f"присутствие {present_seconds // 60} мин из {math.ceil(required / 60)} мин необходимых")
    meets = not problems

    doubtful = sum(1 for s in usable if (not s.liveness_ok) or s.confidence < rules.review_confidence)
    share = doubtful / len(usable)
    needs_review = meets and share > rules.max_doubtful_share

    if not meets:
        reason = "Правило не выполнено: " + "; ".join(problems)
    elif needs_review:
        reason = (f"Правило выполнено, но {round(share * 100)}% отметок сомнительные "
                  "(низкая уверенность или нет «живого» лица) — нужна проверка")
    else:
        reason = "Правило выполнено"
    return Evaluation(meets, needs_review, first_seen, last_seen, present_seconds, required, reason)


def initial_status(ev: Evaluation) -> str:
    if not ev.meets_rules:
        return NOT_MET
    return NEEDS_REVIEW if ev.needs_review else PENDING


# ---------------- зарплата ----------------

def lesson_hours(starts_at: datetime, ends_at: datetime) -> Decimal:
    seconds = int((ends_at - starts_at).total_seconds())
    return (Decimal(seconds) / Decimal(3600)).quantize(CENT, ROUND_HALF_UP)


def lesson_amount(pay_model: str, rate, starts_at: datetime, ends_at: datetime) -> Decimal:
    """Две модели оплаты не смешиваем: либо за урок, либо за час."""
    rate = Decimal(str(rate))
    if pay_model == PER_LESSON:
        amount = rate
    elif pay_model == PER_HOUR:
        amount = rate * lesson_hours(starts_at, ends_at)
    else:
        raise ValueError(f"Неизвестная модель оплаты: {pay_model}")
    return amount.quantize(CENT, ROUND_HALF_UP)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise ValueError("Некорректный период")
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end
