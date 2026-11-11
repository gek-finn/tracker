"""Task Tracker — учебное приложение для управления задачами команды.

CRUD, фильтры, дашборд, канбан, планирование, повторяющиеся задачи,
отчёты, экспорт CSV/XLSX (этапы 1–6.2). Этап 7: production-конфигурация
через переменные окружения, защита изменяющих операций паролем
администратора, healthcheck, подготовка к Railway.

Переменные окружения:
  TRACKER_ENV               local (по умолчанию) или production;
                             не задан — распознаётся среда Railway
  TASK_TRACKER_SECRET        секретный ключ Flask; в production обязателен,
                             локально без него генерируется случайный
  TASK_TRACKER_DB            путь к SQLite-базе (по умолчанию tasks.db)
  TASK_TRACKER_DEBUG         1 — debug (только локально), 0 — production
  TASK_TRACKER_HOST/PORT     адрес локального запуска (127.0.0.1:5000)
  TRACKER_ADMIN_PASSWORD     пароль админа. В production обязателен и
                             закрывает ВСЁ, кроме /healthz (fail closed:
                             без пароля приложение не запускается).
                             Локально — опционально: защищает изменение
                             данных, просмотр остаётся открытым
  TRACKER_HTTPS              1 — cookie только по HTTPS (локально за прокси)
  TRACKER_PROXY_FIX          1 — доверять X-Forwarded-* от прокси платформы
"""
import calendar
import csv
import hmac
import io
import os
import secrets
import sqlite3
from collections import deque
from datetime import date, datetime, timedelta

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

import db as database

# --- Справочники этапа 1 --------------------------------------------------

PRIORITIES = ["Низкий", "Средний", "Высокий"]
STATUSES = ["Новая", "В работе", "Завершена"]

PRIORITY_CLASS = {
    "Низкий": "priority-low",
    "Средний": "priority-medium",
    "Высокий": "priority-high",
}
STATUS_CLASS = {
    "Новая": "status-new",
    "В работе": "status-progress",
    "Завершена": "status-done",
}

# Цвета статусов на диаграмме дашборда
STATUS_COLORS = {
    "Новая": "#64748b",
    "В работе": "#2563eb",
    "Завершена": "#059669",
}

DONE_STATUS = "Завершена"

# Колонки канбан-доски: статус в БД -> заголовок колонки
KANBAN_COLUMNS = [
    {"status": "Новая", "title": "Новые"},
    {"status": "В работе", "title": "В работе"},
    {"status": "Завершена", "title": "Завершены"},
]

# Русские названия дней недели: индекс 0 — понедельник (как у date.weekday())
WEEKDAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница",
                 "Суббота", "Воскресенье"]
# Винительный падеж — для текста «повторять каждую …»
WEEKDAY_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу",
               "субботу", "воскресенье"]

FREQUENCY_CHOICES = [
    ("none", "Не повторять"),
    ("daily", "Каждый день"),
    ("weekly", "Каждую неделю"),
    ("monthly", "Каждый месяц"),
]

# Максимум «догоняющих» экземпляров, создаваемых за один прогон расписания
# после простоя приложения. Более старые пропущенные даты пропускаются,
# чтобы не перегружать приложение после долгого выключения.
RECURRENCE_CATCH_UP_LIMIT = 30


# --- Режим работы и доступ администратора -------------------------------------
# TRACKER_ENV: local (по умолчанию) или production.
#   local      — сервер только на 127.0.0.1; без пароля всё открыто;
#                при заданном TRACKER_ADMIN_PASSWORD защищается изменение
#                данных (демо с паролем на localhost).
#   production — ВЕСЬ интерфейс закрыт Basic Auth (admin), кроме /healthz.
#                Без TRACKER_ADMIN_PASSWORD или TASK_TRACKER_SECRET
#                приложение отказывается запускаться (fail closed).
#                Debug принудительно выключен, cookie только по HTTPS.
# Если TRACKER_ENV не задан, среда Railway распознаётся автоматически
# (переменные RAILWAY_*) и включает production.

ADMIN_USER = "admin"


def _is_production():
    """Режим работы: True — production (полная защита и fail closed)."""
    mode = os.environ.get("TRACKER_ENV", "").strip().lower()
    if mode in ("production", "prod"):
        return True
    if mode:  # явно задан локальный (или иной) режим — он главнее
        return False
    # не задан: автодетект платформы Railway
    return any(name.startswith("RAILWAY") for name in os.environ)


def _admin_password():
    """Текущий пароль администратора (пустая строка — не задан)."""
    return os.environ.get("TRACKER_ADMIN_PASSWORD", "").strip()


# --- Создание приложения -------------------------------------------------------

def configure_app(app):
    """Конфигурация через переменные окружения.

    Production (fail closed): отсутствие пароля администратора или
    секретного ключа — отказ запуска; debug выключен принудительно;
    cookie помечаются Secure (HTTPS). Секреты в коде и логах не хранятся.
    Локальный режим: без пароля всё открыто, debug — по флагу.
    """
    production = _is_production()
    app.config["TRACKER_PRODUCTION"] = production

    secret = os.environ.get("TASK_TRACKER_SECRET", "").strip()
    if production:
        if not secret:
            raise RuntimeError(
                "TRACKER_ENV=production: задайте TASK_TRACKER_SECRET "
                "(длинная случайная строка). Работа со встроенным или "
                "случайным ключом в production запрещена — приложение "
                "не запущено.")
        app.config["SECRET_KEY"] = secret
        if not _admin_password():
            raise RuntimeError(
                "TRACKER_ENV=production: задайте TRACKER_ADMIN_PASSWORD "
                "в переменных окружения. Без пароля администратора "
                "production не запускается (fail closed).")
        app.config["DEBUG"] = False           # debug в production выключен
        app.config["SESSION_COOKIE_SECURE"] = True   # cookie только по HTTPS
    else:
        # локальный режим: случайный ключ без переменной — сессии живут
        # в рамках запуска, утечка ключа невозможна
        app.config["SECRET_KEY"] = secret if secret else secrets.token_hex(32)
        app.config["DEBUG"] = os.environ.get("TASK_TRACKER_DEBUG", "0") == "1"
        if os.environ.get("TRACKER_HTTPS", "0") == "1":
            app.config["SESSION_COOKIE_SECURE"] = True

    # Общие безопасные настройки cookie/сессий
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def create_app():
    app = Flask(__name__)
    configure_app(app)
    if _is_production() or os.environ.get("TRACKER_PROXY_FIX", "0") == "1":
        # За прокси платформы (Railway): реальные схема/хост из заголовков.
        # В production включается автоматически.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.teardown_appcontext(database.close_db)
    database.init_db()  # создаёт таблицы/мигрирует, данные не трогает
    return app


app = create_app()


# --- Защита доступа (этап 7) ---------------------------------------------------
# production: закрыто ВСЁ, кроме /healthz и статики (css/js данных не
# содержат); неавторизованные запросы получают 401.
# local + TRACKER_ADMIN_PASSWORD: закрыты изменение данных и страницы форм.
# local без пароля: авторизации нет (локальный запуск по-прежнему удобен).
# Пароль читается только из переменной окружения TRACKER_ADMIN_PASSWORD
# и сравнивается за постоянное время (hmac.compare_digest).

def _is_admin_authorized():
    auth = request.authorization
    if auth is None or auth.username != ADMIN_USER:
        return False
    return hmac.compare_digest(auth.password or "", _admin_password())


# Публичные всегда: healthcheck (данных не раскрывает) и статика
_PUBLIC_ENDPOINTS = ("healthz", "static")

