"""Распознавание лиц на OpenCV: Haar-каскад (поиск лица) + LBPH (узнавание).

Нужен пакет opencv-contrib-python (модуль cv2.face).
"""
import shutil
from collections import deque
from pathlib import Path

import cv2
import numpy as np

FACE_SIZE = (200, 200)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


class FaceEngine:
    def __init__(self, model_path: str = "data/model.yml", max_distance: float = 120.0):
        self.model_path = Path(model_path)
        self.max_distance = max_distance
        self.cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.cascade.empty():
            raise RuntimeError("Не найден каскад haarcascade_frontalface_default.xml")
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.trained = False

    # --- поиск лица ---
    def detect(self, frame) -> list[tuple[int, int, int, int]]:
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        boxes = self.cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
        return [tuple(int(v) for v in b) for b in boxes]

    @staticmethod
    def prepare(frame, box) -> np.ndarray:
        """Вырезает лицо, переводит в серый, нормализует размер и контраст."""
        x, y, w, h = box
        gray = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        return cv2.equalizeHist(cv2.resize(gray, FACE_SIZE))

    # --- обучение / хранение модели ---
    def train(self, faces_dir: str) -> int:
        """Папка faces_dir/<id_ментора>/*.jpg -> модель. Возвращает число фотографий."""
        images, labels = [], []
        for person in sorted(Path(faces_dir).glob("*")):
            if not (person.is_dir() and person.name.isdigit()):
                continue
            for path in sorted(person.iterdir()):
                if path.suffix.lower() not in IMAGE_EXT:
                    continue
                img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                images.append(cv2.resize(img, FACE_SIZE))
                labels.append(int(person.name))
        if not images:
            raise ValueError(f"В {faces_dir} нет фотографий для обучения")
        self.recognizer.train(images, np.array(labels, dtype=np.int32))
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.recognizer.write(str(self.model_path))
        self.trained = True
        return len(images)

    def load(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель {self.model_path} не найдена — сначала запустите enroll")
        self.recognizer.read(str(self.model_path))
        self.trained = True

    def forget(self, mentor_id: int, faces_dir: str) -> None:
        """Удаляет фото ментора (отзыв согласия) и переобучает модель."""
        shutil.rmtree(Path(faces_dir) / str(mentor_id), ignore_errors=True)
        try:
            self.train(faces_dir)
        except ValueError:  # фото не осталось совсем
            self.model_path.unlink(missing_ok=True)
            self.trained = False

    # --- распознавание ---
    def predict(self, face_gray: np.ndarray) -> tuple[int, float]:
        """(id ментора, уверенность 0..1). LBPH возвращает расстояние: чем меньше, тем лучше."""
        if not self.trained:
            raise RuntimeError("Модель не обучена")
        label, distance = self.recognizer.predict(face_gray)
        return int(label), float(max(0.0, 1.0 - distance / self.max_distance))


class MotionLiveness:
    """Грубая проверка «живого» лица: у неподвижной фотографии почти нет изменений между кадрами.

    ВАЖНО: это только эвристика MVP. Видео с телефона она не ловит; сомнительные отметки
    уходят на ручную проверку администратору. Порог нужно калибровать на своей камере.
    """

    def __init__(self, window: int = 10, threshold: float = 2.0):
        self.window, self.threshold = window, threshold
        self._frames: dict[int, deque] = {}
        self.last_score: float | None = None

    def update(self, key: int, face_gray: np.ndarray) -> bool:
        small = cv2.resize(face_gray, (32, 32)).astype(np.float32)
        buf = self._frames.setdefault(key, deque(maxlen=self.window))
        buf.append(small)
        if len(buf) < self.window:
            return True  # данных мало — не считаем подозрительным
        frames = list(buf)
        self.last_score = float(np.mean([np.mean(np.abs(a - b)) for a, b in zip(frames, frames[1:])]))
        return self.last_score >= self.threshold
