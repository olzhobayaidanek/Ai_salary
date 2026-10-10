import unittest
from datetime import datetime, timedelta

from backend.app import rules
from backend.app.rules import Rules, Sighting
from backend.app.security import create_token, decode_token, hash_password, verify_password

START = datetime(2026, 10, 5, 10, 0)
END = datetime(2026, 10, 5, 12, 0)


def every(step_s, from_dt, to_dt, conf=0.9, live=True):
    out, t = [], from_dt
    while t <= to_dt:
        out.append(Sighting(t, conf, live))
        t += timedelta(seconds=step_s)
    return out


class EvaluateLessonTests(unittest.TestCase):
    def test_full_presence_is_pending(self):
        ev = rules.evaluate_lesson(START, END, every(30, START, END))
        self.assertTrue(ev.meets_rules)
        self.assertEqual(rules.initial_status(ev), rules.PENDING)
        self.assertEqual(ev.present_seconds, 7200)

    def test_no_sightings(self):
        ev = rules.evaluate_lesson(START, END, [])
        self.assertFalse(ev.meets_rules)
        self.assertEqual(rules.initial_status(ev), rules.NOT_MET)

    def test_only_first_half(self):
        ev = rules.evaluate_lesson(START, END, every(30, START, START + timedelta(hours=1)))
        self.assertEqual(rules.initial_status(ev), rules.NOT_MET)
        self.assertIn("в конце", ev.reason)

    def test_late_arrival_fails_start_rule(self):
        ev = rules.evaluate_lesson(START, END, every(30, START + timedelta(minutes=30), END))
        self.assertFalse(ev.meets_rules)
        self.assertIn("в начале", ev.reason)

    def test_80_percent_threshold(self):
        # 96 минут из 120 = ровно 80% -> засчитывается; чуть меньше — нет
        ok = rules.evaluate_lesson(START, END, every(30, START, START + timedelta(minutes=96)) + [Sighting(END, 0.9)])
        # разрыв в конце (>180с) не считается присутствием, поэтому присутствие = 96 мин
        self.assertEqual(ok.present_seconds, 96 * 60)
        self.assertTrue(ok.meets_rules)
        short = rules.evaluate_lesson(START, END, every(30, START, START + timedelta(minutes=95)) + [Sighting(END, 0.9)])
        self.assertFalse(short.meets_rules)

    def test_long_gap_not_counted(self):
        s = every(30, START, START + timedelta(minutes=10)) + every(30, END - timedelta(minutes=10), END)
        ev = rules.evaluate_lesson(START, END, s)
        self.assertFalse(ev.meets_rules)  # присутствие всего ~20 минут

    def test_low_confidence_ignored(self):
        ev = rules.evaluate_lesson(START, END, every(30, START, END, conf=0.4))
        self.assertFalse(ev.meets_rules)

    def test_spoof_goes_to_review(self):
        ev = rules.evaluate_lesson(START, END, every(30, START, END, live=False))
        self.assertTrue(ev.meets_rules)
        self.assertEqual(rules.initial_status(ev), rules.NEEDS_REVIEW)

    def test_doubtful_confidence_goes_to_review(self):
        ev = rules.evaluate_lesson(START, END, every(30, START, END, conf=0.65))
        self.assertEqual(rules.initial_status(ev), rules.NEEDS_REVIEW)

    def test_few_doubtful_sightings_ok(self):
        s = every(30, START, END) + [Sighting(START + timedelta(seconds=15), 0.62, False)]
        self.assertEqual(rules.initial_status(rules.evaluate_lesson(START, END, s)), rules.PENDING)

    def test_early_arrival_and_sightings_outside_window(self):
        s = every(30, START - timedelta(minutes=5), END + timedelta(minutes=5))
        ev = rules.evaluate_lesson(START, END, s)
        self.assertTrue(ev.meets_rules)
        self.assertEqual(ev.present_seconds, 7200)  # время обрезано границами урока
        far = [Sighting(START - timedelta(hours=2), 0.9)]
        self.assertFalse(rules.evaluate_lesson(START, END, far).meets_rules)

    def test_custom_rules(self):
        strict = Rules(presence_ratio=1.0, max_gap_seconds=60)
        ev = rules.evaluate_lesson(START, END, every(30, START, END - timedelta(minutes=1)), strict)
        self.assertFalse(ev.meets_rules)

    def test_bad_lesson(self):
        with self.assertRaises(ValueError):
            rules.evaluate_lesson(END, START, [])


class PayrollTests(unittest.TestCase):
    def test_slide_7_demo_numbers(self):
        # Ментор 1: 10 уроков по 2 часа, 500 сом за урок -> 20 ч, 5000
        for lessons, hours, total in [(10, 20, 5000), (12, 24, 6000), (8, 16, 4000)]:
            amount = sum(rules.lesson_amount(rules.PER_LESSON, 500, START, END) for _ in range(lessons))
            h = sum(rules.lesson_hours(START, END) for _ in range(lessons))
            self.assertEqual(int(amount), total)
            self.assertEqual(int(h), hours)

    def test_per_hour_model(self):
        self.assertEqual(str(rules.lesson_amount(rules.PER_HOUR, 300, START, END)), "600.00")

    def test_models_not_mixed_unknown_rejected(self):
        with self.assertRaises(ValueError):
            rules.lesson_amount("mixed", 1, START, END)

    def test_month_bounds(self):
        self.assertEqual(rules.month_bounds(2026, 12), (datetime(2026, 12, 1), datetime(2027, 1, 1)))
        with self.assertRaises(ValueError):
            rules.month_bounds(2026, 13)


class SecurityTests(unittest.TestCase):
    def test_password(self):
        h = hash_password("secret-pass")
        self.assertTrue(verify_password("secret-pass", h))
        self.assertFalse(verify_password("other", h))
        self.assertFalse(verify_password("x", "garbage"))

    def test_token_roundtrip_and_tamper(self):
        t = create_token({"sub": "1", "role": "admin"}, key="k")
        self.assertEqual(decode_token(t, key="k")["role"], "admin")
        with self.assertRaises(ValueError):
            decode_token(t, key="other")
        h, b, s = t.split(".")
        with self.assertRaises(ValueError):
            decode_token(f"{h}.{b}x.{s}", key="k")

    def test_token_expired(self):
        t = create_token({"sub": "1"}, ttl_minutes=-1, key="k")
        with self.assertRaises(ValueError):
            decode_token(t, key="k")


if __name__ == "__main__":
    unittest.main()
