import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from worker.face_engine import FACE_SIZE, FaceEngine, MotionLiveness


def texture(seed):
    rng = np.random.default_rng(seed)
    img = cv2.resize(rng.integers(0, 255, (12, 12), dtype=np.uint8), FACE_SIZE, interpolation=cv2.INTER_CUBIC)
    return img


class EngineTests(unittest.TestCase):
    def test_cascade_loads_and_blank_frame_has_no_faces(self):
        e = FaceEngine(str(Path(tempfile.mkdtemp()) / "m.yml"))
        self.assertEqual(e.detect(np.zeros((480, 640, 3), np.uint8)), [])

    def test_train_predict_forget(self):
        tmp = Path(tempfile.mkdtemp())
        faces = tmp / "faces"
        for mid, seed in ((3, 1), (7, 2)):
            (faces / str(mid)).mkdir(parents=True)
            for k in range(5):
                noisy = np.clip(texture(seed).astype(int) + np.random.default_rng(k).integers(-2, 3, FACE_SIZE[::-1]), 0, 255)
                cv2.imwrite(str(faces / str(mid) / f"{k}.png"), noisy.astype(np.uint8))
        e = FaceEngine(str(tmp / "model.yml"))
        self.assertEqual(e.train(str(faces)), 10)
        label, score = e.predict(texture(1))
        self.assertEqual(label, 3)
        self.assertGreater(score, 0.5)
        self.assertEqual(e.recognizer.predict(cv2.imread(str(faces / "3" / "0.png"), 0))[1], 0.0)  # точное совпадение
        label2, _ = e.predict(texture(2))
        self.assertEqual(label2, 7)
        _, stranger = e.predict(texture(99))  # незнакомое «лицо» -> низкая уверенность
        self.assertLess(stranger, score)

        e2 = FaceEngine(str(tmp / "model.yml"))  # модель сохраняется и загружается
        e2.load()
        self.assertEqual(e2.predict(texture(2))[0], 7)

        e.forget(3, str(faces))  # отзыв согласия: фото удалены, модель переобучена
        self.assertFalse((faces / "3").exists())
        self.assertEqual(e.predict(texture(1))[0], 7)

    def test_train_empty_dir_raises(self):
        e = FaceEngine(str(Path(tempfile.mkdtemp()) / "m.yml"))
        with self.assertRaises(ValueError):
            e.train(tempfile.mkdtemp())

    def test_liveness_static_vs_moving(self):
        static = MotionLiveness(window=6, threshold=2.0)
        img = texture(5)
        results = [static.update(1, img) for _ in range(8)]
        self.assertFalse(results[-1])  # одинаковые кадры -> подозрительно
        moving = MotionLiveness(window=6, threshold=2.0)
        results = [moving.update(1, texture(i)) for i in range(8)]
        self.assertTrue(results[-1])
        young = MotionLiveness(window=6)
        self.assertTrue(young.update(1, img))  # мало данных -> не подозреваем


if __name__ == "__main__":
    unittest.main()
