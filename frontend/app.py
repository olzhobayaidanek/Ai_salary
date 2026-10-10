"""AI Mentor — Streamlit-интерфейс (ментор / администратор / руководитель).

Запуск:  streamlit run frontend/app.py
"""
import os
from datetime import date, datetime, time

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
STATUS_RU = {
    "pending": "Ждёт проверки",
    "needs_review": "Сомнительная — проверить",
    "not_met": "Правило не выполнено",
    "confirmed": "Подтверждён",
    "rejected": "Отклонён",
}
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
PAY_RU = {"per_lesson": "за урок", "per_hour": "за час"}

st.set_page_config(page_title="AI Mentor", page_icon="🎓", layout="wide")


# ---------------------------------------------------------------- API

def api(method: str, path: str, **kwargs):
    headers = kwargs.pop("headers", {})
    if st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state['token']}"
    try:
        resp = requests.request(method, f"{API_URL}{path}", headers=headers, timeout=15, **kwargs)
    except requests.RequestException as exc:
        st.error(f"Нет связи с сервером: {exc}")
        return None
    if resp.status_code == 401 and path != "/auth/login":
        st.session_state.clear()
        st.warning("Сессия истекла — войдите снова.")
        st.rerun()
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        st.error(f"Ошибка {resp.status_code}: {detail}")
        return None
    if resp.headers.get("content-type", "").startswith("text/csv"):
        return resp.content
    return resp.json()


# ---------------------------------------------------------------- помощники

def period_picker(key: str) -> tuple[int, int]:
    today = date.today()
    c1, c2, _ = st.columns([1, 1, 4])
    month = c1.selectbox("Месяц", range(1, 13), index=today.month - 1, format_func=lambda m: MONTHS[m - 1], key=f"{key}_m")
    year = c2.number_input("Год", 2000, 2100, today.year, key=f"{key}_y")
    return int(year), int(month)


def fmt_dt(value: str | None, fmt: str) -> str:
    return datetime.fromisoformat(value).strftime(fmt) if value else "—"


def records_df(records: list[dict]) -> pd.DataFrame:
    rows = [{
        "Дата": fmt_dt(r["starts_at"], "%d.%m.%Y"),
        "Время": f"{fmt_dt(r['starts_at'], '%H:%M')}–{fmt_dt(r['ends_at'], '%H:%M')}",
        "Ментор": r["mentor_name"],
        "Кабинет": r["room"],
        "Статус": STATUS_RU.get(r["status"], r["status"]),
        "Присутствие, мин": r["present_seconds"] // 60,
        "Сумма, сом": r["amount"] if r["amount"] is not None else None,
        "Комментарий": r["note"] or r["reason"],
    } for r in records]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- вход

def login_page():
    st.title("🎓 AI Mentor")
    st.caption("Учёт уроков и расчёт зарплаты менторов")
    with st.form("login"):
        username = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        if st.form_submit_button("Войти"):
            data = api("POST", "/auth/login", json={"username": username, "password": password})
            if data:
                st.session_state.update(token=data["access_token"], role=data["role"], username=data["username"])
                st.rerun()


# ---------------------------------------------------------------- ментор

def mentor_page():
    st.header("Мои уроки")
    year, month = period_picker("me")
    data = api("GET", "/me/summary", params={"year": year, "month": month})
    if not data:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Подтверждённых уроков", data["lessons"])
    c2.metric("Часов", f"{data['hours']:.1f}")
    c3.metric("Начислено, сом", f"{data['amount']:,.0f}".replace(",", " "))
    st.caption("В начисления входят только уроки, подтверждённые администратором.")
    if data["records"]:
        st.dataframe(records_df(data["records"]).drop(columns=["Ментор"]), hide_index=True)
    else:
        st.info("За этот месяц записей нет.")


# ---------------------------------------------------------------- администратор

