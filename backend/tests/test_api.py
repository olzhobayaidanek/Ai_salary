"""Интеграционный тест API на SQLite. Пропускается, если нет fastapi / sqlalchemy / httpx.

Запуск:  python -m unittest discover -s backend/tests -t . -v
"""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("fastapi", "sqlalchemy", "httpx"))

if HAVE_DEPS:
    _tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
    os.environ["CAMERA_API_KEY"] = "cam-key"
    from fastapi.testclient import TestClient

    from backend.app.db import SessionLocal, init_db
    from backend.app.main import app
    from backend.app.models import User
    from backend.app.security import hash_password

CAM = {"X-API-Key": "cam-key"}


@unittest.skipUnless(HAVE_DEPS, "fastapi/sqlalchemy/httpx не установлены")
class ApiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        with SessionLocal() as db:
            for name, role in (("admin", "admin"), ("boss", "manager")):
                db.add(User(username=name, password_hash=hash_password("password123"), role=role))
            db.commit()
        cls.client = TestClient(app)

    def login(self, username):
        r = self.client.post("/auth/login", json={"username": username, "password": "password123"})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def test_full_flow(self):
        c = self.client
        admin, boss = self.login("admin"), self.login("boss")
        self.assertEqual(c.get("/mentors").status_code, 401)

        m = c.post("/mentors", json={"full_name": "Айбек", "rate": 500}, headers=admin).json()
        # без согласия камера ничего не принимает
        start = datetime(2026, 9, 1, 10, 0)
        end = start + timedelta(hours=2)
        lesson = c.post("/lessons", headers=admin, json={
            "mentor_id": m["id"], "room": "A1", "starts_at": start.isoformat(), "ends_at": end.isoformat()}).json()
        body = {"mentor_id": m["id"], "room": "A1", "seen_at": start.isoformat(), "confidence": 0.9}
        self.assertEqual(c.post("/camera/sightings", json=body, headers=CAM).status_code, 403)
        self.assertEqual(c.post("/camera/sightings", json=body).status_code, 401)

        self.assertTrue(c.post(f"/mentors/{m['id']}/consent", json={"given": True}, headers=admin).json()["consent_given"])
        t = start
        while t <= end:
            r = c.post("/camera/sightings", headers=CAM,
                       json={**body, "seen_at": t.isoformat()})
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["lesson_id"], lesson["id"])
            t += timedelta(seconds=60)

        self.assertEqual(c.post("/attendance/evaluate-finished", headers=CAM).json()["evaluated"], 1)
        self.assertEqual(c.post("/attendance/evaluate-finished", headers=CAM).json()["evaluated"], 0)  # без дублей

        rec = c.get("/attendance", params={"year": 2026, "month": 9}, headers=admin).json()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["status"], "pending")
        # в зарплату неподтверждённое не попадает
        pay = c.get("/payroll", params={"year": 2026, "month": 9}, headers=boss).json()
        self.assertEqual(pay["total_amount"], 0)
        # закрыть месяц с непроверенной отметкой нельзя
        self.assertEqual(c.post("/payroll/2026/9/close", headers=boss).status_code, 409)
        # менеджер не может подтверждать
        self.assertEqual(c.post(f"/attendance/{rec[0]['id']}/review", json={"decision": "confirm"}, headers=boss).status_code, 403)

        ok = c.post(f"/attendance/{rec[0]['id']}/review", json={"decision": "confirm"}, headers=admin)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["amount"], 500.0)

        # смена ставки не меняет уже подтверждённое начисление
        c.patch(f"/mentors/{m['id']}", json={"rate": 900}, headers=admin)
        pay = c.get("/payroll", params={"year": 2026, "month": 9}, headers=boss).json()
        self.assertEqual((pay["total_lessons"], pay["total_hours"], pay["total_amount"]), (1, 2.0, 500.0))
        self.assertIn("Айбек", c.get("/payroll/export.csv", params={"year": 2026, "month": 9}, headers=boss).content.decode("utf-8-sig"))

        # закрытие периода блокирует правки
        self.assertEqual(c.post("/payroll/2026/9/close", headers=boss).status_code, 200)
        locked = c.post(f"/attendance/{rec[0]['id']}/review", json={"decision": "reject", "note": "ошибка"}, headers=admin)
        self.assertEqual(locked.status_code, 409)

        log = c.get("/audit", headers=admin).json()
        self.assertTrue(any(a["action"] == "attendance.review" for a in log))


if __name__ == "__main__":
    unittest.main()