# Страницы форм изменения (локальный режим с паролем): Basic-диалог
# появляется здесь, после чего браузер прикрепляет учётные данные ко всем
# запросам сайта (включая перетаскивание на канбане через fetch).
_PROTECTED_GET_ENDPOINTS = ("task_new", "task_edit", "recurrence_edit")


@app.before_request
def require_admin():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    write = request.method in ("POST", "PUT", "PATCH", "DELETE")
    form_page = request.endpoint in _PROTECTED_GET_ENDPOINTS
    password_set = bool(_admin_password())
    protected = _is_production() or (password_set and (write or form_page))
    if protected and not _is_admin_authorized():
        return Response(
            "Требуется авторизация администратора.",
            401,
            {"WWW-Authenticate": 'Basic realm="task-tracker-admin", charset="UTF-8"'},
        )
    return None


@app.route("/healthz")
def healthz():
    """Проверка работоспособности: приложение отвечает, база доступна."""
    try:
        database.get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        app.logger.exception("Healthcheck failed: database error")
        return jsonify({"ok": False, "db": "error"}), 503
    return jsonify({"ok": True, "db": "ok"})


# --- Фильтры и контекст шаблонов -------------------------------------------

@app.template_filter("ru_date")
def ru_date(value):
    """Дату из БД (ГГГГ-ММ-ДД) показываем в привычном виде ДД.ММ.ГГГГ."""
    if not value:
        return "—"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


@app.context_processor
def inject_constants():
    return {
        "PRIORITIES": PRIORITIES,
        "STATUSES": STATUSES,
        "PRIORITY_CLASS": PRIORITY_CLASS,
        "STATUS_CLASS": STATUS_CLASS,
        "STATUS_COLORS": STATUS_COLORS,
        "DONE_STATUS": DONE_STATUS,
        "WEEKDAY_NAMES": WEEKDAY_NAMES,
        "FREQUENCY_CHOICES": FREQUENCY_CHOICES,
    }


# --- Контроль сроков ---------------------------------------------------------

def _today_iso():
    """Текущая локальная дата в формате ГГГГ-ММ-ДД (для сравнения со сроками)."""
    return date.today().isoformat()


def _parse_date_arg(value):
    """Безопасный разбор даты из строки запроса. None при некорректном значении."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _overdue_condition():
    """SQL-условие «задача просрочена»: срок прошёл, задача не завершена.

    Задачи без срока (NULL или пустая строка) просроченными не считаются.
    """
    return "due_date IS NOT NULL AND due_date != '' AND due_date < ? AND status != ?"


def _with_overdue_flag(rows):
    """Добавляет каждой задаче вычисляемый признак is_overdue."""
    today = _today_iso()
    tasks = []
    for row in rows:
        task = dict(row)
        task["is_overdue"] = bool(
            task["due_date"]
            and task["due_date"] < today
            and task["status"] != DONE_STATUS
        )
        tasks.append(task)
    return tasks


# --- Отчёты и аналитика (этап 6.1) -------------------------------------------
# Общие фильтры для списка задач и экспорта CSV, период аналитики дашборда,
# статистика по исполнителям и проектам.

# Служебные значения фильтров: «задачи без исполнителя / без проекта».
# В _distinct_values пустые значения не попадают, поэтому фильтруются отдельным
# условием SQL, а не значением из списка.
NO_ASSIGNEE_VALUE = "__none__"
NO_PROJECT_VALUE = "__none__"

PERIOD_MODES = ("all", "7d", "30d", "custom")


def _list_filters_from_request():
    """Фильтры списка задач из адресной строки (общие для списка и экспорта CSV)."""
    return {
        "q": request.args.get("q", "").strip(),
        "status": request.args.get("status", "").strip(),
        "priority": request.args.get("priority", "").strip(),
        "assignee": request.args.get("assignee", "").strip(),
        "project": request.args.get("project", "").strip(),
        "overdue": request.args.get("overdue", "").strip() in ("on", "1", "true"),
    }


def _empty_filters():
    """Фильтры «без ограничений» — для дашборда и экспорта без параметров."""
    return {"q": "", "status": "", "priority": "", "assignee": "",
            "project": "", "overdue": False}


def _tasks_query_conditions(filters):
    """Условия WHERE по фильтрам (кроме поиска — он выполняется на Python).

    Возвращает (conditions, params) для сборки запроса.
    """
    conditions, params = [], []
    if filters["status"] in STATUSES:
        conditions.append("status = ?")
        params.append(filters["status"])
    if filters["priority"] in PRIORITIES:
        conditions.append("priority = ?")
        params.append(filters["priority"])
    if filters["assignee"] == NO_ASSIGNEE_VALUE:
        conditions.append("(assignee IS NULL OR TRIM(assignee) = '')")
    elif filters["assignee"]:
        conditions.append("assignee = ?")
        params.append(filters["assignee"])
    if filters["project"] == NO_PROJECT_VALUE:
        conditions.append("(project IS NULL OR TRIM(project) = '')")
    elif filters["project"]:
        conditions.append("project = ?")
        params.append(filters["project"])
    if filters["overdue"]:
        conditions.append(_overdue_condition())
        params += [_today_iso(), DONE_STATUS]
    return conditions, params


def _filtered_task_rows(filters, extra_conditions=(), extra_params=()):
    """Отбор задач по фильтрам (+ доп. условия, например период дашборда).

    Сортировка и поиск — те же, что на странице списка задач.
    """
    conditions, params = _tasks_query_conditions(filters)
    conditions += list(extra_conditions)
    params += list(extra_params)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = database.get_db().execute(
        "SELECT * FROM tasks" + where + " ORDER BY (due_date IS NULL), due_date, id DESC",
        params,
    ).fetchall()

    # Поиск по названию и описанию — на Python, чтобы работал без учёта
    # регистра и для кириллицы (LIKE в SQLite регистрозависим для не-ASCII).
    if filters["q"]:
        needle = filters["q"].lower()
        rows = [r for r in rows
                if needle in (r["title"] + " " + r["description"]).lower()]
    return rows


def _report_period_from_request():
    """Период аналитики из адресной строки: (period, date_from, date_to, error).

    Задачи попадают в период по СРОКУ ВЫПОЛНЕНИЯ (due_date): это единственная
    операционная дата задач в базе — отдельной даты завершения в схеме нет.
    Задачи без срока учитываются только в периоде «Все время».
    При некорректном произвольном периоде возвращается ошибка, period — «all».
    """
    period = (request.args.get("period") or "all").strip()
    if period not in PERIOD_MODES:
        period = "all"
    date_from = date_to = None
    error = None
    if period in ("7d", "30d"):
        date_to = date.today()
        date_from = date_to - timedelta(days=6 if period == "7d" else 29)
    elif period == "custom":
        date_from = _parse_date_arg(request.args.get("date_from", ""))
        date_to = _parse_date_arg(request.args.get("date_to", ""))
        if date_from is None or date_to is None:
            error = "Произвольный период: укажите корректные даты начала и окончания."
        elif date_from > date_to:
            error = "Дата начала периода не может быть позже даты окончания."
        if error:
            period = "all"
    return period, date_from, date_to, error


def _period_conditions(period, date_from, date_to):
    """SQL-условие попадания задачи в период (по сроку выполнения).

    Для «all» ограничений нет — задачи без срока также участвуют в отчёте.
    """
    if period == "all":
        return [], []
    return (["due_date IS NOT NULL", "due_date != ''",
             "due_date >= ?", "due_date <= ?"],
            [date_from.isoformat(), date_to.isoformat()])


def _report_period_title(period, date_from, date_to):
    """Человеческое название периода для заголовков отчётов."""
    if period == "custom":
        return "за %s – %s" % (date_from.strftime("%d.%m.%Y"),
                               date_to.strftime("%d.%m.%Y"))
    if period == "7d":
        return "за последние 7 дней"
    if period == "30d":
        return "за последние 30 дней"
    return "за всё время"


def _group_report(tasks, field):
    """Статистика по исполнителям (field='assignee') или проектам (field='project').

    Работает по задачам с вычисленным признаком is_overdue (единое правило
    просрочки проекта). Пустое значение поля образует отдельную группу
    «Без исполнителя» / «Без проекта» и идёт в конце списка.
    """
    groups = {}
    for task in tasks:
        groups.setdefault((task[field] or "").strip(), []).append(task)

    rows = []
    for name, items in groups.items():
        total = len(items)
        done = sum(1 for t in items if t["status"] == DONE_STATUS)
        rows.append({
            "name": name,
            "total": total,
            "done": done,
            "undone": total - done,
            "in_progress": sum(1 for t in items if t["status"] == "В работе"),
            "overdue": sum(1 for t in items if t["is_overdue"]),
            "percent": round(done * 100 / total) if total else 0,
        })
    # Сначала группы с наибольшим числом задач; пустая группа — в конце.
    rows.sort(key=lambda r: (-r["total"], r["name"] == "", r["name"].lower()))
    return rows


def _report_row_urls(rows, field):
    """Добавляет каждой строке отчёта ссылку на список задач с фильтром."""
    for row in rows:
        value = row["name"] or (NO_ASSIGNEE_VALUE if field == "assignee"
                                else NO_PROJECT_VALUE)
        row["url"] = url_for("index", **{field: value})
    return rows


# Защита от CSV-инъекций: Excel исполняет значения, начинающиеся с этих
# символов, как формулы. Такие значения предваряются апострофом и
# открываются как обычный текст.
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Безопасное для Excel значение ячейки CSV."""
    text = "" if value is None else str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