def review_page():
    st.header("Проверка отметок")
    year, month = period_picker("rev")
    if st.button("Обработать завершённые уроки"):
        res = api("POST", "/attendance/evaluate-finished")
        if res is not None:
            st.success(f"Обработано уроков: {res['evaluated']}")
    records = api("GET", "/attendance", params={"year": year, "month": month}) or []
    todo = [r for r in records if r["status"] in ("needs_review", "pending", "not_met")]
    st.subheader(f"Требуют решения: {len(todo)}")
    for r in todo:
        title = (f"{r['mentor_name']} · {fmt_dt(r['starts_at'], '%d.%m %H:%M')}–{fmt_dt(r['ends_at'], '%H:%M')} · "
                 f"{r['room']} · {STATUS_RU[r['status']]}")
        with st.expander(title, expanded=r["status"] == "needs_review"):
            st.write(r["reason"])
            st.write(f"Присутствие: {r['present_seconds'] // 60} мин (нужно ≥ {-(-r['required_seconds'] // 60)} мин). "
                     f"Первая отметка: {fmt_dt(r['first_seen'], '%H:%M')}, последняя: {fmt_dt(r['last_seen'], '%H:%M')}.")
            note = st.text_input("Комментарий (обязателен для отклонения и для ручного зачёта)", key=f"note_{r['id']}")
            b1, b2, _ = st.columns([1, 1, 4])
            for col, decision, label in ((b1, "confirm", "✅ Подтвердить"), (b2, "reject", "❌ Отклонить")):
                if col.button(label, key=f"{decision}_{r['id']}"):
                    if api("POST", f"/attendance/{r['id']}/review", json={"decision": decision, "note": note}):
                        st.rerun()
    with st.expander("Все записи за месяц"):
        if records:
            st.dataframe(records_df(records), hide_index=True)
        else:
            st.write("Нет записей.")


def lessons_page():
    st.header("Расписание")
    mentors = api("GET", "/mentors") or []
    active = {m["full_name"]: m["id"] for m in mentors if m["is_active"]}
    with st.form("new_lesson"):
        name = st.selectbox("Ментор", list(active)) if active else None
        room = st.text_input("Кабинет (как в настройках камеры)", "Класс 1")
        day = st.date_input("Дата", date.today())
        c1, c2 = st.columns(2)
        start_t = c1.time_input("Начало", time(10, 0))
        end_t = c2.time_input("Конец", time(12, 0))
        if st.form_submit_button("Добавить урок") and name:
            res = api("POST", "/lessons", json={
                "mentor_id": active[name], "room": room,
                "starts_at": datetime.combine(day, start_t).isoformat(),
                "ends_at": datetime.combine(day, end_t).isoformat()})
            if res:
                st.success("Урок добавлен")
    year, month = period_picker("les")
    lessons = api("GET", "/lessons", params={"year": year, "month": month}) or []
    if lessons:
        df = pd.DataFrame([{"Дата": fmt_dt(l["starts_at"], "%d.%m.%Y"),
                            "Время": f"{fmt_dt(l['starts_at'], '%H:%M')}–{fmt_dt(l['ends_at'], '%H:%M')}",
                            "Ментор": l["mentor_name"], "Кабинет": l["room"]} for l in lessons])
        st.dataframe(df, hide_index=True)


# ---------------------------------------------------------------- администратор и руководитель

