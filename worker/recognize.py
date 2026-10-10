"""Воркер камеры: читает видео, узнаёт зарегистрированных менторов, шлёт отметки в backend.

  python -m worker.recognize --room "Класс 1" --source 0
  python -m worker.recognize --room "Класс 2" --source rtsp://user:pass@192.168.1.10/stream --show
"""
import argparse
import logging
import os
import time
from datetime import datetime

import cv2

from .client import ApiClient
from .face_engine import FaceEngine, MotionLiveness

log = logging.getLogger("worker")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--room", required=True, help="кабинет (должен совпадать с расписанием)")
    p.add_argument("--source", default="0")
    p.add_argument("--interval", type=float, default=30, help="как часто слать отметку об одном менторе, сек")
    p.add_argument("--every", type=int, default=5, help="обрабатывать каждый N-й кадр")
    p.add_argument("--send-min", type=float, default=0.4, help="не отправлять отметки с уверенностью ниже")
    p.add_argument("--liveness-threshold", type=float, default=2.0)
    p.add_argument("--model", default="data/model.yml")
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--api-key", default=os.getenv("CAMERA_API_KEY", "change-me-camera"))
    p.add_argument("--show", action="store_true", help="показывать окно с видео (нужен GUI)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    engine = FaceEngine(a.model)
    engine.load()
    client = ApiClient(a.api_url, a.api_key)
    liveness = MotionLiveness(threshold=a.liveness_threshold)
    allowed: dict[int, str] = {}
    last_sent: dict[int, float] = {}
    last_refresh = last_eval = 0.0
    frame_no = 0
    src = int(a.source) if a.source.isdigit() else a.source
    cap = cv2.VideoCapture(src)

    while True:
        now = time.time()
        if now - last_refresh > 300:  # список согласившихся менторов обновляем раз в 5 минут
            try:
                allowed = client.consenting_mentors()
                last_refresh = now
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось обновить список менторов: %s", exc)
                last_refresh = now - 240  # повторим через минуту
        if now - last_eval > 300:
            client.evaluate_finished()
            last_eval = now

        ok, frame = cap.read()
        if not ok:
            log.warning("Нет кадра, переподключаюсь к камере...")
            cap.release()
            time.sleep(3)
            cap = cv2.VideoCapture(src)
            continue
        frame_no += 1
        if frame_no % a.every:
            continue

        for box in engine.detect(frame):
            face = engine.prepare(frame, box)
            mentor_id, score = engine.predict(face)
            if mentor_id not in allowed or score < a.send_min:
                continue  # чужие и несогласившиеся не фиксируются
            live = liveness.update(mentor_id, face)
            if now - last_sent.get(mentor_id, 0) >= a.interval:
                client.send_sighting(mentor_id, a.room, datetime.now(), score, live)
                last_sent[mentor_id] = now
                log.info("%s: score=%.2f live=%s", allowed[mentor_id], score, live)
            if a.show:
                x, y, w, h = box
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 200, 0), 2)
                cv2.putText(frame, f"{mentor_id} {score:.2f}", (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
        if a.show:
            cv2.imshow("AI Mentor", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
