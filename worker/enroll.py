"""Регистрация лиц менторов (только при наличии согласия в системе).

  python -m worker.enroll --mentor-id 3 --room "Класс 1"          # снять фото с камеры
  python -m worker.enroll --mentor-id 3 --from-dir ./photos/3     # взять готовые фото
  python -m worker.enroll --train-only                            # переобучить модель
  python -m worker.enroll --forget 3                              # удалить данные ментора (отзыв согласия)
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import cv2

from .client import ApiClient
from .face_engine import FaceEngine


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mentor-id", type=int)
    p.add_argument("--source", default="0", help="номер камеры или RTSP/файл")
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--from-dir")
    p.add_argument("--train-only", action="store_true")
    p.add_argument("--forget", type=int)
    p.add_argument("--faces-dir", default="data/faces")
    p.add_argument("--model", default="data/model.yml")
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--api-key", default=os.getenv("CAMERA_API_KEY", "change-me-camera"))
    a = p.parse_args()

    engine = FaceEngine(a.model)
    if a.forget is not None:
        engine.forget(a.forget, a.faces_dir)
        print(f"Данные ментора {a.forget} удалены, модель переобучена.")
        return
    if a.train_only:
        print(f"Обучено на {engine.train(a.faces_dir)} фото.")
        return
    if a.mentor_id is None:
        sys.exit("Укажите --mentor-id")

    allowed = ApiClient(a.api_url, a.api_key).consenting_mentors()
    if a.mentor_id not in allowed:
        sys.exit("У ментора нет согласия на распознавание (или он неактивен) — регистрация запрещена.")

    target = Path(a.faces_dir) / str(a.mentor_id)
    target.mkdir(parents=True, exist_ok=True)
    saved = 0
    if a.from_dir:
        for path in sorted(Path(a.from_dir).iterdir()):
            frame = cv2.imread(str(path))
            boxes = engine.detect(frame) if frame is not None else []
            if len(boxes) == 1:
                cv2.imwrite(str(target / f"{int(time.time() * 1000)}_{saved}.jpg"), engine.prepare(frame, boxes[0]))
                saved += 1
    else:
        cap = cv2.VideoCapture(int(a.source) if a.source.isdigit() else a.source)
        print(f"Смотрите в камеру, снимаю {a.count} кадров...")
        last = 0.0
        while saved < a.count:
            ok, frame = cap.read()
            if not ok:
                sys.exit("Камера недоступна")
            boxes = engine.detect(frame)
            if len(boxes) == 1 and time.time() - last > 0.4:  # ровно одно лицо в кадре
                cv2.imwrite(str(target / f"{int(time.time() * 1000)}_{saved}.jpg"), engine.prepare(frame, boxes[0]))
                saved += 1
                last = time.time()
        cap.release()
    if saved == 0:
        shutil.rmtree(target, ignore_errors=True)
        sys.exit("Лицо не найдено ни на одном кадре")
    print(f"Сохранено {saved} фото. Обучено на {engine.train(a.faces_dir)} фото всего.")


if __name__ == "__main__":
    main()