def mentors_page():
    st.header("Менторы")
    mentors = api("GET", "/mentors") or []
    if mentors:
        st.dataframe(pd.DataFrame([{
            "ID": m["id"], "ФИО": m["full_name"], "Оплата": PAY_RU[m["pay_model"]], "Ставка, сом": m["rate"],
            "Согласие на распознавание": "да" if m["consent_given"] else "нет",
            "Активен": "да" if m["is_active"] else "нет"} for m in mentors]), hide_index=True)
    with st.form("new_mentor"):
        st.subheader("Добавить ментора")
        full_name = st.text_input("ФИО")
        pay_model = st.selectbox("Модель оплаты", list(PAY_RU), format_func=PAY_RU.get)
        rate = st.number_input("Ставка, сом", 1.0, 1_000_000.0, 500.0)
        if st.form_submit_button("Создать") and api("POST", "/mentors", json={
                "full_name": full_name, "pay_model": pay_model, "rate": rate}):
            st.rerun()
    if mentors:
        st.subheader("Согласие на распознавание лица")
        by_name = {m["full_name"]: m for m in mentors}
        chosen = by_name[st.selectbox("Ментор", list(by_name), key="consent_mentor")]
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Согласие получено", disabled=chosen["consent_given"]):
            if api("POST", f"/mentors/{chosen['id']}/consent", json={"given": True}):
                st.rerun()
        if c2.button("Отозвать согласие", disabled=not chosen["consent_given"]):
            if api("POST", f"/mentors/{chosen['id']}/consent", json={"given": False}):
                st.warning("Согласие отозвано. Удалите фото ментора: python -m worker.enroll --forget "
                           f"{chosen['id']}")


def payroll_page(can_close: bool):
    st.header("Отчёт за период")
    year, month = period_picker("pay")
    rep = api("GET", "/payroll", params={"year": year, "month": month})
    if not rep:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Уроков", rep["total_lessons"])
    c2.metric("Часов", f"{rep['total_hours']:.1f}")
    c3.metric("Сумма, сом", f"{rep['total_amount']:,.0f}".replace(",", " "))
    c4.metric("Период", "закрыт 🔒" if rep["closed"] else "открыт")
    st.caption("Зарплата = подтверждённые уроки × ставка (за урок) или часы × ставка (за час).")
    if rep["rows"]:
        st.dataframe(pd.DataFrame([{"Ментор": r["mentor_name"], "Уроков": r["lessons"], "Часов": r["hours"],
                                    "Сумма, сом": r["amount"]} for r in rep["rows"]]), hide_index=True)
    csv_bytes = api("GET", "/payroll/export.csv", params={"year": year, "month": month})
    if csv_bytes:
        st.download_button("⬇️ Скачать CSV", csv_bytes, file_name=f"payroll_{year}_{month:02d}.csv", mime="text/csv")
    if can_close and not rep["closed"]:
        st.divider()
        st.write("После выплаты закройте период — правки задним числом станут невозможны.")
        if st.checkbox("Выплата произведена, закрыть период", key="confirm_close") and st.button("🔒 Закрыть период"):
            if api("POST", f"/payroll/{year}/{month}/close"):
                st.rerun()


def audit_page():
    st.header("Журнал изменений")
    rows = api("GET", "/audit", params={"limit": 200}) or []
    if rows:
        st.dataframe(pd.DataFrame([{"Время": fmt_dt(r["created_at"], "%d.%m.%Y %H:%M:%S"), "Пользователь": r["user_id"],
                                    "Действие": r["action"], "Объект": f"{r['entity']} #{r['entity_id']}",
                                    "Детали": r["details"]} for r in rows]), hide_index=True)


# ---------------------------------------------------------------- маршрутизация

PAGES = {
    "mentor": {"Мои уроки": mentor_page},
    "admin": {"Проверка отметок": review_page, "Расписание": lessons_page, "Менторы": mentors_page,
              "Отчёт": lambda: payroll_page(False), "Журнал": audit_page},
    "manager": {"Отчёт": lambda: payroll_page(True), "Менторы": mentors_page, "Расписание": lessons_page,
                "Журнал": audit_page},
}

if "token" not in st.session_state:
    login_page()
else:
    role = st.session_state["role"]
    st.sidebar.write(f"👤 {st.session_state['username']} ({role})")
    pages = PAGES[role]
    choice = st.sidebar.radio("Раздел", list(pages))
    if st.sidebar.button("Выйти"):
        st.session_state.clear()
        st.rerun()
    pages[choice]()
