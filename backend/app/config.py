"""Настройки backend. Всё берётся из переменных окружения (или файла .env)."""
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv необязателен
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    camera_api_key: str
    token_ttl_minutes: int
    # --- правила зачёта урока (настраивает академия) ---
    presence_ratio: float        # доля урока, которую ментор должен быть в кадре
    edge_seconds: int            # допуск «замечен в начале / в конце» (сек)
    max_gap_seconds: int         # максимальный разрыв между отметками внутри одного присутствия
    min_confidence: float        # ниже — отметка игнорируется
    review_confidence: float     # ниже — отметка считается сомнительной
    max_doubtful_share: float    # доля сомнительных отметок, после которой нужна ручная проверка
    evaluate_delay_seconds: int  # через сколько после конца урока его можно оценивать


def load_settings() -> Settings:
    e = os.getenv
    return Settings(
        database_url=e("DATABASE_URL", "postgresql+psycopg2://aimentor:aimentor@localhost:5432/aimentor"),
        secret_key=e("SECRET_KEY", "change-me-please"),
        camera_api_key=e("CAMERA_API_KEY", "change-me-camera"),
        token_ttl_minutes=int(e("TOKEN_TTL_MINUTES", "480")),
        presence_ratio=float(e("PRESENCE_RATIO", "0.8")),
        edge_seconds=int(e("EDGE_SECONDS", "600")),
        max_gap_seconds=int(e("MAX_GAP_SECONDS", "180")),
        min_confidence=float(e("MIN_CONFIDENCE", "0.6")),
        review_confidence=float(e("REVIEW_CONFIDENCE", "0.75")),
        max_doubtful_share=float(e("MAX_DOUBTFUL_SHARE", "0.2")),
        evaluate_delay_seconds=int(e("EVALUATE_DELAY_SECONDS", "120")),
    )


settings = load_settings()