# --- Повторяющиеся задачи -----------------------------------------------------

def _monthly_date(year, month, month_day):
    """Дата месяца с «прижатием» числа к последнему дню месяца.

    Правило: если выбранного числа в месяце нет (31-е в коротких месяцах,
    29–31 февраля), экземпляр создаётся на последний календарный день месяца.
    """
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(month_day, last_day))


def _next_occurrence(from_date, frequency, weekday, month_day):
    """Следующая дата расписания СТРОГО ПОСЛЕ from_date."""
    if frequency == "daily":
        return from_date + timedelta(days=1)
    if frequency == "weekly":
        ahead = (weekday - from_date.weekday()) % 7
        if ahead == 0:
            ahead = 7
        return from_date + timedelta(days=ahead)
    # monthly: то же число месяца (с правилом последнего дня).
    # Сначала проверяем дату в ТЕКУЩЕМ месяце (она может быть ещё впереди),
    # затем — в следующем.
    candidate = _monthly_date(from_date.year, from_date.month, month_day)
    if candidate > from_date:
        return candidate
    year, month = from_date.year, from_date.month + 1
    if month > 12:
        month, year = 1, year + 1
    return _monthly_date(year, month, month_day)


def _recurrence_rule_text(frequency, weekday, month_day):
    """Человеческое описание правила: «каждую неделю, пятницу»."""
    if frequency == "daily":
        return "каждый день"
    if frequency == "weekly":
        return "каждую неделю, " + WEEKDAY_ACC[int(weekday)]
    return "каждый месяц, " + str(int(month_day)) + "-го числа"


def _recurrence_frequency_label(frequency):
    for value, label in FREQUENCY_CHOICES:
        if value == frequency:
            return label
    return ""


def _recurrences_map():
    """Словарь {id: данные расписания} для отметок на задачах."""
    rows = database.get_db().execute("SELECT * FROM recurrences").fetchall()
    result = {}
    for r in rows:
        result[r["id"]] = {
            "text": _recurrence_rule_text(r["frequency"], r["weekday"], r["month_day"]),
            "next": ru_date(r["next_date"]),
            "paused": bool(r["paused"]),
            "stopped": bool(r["stopped"]),
        }
    return result


def process_recurrences():
    """Создаёт экземпляры повторяющихся задач с уже наступившими датами.

    Вызывается при старте приложения и при открытии основных страниц.
    Правила:
      - заранее (на будущее) экземпляры не создаются — только на даты <= сегодня;
      - при простое создаётся не более RECURRENCE_CATCH_UP_LIMIT ближайших
        пропущенных экземпляров на расписание, более старые даты пропускаются;
      - уникальный индекс (recurrence_id, recurrence_date) и транзакция
        BEGIN IMMEDIATE защищают от дублей, в том числе при параллельных запросах.
    """
    con = database.get_db()
    today = date.today()
    schedules = con.execute(
        "SELECT * FROM recurrences WHERE paused = 0 AND stopped = 0"
    ).fetchall()

    for rec in schedules:
        try:
            nxt = date.fromisoformat(rec["next_date"] or "")
        except ValueError:
            continue
        end = None
        if rec["end_date"]:
            try:
                end = date.fromisoformat(rec["end_date"])
            except ValueError:
                end = None

        dates = deque(maxlen=RECURRENCE_CATCH_UP_LIMIT)
        while nxt <= today and (end is None or nxt <= end):
            dates.append(nxt)  # deque хранит только последние N дат
            nxt = _next_occurrence(nxt, rec["frequency"], rec["weekday"], rec["month_day"])

        if not dates:
            continue

        # при простое — только ближайшие к сегодняшнему дню (лишние отброшены deque)
        try:
            con.execute("BEGIN IMMEDIATE")
            for d in dates:
                con.execute(
                    "INSERT OR IGNORE INTO tasks (title, description, assignee, "
                    "project, priority, status, due_date, recurrence_id, "
                    "recurrence_date) VALUES (?, ?, ?, ?, ?, 'Новая', ?, ?, ?)",
                    (rec["title"], rec["description"], rec["assignee"], rec["project"],
                     rec["priority"], d.isoformat(), rec["id"], d.isoformat()),
                )
            con.execute(
                "UPDATE recurrences SET last_generated = ?, next_date = ? WHERE id = ?",
                (dates[-1].isoformat(), nxt.isoformat(), rec["id"]),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise


def _recurrence_from_request():
    """Настройки повторения из формы задачи."""
    frequency = request.form.get("recurrence_frequency", "none")
    if frequency not in ("none", "daily", "weekly", "monthly"):
        frequency = "none"
    rc = {
        "frequency": frequency,
        "weekday": request.form.get("recurrence_weekday", "").strip(),
        "month_day": request.form.get("recurrence_month_day", "").strip(),
        "end": request.form.get("recurrence_end", "").strip(),
    }
    rc["errors"] = []
    if rc["frequency"] != "none":
        if rc["frequency"] == "weekly":
            try:
                rc["weekday"] = int(rc["weekday"])
                if not 0 <= rc["weekday"] <= 6:
                    raise ValueError
            except ValueError:
                rc["errors"].append("Выберите день недели для еженедельного повторения.")
        if rc["frequency"] == "monthly":
            try:
                rc["month_day"] = int(rc["month_day"])
                if not 1 <= rc["month_day"] <= 31:
                    raise ValueError
            except ValueError:
                rc["errors"].append("Число месяца должно быть от 1 до 31.")
        if rc["end"]:
            try:
                rc["end_date"] = date.fromisoformat(rc["end"])
            except ValueError:
                rc["errors"].append("Дата окончания повторения: неверная дата.")
    return rc


def _recurrence_form_errors(rc, due_date):
    """Дополнительные проверки повторения, зависящие от срока задачи."""
    errors = list(rc["errors"])
    if rc["frequency"] == "none":
        return errors
    if not due_date:
        errors.append("Для повторяющейся задачи укажите срок выполнения.")
        return errors
    start = date.fromisoformat(due_date)
    if rc.get("end_date") and rc["end_date"] < start:
        errors.append("Дата окончания повторения не может быть раньше срока задачи.")
    return errors


def _insert_recurrence(con, form, rc, start_date):
    """Создаёт расписание и возвращает его id. start_date — срок задачи-источника."""
    end_iso = rc["end_date"].isoformat() if rc.get("end_date") else None
    weekday = rc["weekday"] if rc["frequency"] == "weekly" else None
    month_day = rc["month_day"] if rc["frequency"] == "monthly" else None
    cur = con.execute(
        "INSERT INTO recurrences (title, description, assignee, project, priority, "
        "frequency, weekday, month_day, start_date, end_date, next_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (form["title"], form["description"], form["assignee"], form["project"],
         form["priority"], rc["frequency"], weekday, month_day,
         start_date.isoformat(), end_iso, start_date.isoformat()),
    )
    rec_id = cur.lastrowid
    nxt = _next_occurrence(start_date, rc["frequency"], weekday, month_day)
    con.execute("UPDATE recurrences SET next_date = ? WHERE id = ?",
                (nxt.isoformat(), rec_id))
    return rec_id


