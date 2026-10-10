"""Клиент к backend: отправка отметок с буфером на случай обрыва связи."""
from collections import deque
from datetime import datetime

import requests


class ApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key}
        self.timeout = timeout
        self._buffer: deque[dict] = deque(maxlen=1000)

    def consenting_mentors(self) -> dict[int, str]:
        r = requests.get(f"{self.base_url}/camera/mentors", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return {m["id"]: m["full_name"] for m in r.json()}

    def send_sighting(self, mentor_id: int, room: str, seen_at: datetime, confidence: float, liveness_ok: bool) -> bool:
        self._buffer.append({"mentor_id": mentor_id, "room": room, "seen_at": seen_at.isoformat(timespec="seconds"),
                             "confidence": round(confidence, 3), "liveness_ok": liveness_ok})
        return self.flush()

    def flush(self) -> bool:
        """Отправляет накопленное. Нет связи — оставляет в буфере. Отказ сервера (4xx) — отбрасывает отметку."""
        while self._buffer:
            item = self._buffer[0]
            try:
                r = requests.post(f"{self.base_url}/camera/sightings", json=item, headers=self.headers, timeout=self.timeout)
            except requests.RequestException:
                return False
            if r.status_code >= 500:
                return False
            self._buffer.popleft()
        return True

    def evaluate_finished(self) -> None:
        try:
            requests.post(f"{self.base_url}/attendance/evaluate-finished", headers=self.headers, timeout=self.timeout)
        except requests.RequestException:
            pass