# --- Вспомогательные функции ------------------------------------------------

def _load_task_or_404(task_id):
    task = database.get_db().execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if task is None:
        abort(404)
    return task


def _distinct_values(column):
    """Существующие значения поля — для подсказок в форме (datalist)."""
    if column not in ("assignee", "project"):
        raise ValueError("Недопустимое имя поля")
    rows = database.get_db().execute(
        "SELECT DISTINCT " + column + " AS value FROM tasks "
        "WHERE TRIM(" + column + ") != '' ORDER BY value COLLATE NOCASE"
    ).fetchall()
    return [row["value"] for row in rows]


def _form_from_request(task=None):
    """Данные формы: из POST (при отправке) или из задачи (при открытии)."""
    form = {
        "title": task["title"] if task else "",
        "description": task["description"] if task else "",
        "assignee": task["assignee"] if task else "",
        "project": task["project"] if task else "",
        "priority": task["priority"] if task else "Средний",
        "status": task["status"] if task else "Новая",
        "due_date": (task["due_date"] or "") if task else "",
    }
    if request.method == "POST":
        for key in form:
            form[key] = request.form.get(key, "").strip()
    return form


def _validate(form):
    errors = []
    if not form["title"]:
        errors.append("Укажите название задачи.")
    elif len(form["title"]) > 200:
        errors.append("Название не должно превышать 200 символов.")
    if len(form["description"]) > 5000:
        errors.append("Описание не должно превышать 5000 символов.")
    if form["priority"] not in PRIORITIES:
        errors.append("Приоритет должен быть одним из: " + ", ".join(PRIORITIES) + ".")
    if form["status"] not in STATUSES:
        errors.append("Статус должен быть одним из: " + ", ".join(STATUSES) + ".")
    if form["due_date"]:
        try:
            datetime.strptime(form["due_date"], "%Y-%m-%d")
        except ValueError:
            errors.append("Срок выполнения: неверная дата.")
    return errors


# --- Защита от повторной отправки формы -------------------------------------
# Каждая открытая форма получает уникальный токен. После успешного сохранения
# токен запоминается в сессии, и повторный POST с тем же токеном (двойной
# клик, повторное нажатие Enter) уже не создаёт вторую запись.

FORM_TOKEN_LIMIT = 50  # сколько последних токенов хранить в сессии


def _new_form_token():
    """Случайный токен для новой формы."""
    return secrets.token_hex(16)


def _is_repeat_submission(token):
    """True, если форму с таким токеном уже успешно отправляли."""
    return not token or token in session.get("used_form_tokens", [])


def _mark_token_used(token):
    """Запоминаем токен как израсходованный (только после успешной записи)."""
    if not token:
        return
    used = session.get("used_form_tokens", [])
    if token not in used:
        used.append(token)
        session["used_form_tokens"] = used[-FORM_TOKEN_LIMIT:]


# --- Маршруты ----------------------------------------------------------------

@app.route("/")
def index():
    process_recurrences()  # актуализируем повторяющиеся задачи
    con = database.get_db()

    # --- Фильтрация (общая с экспортом CSV) ---
    filters = _list_filters_from_request()
    rows = _filtered_task_rows(filters)

    total_count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    tasks = _with_overdue_flag(rows)

    return render_template(
        "index.html",
        tasks=tasks,
        total_count=total_count,
        has_any=total_count > 0,
        filters=filters,
        filters_active=any([filters["q"], filters["status"], filters["priority"],
                            filters["assignee"], filters["project"], filters["overdue"]]),
        assignees=_distinct_values("assignee"),
        projects=_distinct_values("project"),
        recurrences=_recurrences_map(),
    )


@app.route("/export/csv")
def export_csv():
    """Экспорт задач в CSV с учётом фильтров (и периода дашборда, если задан).

    - UTF-8 с BOM: русский текст корректно открывается в Excel;
    - разделитель «;» (привычен для русской локали Excel);
    - кавычки, разделители и переносы строк экранирует модуль csv;
    - значения, начинающиеся с = + - @ (или управляющих символов),
      предваряются апострофом — защита от CSV-инъекций;
    - данные в базе не изменяются.
    """
    filters = _list_filters_from_request()
    period, date_from, date_to, _error = _report_period_from_request()
    period_conds, period_params = _period_conditions(period, date_from, date_to)
    rows = _filtered_task_rows(filters, period_conds, period_params)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ID", "Название", "Описание", "Исполнитель", "Проект",
                     "Статус", "Приоритет", "Срок выполнения"])
    for row in rows:
        writer.writerow([
            _csv_safe(row["id"]),
            _csv_safe(row["title"]),
            _csv_safe(row["description"]),
            _csv_safe(row["assignee"]),
            _csv_safe(row["project"]),
            _csv_safe(row["status"]),
            _csv_safe(row["priority"]),
            _csv_safe(ru_date(row["due_date"])),
        ])

    filename = "tasks_%s.csv" % date.today().isoformat()
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=" + filename},
    )


# --- Экспорт Excel (этап 6.2; печать доработана на этапе 7) -------------------
# Колонки отчёта: (заголовок, ширина). Те же поля, что и в CSV.
# Ширины подобраны так, чтобы 8 колонок при A4 альбомной и fitToWidth=1
# печатались максимально крупным шрифтом.
XLSX_COLUMNS = [
    ("ID", 6),
    ("Название", 36),
    ("Описание", 44),
    ("Исполнитель", 16),
    ("Проект", 16),
    ("Статус", 12),
    ("Приоритет", 12),
    ("Срок выполнения", 17),
]

# Высота строки данных (pt) и её максимум: длинные тексты переносятся,
# но строки не становятся чрезмерно высокими.
XLSX_ROW_HEIGHT = 15
XLSX_MAX_ROW_HEIGHT = 105  # ~7 видимых строк текста


def _xlsx_set_text(cell, value):
    """Записывает значение в ячейку как ТЕКСТ.

    openpyxl считает строки, начинающиеся с «=», формулами — принудительно
    возвращаем текстовый тип, чтобы Excel показал пользовательский ввод
    как текст и не исполнял его как формулу.
    """
    cell.value = "" if value is None else str(value)
    if cell.data_type == "f":
        cell.data_type = "s"


def _xlsx_text_lines(text, width):
    """Оценка числа строк текста при переносе по ширине колонки."""
    if not text:
        return 1
    lines = 0
    for part in str(text).split("\n"):
        lines += max(1, -(-len(part) // width))  # округление вверх
    return lines


@app.route("/export/xlsx")
def export_xlsx():
    """Экспорт задач в Excel (.xlsx) — те же фильтры, поиск и период, что и CSV.

    Оформление: жирная контрастная шапка, чередование фона строк, тонкие
    границы, автофильтр, закреплённая первая строка, перенос длинного текста,
    настоящие даты Excel с форматом ДД.ММ.ГГГГ.

    Печать: A4 альбомная, все 8 колонок по ширине одной страницы
    (fitToPage, fitToWidth=1, fitToHeight=0 — по высоте сколько нужно),
    строка заголовков повторяется на каждой странице, область печати —
    только фактические данные, узкие поля.
    """
    filters = _list_filters_from_request()
    period, date_from, date_to, _error = _report_period_from_request()
    period_conds, period_params = _period_conditions(period, date_from, date_to)
    rows = _filtered_task_rows(filters, period_conds, period_params)

    wb = Workbook()
    ws = wb.active
    ws.title = "Задачи"

    # --- Шапка: контрастный фон, жирный белый шрифт покрупнее ---
    header_fill = PatternFill("solid", fgColor="2563EB")
    header_font = Font(bold=True, color="FFFFFF", size=12)
    header_align = Alignment(horizontal="center", vertical="center",
                             wrap_text=True)
    for col, (title, width) in enumerate(XLSX_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        ws.column_dimensions[get_column_letter(col)].width = width
    # высоты хватает на перенос длинного заголовка «Срок выполнения»
    ws.row_dimensions[1].height = 32

    data_font = Font(size=11)
    thin = Side(style="thin", color="D9E2EC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(vertical="top", wrap_text=True)
    plain_top = Alignment(vertical="top")
    center_top = Alignment(horizontal="center", vertical="top")
    stripe_fill = PatternFill("solid", fgColor="F1F5F9")

    # --- Данные ---
    for i, row in enumerate(rows):
        r = i + 2
        due = None
        if row["due_date"]:
            try:
                due = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
            except ValueError:
                due = None

        values = [row["id"], row["title"], row["description"],
                  row["assignee"], row["project"], row["status"],
                  row["priority"], due]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col)
            if col == 8 and due is not None:
                cell.value = due                      # настоящая дата Excel
                cell.number_format = "DD.MM.YYYY"
            elif col == 1:
                cell.value = value
            else:
                _xlsx_set_text(cell, value)           # всегда текст
            cell.border = border
            cell.font = data_font           # читаемый размер при печати
            cell.alignment = center_top if col in (1, 8) else (
                wrap_top if col in (2, 3) else plain_top)
            if i % 2 == 1:
                cell.fill = stripe_fill

        # Высота строки — по самому «многострочному» тексту, с ограничением
        lines = max(_xlsx_text_lines(row["title"], 40),
                    _xlsx_text_lines(row["description"], 50), 1)
        ws.row_dimensions[r].height = min(XLSX_ROW_HEIGHT * lines,
                                          XLSX_MAX_ROW_HEIGHT)

    # --- Автофильтр и закрепление первой строки ---
    last_col = get_column_letter(len(XLSX_COLUMNS))
    data_range = "A1:%s%d" % (last_col, len(rows) + 1)
    ws.auto_filter.ref = data_range
    ws.freeze_panes = "A2"

    # --- Настройки печати ---
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.4, bottom=0.4,
                                  header=0.2, footer=0.2)
    ws.print_title_rows = "1:1"          # шапка на каждой печатной странице
    ws.print_area = data_range           # печать только фактических данных

    buf = io.BytesIO()
    wb.save(buf)
    filename = "tasks_%s.xlsx" % date.today().isoformat()
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=" + filename},
    )


@app.route("/dashboard")
def dashboard():
    """Дашборд: статистика, распределение по статусам, отчёты по исполнителям
    и проектам. Все показатели считаются по задачам выбранного периода
    (по умолчанию — за всё время)."""
    process_recurrences()

    period, date_from, date_to, period_error = _report_period_from_request()
    if period_error:
        flash(period_error, "error")
    period_conds, period_params = _period_conditions(period, date_from, date_to)

    rows = _filtered_task_rows(_empty_filters(), period_conds, period_params)
    tasks = _with_overdue_flag(rows)

    total = len(tasks)
    counts = {status: 0 for status in STATUSES}
    for task in tasks:
        if task["status"] in counts:
            counts[task["status"]] += 1
    overdue = sum(1 for task in tasks if task["is_overdue"])

    done = counts[DONE_STATUS]
    done_percent = round(done * 100 / total) if total else 0

    # Сегменты круговой диаграммы — CSS conic-gradient, без библиотек
    segments, offset = [], 0
    if total:
        for status in STATUSES:
            count = counts[status]
            if not count:
                continue
            start = offset * 100 / total
            offset += count
            end = offset * 100 / total
            segments.append("%s %.2f%% %.2f%%" % (STATUS_COLORS[status], start, end))
    donut_css = ("conic-gradient(" + ", ".join(segments) + ")") if segments \
        else "conic-gradient(#e2e8f0 0 100%)"

    # Отчёты по исполнителям и проектам (по задачам выбранного периода)
    assignee_rows = _report_row_urls(_group_report(tasks, "assignee"), "assignee")
    project_rows = _report_row_urls(_group_report(tasks, "project"), "project")

    export_args = {"period": period}
    if period == "custom":
        export_args["date_from"] = date_from.isoformat()
        export_args["date_to"] = date_to.isoformat()

    return render_template(
        "dashboard.html",
        total=total,
        counts=counts,
        overdue=overdue,
        done=done,
        done_percent=done_percent,
        donut_css=donut_css,
        period=period,
        period_from=date_from.isoformat() if date_from else "",
        period_to=date_to.isoformat() if date_to else "",
        period_title=_report_period_title(period, date_from, date_to),
        period_error=period_error,
        assignee_rows=assignee_rows,
        project_rows=project_rows,
        export_csv_url=url_for("export_csv", **export_args),
        export_xlsx_url=url_for("export_xlsx", **export_args),
    )


@app.route("/kanban")
def kanban():
    """Канбан-доска: карточки распределены по колонкам согласно статусу."""
    process_recurrences()
    rows = database.get_db().execute(
        "SELECT * FROM tasks ORDER BY (due_date IS NULL), due_date, id DESC"
    ).fetchall()
    tasks = _with_overdue_flag(rows)

    columns = []
    for column in KANBAN_COLUMNS:
        columns.append({
            "status": column["status"],
            "title": column["title"],
            "tasks": [t for t in tasks if t["status"] == column["status"]],
        })
    # today передаём на клиент: признак просрочки всегда считается по дате
    # сервера, а не по часам браузера (единое правило со списком и дашбордом)
    return render_template("kanban.html", columns=columns, today=_today_iso(),
                           recurrences=_recurrences_map())


def _static_version(filename):
    """Метка времени файла — для версионирования URL статики (?v=...).

    После изменения JS/CSS браузер гарантированно берёт свежую версию,
    а не копию из кэша."""
    try:
        return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
    except OSError:
        return "0"


app.jinja_env.globals["static_version"] = _static_version


@app.route("/task/<int:task_id>/status", methods=("POST",))
def task_status(task_id):
    """Изменение статуса существующей задачи (перетаскивание или список).

    Новая задача не создаётся — меняется только статус записи по её ID.
    Используется и канбан-доской (JSON), и формами (form-encoded).
    """
    data = request.get_json(silent=True) or request.form
    new_status = (data.get("status") or "").strip()

    if new_status not in STATUSES:
        return jsonify({"ok": False, "error": "Недопустимое значение статуса."}), 400

    con = database.get_db()
    task = con.execute(
        "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if task is None:
        return jsonify({"ok": False, "error": "Задача не найдена."}), 404

    if task["status"] != new_status:
        con.execute(
            "UPDATE tasks SET status = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (new_status, task_id),
        )
        con.commit()

    return jsonify({"ok": True, "status": new_status, "task_id": task_id})


@app.route("/task/<int:task_id>/view")
def task_view(task_id):
    """Данные одной задачи для модального окна «Просмотр» (только чтение).

    Клиент вставляет тексты через textContent, поэтому HTML или JavaScript
    из описания задачи не исполняется — пользовательский текст остаётся текстом.
    """
    row = database.get_db().execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "Задача не найдена."}), 404

    task = _with_overdue_flag([row])[0]
    recurrence = None
    if task["recurrence_id"]:
        rec = database.get_db().execute(
            "SELECT * FROM recurrences WHERE id = ?", (task["recurrence_id"],)
        ).fetchone()
        if rec:
            recurrence = {
                "text": _recurrence_rule_text(rec["frequency"], rec["weekday"], rec["month_day"]),
                "next": ru_date(rec["next_date"]),
                "paused": bool(rec["paused"]),
                "stopped": bool(rec["stopped"]),
            }
    return jsonify({
        "ok": True,
        "task": {
            "id": task["id"],
            "title": task["title"],
            "description": task["description"] or "",
            "assignee": task["assignee"],
            "project": task["project"],
            "priority": task["priority"],
            "priority_class": PRIORITY_CLASS.get(task["priority"], ""),
            "status": task["status"],
            "status_class": STATUS_CLASS.get(task["status"], ""),
            "due_date": ru_date(task["due_date"]),
            "overdue": task["is_overdue"],
            "recurrence": recurrence,
        },
    })


# --- Управление расписаниями ---------------------------------------------------

def _recurrence_status(rec):
    """Статус расписания для отображения."""
    if rec["stopped"]:
        return "stopped"
    if rec["paused"]:
        return "paused"
    if rec["end_date"]:
        try:
            if date.fromisoformat(rec["next_date"]) > date.fromisoformat(rec["end_date"]):
                return "finished"
        except ValueError:
            pass
    return "active"


RECURRENCE_STATUS_TEXT = {
    "active": "Активно",
    "paused": "Приостановлено",
    "finished": "Завершено",
    "stopped": "Остановлено",
}


def _load_recurrence_or_404(rec_id):
    rec = database.get_db().execute(
        "SELECT * FROM recurrences WHERE id = ?", (rec_id,)
    ).fetchone()
    if rec is None:
        abort(404)
    return rec


@app.route("/recurrences")
def recurrences_list():
    """Раздел «Повторяющиеся задачи»: просмотр и управление расписаниями."""
    process_recurrences()
    con = database.get_db()
    rows = con.execute(
        "SELECT r.*, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.recurrence_id = r.id) AS created_count, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.recurrence_id = r.id "
        " AND t.status = 'Завершена') AS done_count "
        "FROM recurrences r ORDER BY r.id DESC"
    ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["rule_text"] = _recurrence_rule_text(r["frequency"], r["weekday"], r["month_day"])
        item["status_code"] = _recurrence_status(r)
        item["status_text"] = RECURRENCE_STATUS_TEXT[item["status_code"]]
        items.append(item)
    return render_template("recurrences.html", items=items)


@app.route("/recurrence/<int:rec_id>/edit", methods=("GET", "POST"))
def recurrence_edit(rec_id):
    """Изменение расписания: шаблон будущих экземпляров и правило повторения.

    Уже созданные задачи не переписываются — изменение действует только
    на будущие экземпляры.
    """
    rec = _load_recurrence_or_404(rec_id)

    if request.method == "POST" and _is_repeat_submission(request.form.get("form_token", "")):
        flash("Расписание уже было обновлено: повторная отправка формы отклонена.", "success")
        return redirect(url_for("recurrences_list"))

    errors = []
    if request.method == "POST":
        frequency = request.form.get("frequency", "")
        if frequency not in ("daily", "weekly", "monthly"):
            errors.append("Выберите тип повторения.")
        weekday = request.form.get("weekday", "").strip()
        month_day = request.form.get("month_day", "").strip()
        end = request.form.get("end_date", "").strip()
        try:
            if frequency == "weekly":
                weekday = int(weekday)
                if not 0 <= weekday <= 6:
                    raise ValueError
            else:
                weekday = None
            if frequency == "monthly":
                month_day = int(month_day)
                if not 1 <= month_day <= 31:
                    raise ValueError
            else:
                month_day = None
            end_date = date.fromisoformat(end) if end else None
        except ValueError:
            errors.append("Проверьте день недели, число месяца или дату окончания.")
            weekday = month_day = None
            end_date = None

        if not errors:
            con = database.get_db()
            base = date.today()
            if rec["last_generated"]:
                try:
                    base = max(base, date.fromisoformat(rec["last_generated"]))
                except ValueError:
                    pass
            new_next = _next_occurrence(base, frequency, weekday, month_day)
            con.execute(
                "UPDATE recurrences SET title = ?, description = ?, assignee = ?, "
                "project = ?, priority = ?, frequency = ?, weekday = ?, month_day = ?, "
                "end_date = ?, next_date = ? WHERE id = ?",
                (request.form.get("title", "").strip() or rec["title"],
                 request.form.get("description", "").strip(),
                 request.form.get("assignee", "").strip(),
                 request.form.get("project", "").strip(),
                 request.form.get("priority", "").strip() or "Средний",
                 frequency, weekday, month_day,
                 end_date.isoformat() if end_date else None,
                 new_next.isoformat(), rec_id),
            )
            con.commit()
            _mark_token_used(request.form.get("form_token", ""))
            flash("Расписание обновлено: действует на будущие экземпляры.", "success")
            return redirect(url_for("recurrences_list"))

    token = (request.form.get("form_token", "") if request.method == "POST"
             else _new_form_token())
    return render_template(
        "recurrence_form.html",
        rec=rec,
        rule_text=_recurrence_rule_text(rec["frequency"], rec["weekday"], rec["month_day"]),
        errors=errors,
        form_token=token,
    )


@app.route("/recurrence/<int:rec_id>/pause", methods=("POST",))
def recurrence_pause(rec_id):
    """Приостановить повторение. Время паузы не компенсируется:
    после возобновления следующий экземпляр — ближайшая дата по расписанию
    после текущего дня."""
    rec = _load_recurrence_or_404(rec_id)
    con = database.get_db()
    con.execute("UPDATE recurrences SET paused = 1 WHERE id = ?", (rec_id,))
    con.commit()
    flash("Повторение «" + rec["title"] + "» приостановлено.", "success")
    return redirect(url_for("recurrences_list"))


@app.route("/recurrence/<int:rec_id>/resume", methods=("POST",))
def recurrence_resume(rec_id):
    """Возобновить приостановленное повторение."""
    rec = _load_recurrence_or_404(rec_id)
    con = database.get_db()
    # Пропущенные за время паузы даты не навёрстываются
    base = date.today()
    if rec["last_generated"]:
        try:
            base = max(base, date.fromisoformat(rec["last_generated"]))
        except ValueError:
            pass
    new_next = _next_occurrence(base, rec["frequency"], rec["weekday"], rec["month_day"])
    con.execute("UPDATE recurrences SET paused = 0, next_date = ? WHERE id = ?",
                (new_next.isoformat(), rec_id))
    con.commit()
    flash("Повторение «" + rec["title"] + "» возобновлено. Следующий экземпляр: "
          + ru_date(new_next.isoformat()) + ".", "success")
    return redirect(url_for("recurrences_list"))


@app.route("/recurrence/<int:rec_id>/stop", methods=("POST",))
def recurrence_stop(rec_id):
    """Полностью остановить повторение. Созданные экземпляры сохраняются."""
    rec = _load_recurrence_or_404(rec_id)
    con = database.get_db()
    con.execute("UPDATE recurrences SET stopped = 1, paused = 0 WHERE id = ?", (rec_id,))
    con.commit()
    flash("Повторение «" + rec["title"] + "» остановлено. Созданные задачи сохранены.",
          "success")
    return redirect(url_for("recurrences_list"))


# --- Планирование: день и неделя ---------------------------------------------

@app.route("/plan")
def plan():
    """Планирование задач: режим «День» и «Неделя» + блок задач без срока.

    Никаких копий задач не создаётся — всё строится из таблицы tasks
    по полю due_date. Просроченность считает _with_overdue_flag (единое
    серверное правило: незавершённая задача со сроком строго раньше сегодня).
    """
    process_recurrences()
    con = database.get_db()
    today_iso = _today_iso()
    today = date.today()

    mode = request.args.get("mode", "week")
    if mode not in ("day", "week"):
        mode = "week"
    base = _parse_date_arg(request.args.get("date", "")) or today

    filters = {
        "assignee": request.args.get("assignee", "").strip(),
        "project": request.args.get("project", "").strip(),
        "status": request.args.get("status", "").strip(),
    }

    # --- Общие условия WHERE (фильтры) ---
    conditions, params = [], []
    if filters["status"] in STATUSES:
        conditions.append("status = ?")
        params.append(filters["status"])
    if filters["assignee"]:
        conditions.append("assignee = ?")
        params.append(filters["assignee"])
    if filters["project"]:
        conditions.append("project = ?")
        params.append(filters["project"])

    # --- Период ---
    if mode == "day":
        period_start = period_end = base
    else:
        period_start = base - timedelta(days=base.weekday())   # понедельник
        period_end = period_start + timedelta(days=6)          # воскресенье

    # --- Задачи периода (с указанным сроком) ---
    period_sql = "SELECT * FROM tasks"
    period_conds = list(conditions) + [
        "due_date IS NOT NULL", "due_date != ''",
        "due_date >= ?", "due_date <= ?",
    ]
    period_sql += " WHERE " + " AND ".join(period_conds) + " ORDER BY due_date, id"
    period_rows = con.execute(
        period_sql, params + [period_start.isoformat(), period_end.isoformat()]
    ).fetchall()
    period_tasks = _with_overdue_flag(period_rows)

    # --- Задачи без срока ---
    no_due_sql = "SELECT * FROM tasks"
    no_due_conds = list(conditions) + ["(due_date IS NULL OR due_date = '')"]
    no_due_sql += " WHERE " + " AND ".join(no_due_conds) + " ORDER BY id DESC"
    no_due_tasks = _with_overdue_flag(
        con.execute(no_due_sql, params).fetchall()
    )

    # --- Статистика периода (без срока — отдельно) ---
    stats = {
        "total": len(period_tasks),
        "new": sum(1 for t in period_tasks if t["status"] == "Новая"),
        "progress": sum(1 for t in period_tasks if t["status"] == "В работе"),
        "done": sum(1 for t in period_tasks if t["status"] == DONE_STATUS),
        "overdue": sum(1 for t in period_tasks if t["is_overdue"]),
        "no_due": len(no_due_tasks),
    }

    # --- URL-ы навигации (сохраняют режим, дату и фильтры) ---
    def plan_url(**overrides):
        args = {
            "mode": mode,
            "date": base.isoformat(),
            "assignee": filters["assignee"],
            "project": filters["project"],
            "status": filters["status"],
        }
        args.update(overrides)
        args = {k: v for k, v in args.items() if v not in ("", None)}
        return url_for("plan", **args)

    if mode == "day":
        step = timedelta(days=1)
        labels = {"prev": "← Предыдущий день", "next": "Следующий день →",
                  "today": "Сегодня"}
        period_title = "%s, %s" % (WEEKDAY_NAMES[base.weekday()],
                                   base.strftime("%d.%m.%Y"))
        day_heading = period_title
    else:
        step = timedelta(days=7)
        labels = {"prev": "← Предыдущая неделя", "next": "Следующая неделя →",
                  "today": "Текущая неделя"}
        if period_start.year == period_end.year:
            period_title = "%s – %s" % (period_start.strftime("%d.%m"),
                                        period_end.strftime("%d.%m.%Y"))
        else:
            period_title = "%s – %s" % (period_start.strftime("%d.%m.%Y"),
                                        period_end.strftime("%d.%m.%Y"))
        day_heading = ""

    urls = {
        "prev": plan_url(date=(base - step).isoformat()),
        "next": plan_url(date=(base + step).isoformat()),
        "today": plan_url(date=today.isoformat()),
        "switch_day": plan_url(mode="day"),
        "switch_week": plan_url(mode="week"),
        "reset": plan_url(assignee="", project="", status=""),
    }

    if mode == "day":
        day_tasks = period_tasks
        days = []
        day_hint = {0: "сегодня", 1: "завтра", -1: "вчера"}.get((base - today).days, "")
        period_hint = day_hint
    else:
        day_tasks = []
        period_hint = "текущая неделя" if period_start <= today <= period_end else ""
        days = []
        for i in range(7):
            d = period_start + timedelta(days=i)
            iso = d.isoformat()
            days.append({
                "name": WEEKDAY_NAMES[i],
                "iso": iso,
                "display": d.strftime("%d.%m"),
                "is_today": iso == today_iso,
                "is_weekend": i >= 5,
                "tasks": [t for t in period_tasks if t["due_date"] == iso],
            })

    return render_template(
        "plan.html",
        mode=mode,
        base_iso=base.isoformat(),
        filters=filters,
        filters_active=bool(filters["assignee"] or filters["project"] or filters["status"]),
        assignees=_distinct_values("assignee"),
        projects=_distinct_values("project"),
        stats=stats,
        days=days,
        day_tasks=day_tasks,
        day_heading=day_heading,
        no_due_tasks=no_due_tasks,
        period_title=period_title,
        period_hint=period_hint,
        labels=labels,
        urls=urls,
        recurrences=_recurrences_map(),
    )


@app.route("/task/new", methods=("GET", "POST"))
def task_new():
    if request.method == "POST" and _is_repeat_submission(request.form.get("form_token", "")):
        # Двойной клик или повторная отправка той же формы — записи не дублируем
        flash("Задача уже была добавлена: повторная отправка формы отклонена.", "success")
        return redirect(url_for("index"))

    form = _form_from_request()
    errors = []
    rc = (_recurrence_from_request() if request.method == "POST"
          else {"frequency": "none", "weekday": "", "month_day": "", "end": "", "errors": []})
    if request.method == "POST":
        errors = _validate(form) + _recurrence_form_errors(rc, form["due_date"])
        if not errors:
            con = database.get_db()
            # Повторяющаяся задача: создаём расписание, задача — первый экземпляр
            recurrence_id = None
            if rc["frequency"] != "none":
                start = date.fromisoformat(form["due_date"])
                recurrence_id = _insert_recurrence(con, form, rc, start)
                recurrence_date = start.isoformat()
            else:
                recurrence_date = None
            con.execute(
                "INSERT INTO tasks (title, description, assignee, project, "
                "priority, status, due_date, recurrence_id, recurrence_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (form["title"], form["description"], form["assignee"], form["project"],
                 form["priority"], form["status"], form["due_date"] or None,
                 recurrence_id, recurrence_date),
            )
            con.commit()
            _mark_token_used(request.form.get("form_token", ""))
            flash("Задача добавлена." + (" Повторение: " + _recurrence_rule_text(
                rc["frequency"], rc["weekday"], rc["month_day"]) + "."
                if recurrence_id else ""), "success")
            return redirect(url_for("index"))

    # GET — новый токен; POST с ошибками валидации — прежний (не израсходован)
    token = (request.form.get("form_token", "") if request.method == "POST"
             else _new_form_token())
    return render_template(
        "task_form.html",
        heading="Новая задача",
        form=form,
        errors=errors,
        form_token=token,
        recurrence=rc,
        recurrence_info=None,
        assignees=_distinct_values("assignee"),
        projects=_distinct_values("project"),
    )


@app.route("/task/<int:task_id>/edit", methods=("GET", "POST"))
def task_edit(task_id):
    task = _load_task_or_404(task_id)

    if request.method == "POST" and _is_repeat_submission(request.form.get("form_token", "")):
        # Повторная отправка формы редактирования — обновление уже применено
        flash("Задача уже была обновлена: повторная отправка формы отклонена.", "success")
        return redirect(url_for("index"))

    con = database.get_db()
    current_rec = None
    if task["recurrence_id"]:
        current_rec = con.execute(
            "SELECT * FROM recurrences WHERE id = ?", (task["recurrence_id"],)
        ).fetchone()

    default_rc = {
        "frequency": current_rec["frequency"] if current_rec else "none",
        "weekday": current_rec["weekday"] if current_rec else "",
        "month_day": current_rec["month_day"] if current_rec else "",
        "end": current_rec["end_date"] or "" if current_rec else "",
        "errors": [],
    }
    rc = _recurrence_from_request() if request.method == "POST" else default_rc

    form = _form_from_request(task)
    errors = []
    if request.method == "POST":
        errors = _validate(form) + _recurrence_form_errors(rc, form["due_date"])
        if not errors:
            # 1) Обновляем сам экземпляр задачи (только его — экземпляры независимы)
            con.execute(
                "UPDATE tasks SET title = ?, description = ?, assignee = ?, project = ?, "
                "priority = ?, status = ?, due_date = ?, "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (form["title"], form["description"], form["assignee"], form["project"],
                 form["priority"], form["status"], form["due_date"] or None, task_id),
            )

            extra_flash = ""
            # 2) Управляем расписанием по настройке «Повторение» в форме
            if current_rec is None and rc["frequency"] != "none":
                # Обычная задача становится повторяющейся: срок — первое вхождение
                start = date.fromisoformat(form["due_date"])
                rec_id = _insert_recurrence(con, form, rc, start)
                con.execute(
                    "UPDATE tasks SET recurrence_id = ?, recurrence_date = ? WHERE id = ?",
                    (rec_id, start.isoformat(), task_id),
                )
                extra_flash = " Повторение включено."
            elif current_rec is not None:
                if rc["frequency"] == "none":
                    # «Не повторять» = полностью остановить повторение.
                    # Созданные экземпляры и история сохраняются.
                    con.execute("UPDATE recurrences SET stopped = 1, paused = 0 WHERE id = ?",
                                (current_rec["id"],))
                    extra_flash = " Повторение остановлено: новые экземпляры создаваться не будут."
                else:
                    current_end = current_rec["end_date"] or ""
                    rule_changed = (
                        current_rec["frequency"] != rc["frequency"]
                        or (rc["frequency"] == "weekly"
                            and current_rec["weekday"] != rc["weekday"])
                        or (rc["frequency"] == "monthly"
                            and current_rec["month_day"] != rc["month_day"])
                        or current_end != rc["end"]
                    )
                    if rule_changed:
                        # Изменение действует только на будущие экземпляры:
                        # следующий — ближайшая дата по новому правилу после
                        # сегодняшнего дня (или последней созданной даты).
                        base = date.today()
                        if current_rec["last_generated"]:
                            try:
                                base = max(base, date.fromisoformat(current_rec["last_generated"]))
                            except ValueError:
                                pass
                        new_next = _next_occurrence(
                            base, rc["frequency"], rc["weekday"], rc["month_day"])
                        con.execute(
                            "UPDATE recurrences SET frequency = ?, weekday = ?, "
                            "month_day = ?, end_date = ?, next_date = ? WHERE id = ?",
                            (rc["frequency"],
                             rc["weekday"] if rc["frequency"] == "weekly" else None,
                             rc["month_day"] if rc["frequency"] == "monthly" else None,
                             rc["end_date"].isoformat() if rc.get("end_date") else None,
                             new_next.isoformat(), current_rec["id"]),
                        )
                        extra_flash = " Расписание изменено: действует на будущие экземпляры."

            con.commit()
            _mark_token_used(request.form.get("form_token", ""))
            flash("Задача обновлена." + extra_flash, "success")
            return redirect(url_for("index"))

    token = (request.form.get("form_token", "") if request.method == "POST"
             else _new_form_token())
    return render_template(
        "task_form.html",
        heading="Редактирование задачи",
        form=form,
        errors=errors,
        form_token=token,
        recurrence=rc,
        recurrence_info=current_rec,
        rule_hint_text=(_recurrence_rule_text(current_rec["frequency"],
                                              current_rec["weekday"],
                                              current_rec["month_day"])
                        if current_rec else None),
        assignees=_distinct_values("assignee"),
        projects=_distinct_values("project"),
    )


@app.route("/task/<int:task_id>/delete", methods=("POST",))
def task_delete(task_id):
    task = _load_task_or_404(task_id)
    con = database.get_db()
    con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    con.commit()
    flash("Задача «" + task["title"] + "» удалена.", "success")
    return redirect(url_for("index"))


# --- Страницы ошибок -----------------------------------------------------------

@app.errorhandler(404)
def page_not_found(_error):
    return render_template("error.html", code=404, message="Страница не найдена."), 404


@app.errorhandler(500)
def server_error(_error):
    # Подробности — в лог сервера, пользователю — без деталей и секретов
    app.logger.exception("Internal error: %s %s", request.method, request.path)
    return render_template("error.html", code=500, message="Внутренняя ошибка сервера."), 500


# --- Кэширование HTML ------------------------------------------------------------
# Запрещаем браузеру показывать устаревшие страницы: список, канбан и дашборд
# должны всегда отражать актуальное содержимое SQLite (например, после
# изменения срока задачи через форму редактирования).

@app.after_request
def disable_html_caching(response):
    if response.content_type and response.content_type.startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# Стартовый прогон расписаний: выполняется один раз при загрузке модуля
# (все функции к этому моменту определены). Повторные прогоны — при открытии
# основных страниц; дубли исключены уникальным индексом в БД.
with app.app_context():
    process_recurrences()


def _dev_server_config():
    """(host, debug) для запуска `python app.py` (production использует gunicorn).

    Локальный режим слушает ТОЛЬКО 127.0.0.1; production-хост — из
    переменной окружения, debug принудительно выключен.
    """
    if _is_production():
        return os.environ.get("TASK_TRACKER_HOST", "0.0.0.0"), False
    return "127.0.0.1", os.environ.get("TASK_TRACKER_DEBUG", "1") == "1"


if __name__ == "__main__":
    # Локальный запуск отладочным сервером. Production запускается
    # WSGI-сервером (gunicorn, см. Procfile) — этот блок там не выполняется.
    host, debug = _dev_server_config()
    app.run(
        host=host,
        port=int(os.environ.get("TASK_TRACKER_PORT", "5000")),
        debug=debug,
    )
