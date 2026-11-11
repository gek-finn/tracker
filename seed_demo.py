"""Явное заполнение отдельной пустой SQLite: python seed_demo.py --db PATH.

Не импортирует app (его импорт инициализирует БД). Непустые базы никогда
не изменяются, в том числе при повторном запуске. Режима очистки нет.
"""
import argparse
from datetime import date, timedelta
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
PROJECT = "Запуск корпоративного сайта"
ASSIGNEES = ("Анна Леснова", "Михаил Ветров", "Елена Зорина", "Павел Рощин")

# Название, критерий готовности, исполнитель, приоритет, статус, дней от сегодня.
TASKS = (
    ("Согласовать цели сайта", "Утвердить целевую аудиторию и показатели заявок.", 0, "Высокий", "Завершена", -12),
    ("Собрать требования отделов", "Получить требования продаж, HR и поддержки.", 0, "Высокий", "Завершена", -10),
    ("Исследовать сайты конкурентов", "Сравнить пять сайтов и описать удачные решения.", 2, "Средний", "Завершена", -9),
    ("Утвердить карту сайта", "Согласовать разделы, вложенность и навигацию.", 0, "Высокий", "Завершена", -8),
    ("Подготовить прототип главной", "Показать первый экран, услуги и форму заявки.", 1, "Высокий", "Завершена", -6),
    ("Выбрать визуальную концепцию", "Утвердить шрифты, цвета и сетку макетов.", 1, "Средний", "Завершена", -4),
    ("Получить фотографии команды", "Собрать согласованные портреты в хорошем качестве.", 2, "Низкий", "Новая", -3),
    ("Согласовать тексты услуг", "Получить замечания руководителей направлений.", 2, "Высокий", "В работе", -2),
    ("Разработать мобильные макеты", "Подготовить главную и карточку услуги для телефона.", 1, "Высокий", "В работе", -1),
    ("Сверстать главную страницу", "Проверить адаптивность на трёх размерах экрана.", 3, "Высокий", "В работе", 0),
    ("Подготовить раздел о компании", "Добавить историю, ценности и факты о компании.", 2, "Средний", "В работе", 0),
    ("Настроить тестовый сервер", "Выдать команде доступ к закрытому стенду.", 3, "Высокий", "Завершена", -1),
    ("Сверстать каталог услуг", "Реализовать список услуг и детальные страницы.", 3, "Высокий", "В работе", 2),
    ("Подготовить кейсы клиентов", "Описать три проекта с результатами и иллюстрациями.", 2, "Средний", "Новая", 3),
    ("Разработать форму заявки", "Добавить валидацию, уведомления и защиту от спама.", 3, "Высокий", "Новая", 4),
    ("Нарисовать иконки преимуществ", "Экспортировать согласованный набор SVG.", 1, "Низкий", "Новая", 5),
    ("Подключить веб-аналитику", "Проверить события отправки формы и кликов по телефону.", 3, "Средний", "Новая", 6),
    ("Подготовить SEO-метаданные", "Заполнить уникальные title и description страниц.", 2, "Средний", "Новая", 7),
    ("Проверить доступность интерфейса", "Проверить контраст, фокус и навигацию клавиатурой.", 1, "Средний", "Новая", 8),
    ("Провести приёмочное тестирование", "Проверить пользовательские сценарии по чек-листу.", 0, "Высокий", "Новая", 10),
    ("Настроить домен и HTTPS", "Проверить сертификат и перенаправления адресов.", 3, "Высокий", "Новая", 12),
    ("Обучить редакторов сайта", "Провести встречу и передать инструкцию публикации.", 0, "Средний", "Новая", 13),
    ("Согласовать запуск сайта", "Получить подтверждение владельцев всех разделов.", 0, "Высокий", "Новая", 14),
    ("Собрать идеи второй очереди", "Составить список улучшений для следующего релиза.", 0, "Низкий", "Новая", None),
)


def seed_demo(db_path, *, today=None):
    """Заполнить пустую БД атомарно; ValueError при небезопасной цели.

    today используется тестами; CLI всегда отсчитывает даты от текущего дня.
    Допускаются новый файл, файл нулевого размера и пустая схема приложения.
    """
    target = Path(db_path).expanduser().resolve()
    protected = ROOT / "tasks.db"
    if target == protected or (target.exists() and protected.exists()
                               and target.samefile(protected)):
        raise ValueError("Рабочая tasks.db защищена. Укажите отдельную демонстрационную базу.")
    today = today or date.today()
    con = sqlite3.connect(target, timeout=10)
    try:
        con.execute("BEGIN IMMEDIATE")
        tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            if con.execute("SELECT 1 FROM " + quoted + " LIMIT 1").fetchone():
                raise ValueError("База непустая: заполнение отменено, данные не изменены.")
        # execute, а не executescript: DDL и данные остаются в одной транзакции.
        statement = ""
        for line in (ROOT / "schema.sql").read_text(encoding="utf-8").splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                con.execute(statement)
                statement = ""
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_recurrence "
                    "ON tasks(recurrence_id, recurrence_date)")
        for title, description, person, priority, status, offset in TASKS:
            due = (today + timedelta(days=offset)).isoformat() if offset is not None else None
            con.execute("INSERT INTO tasks "
                        "(title, description, assignee, project, priority, status, due_date) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (title, description, ASSIGNEES[person], PROJECT, priority, status, due))
        # Первые экземпляры создаст штатный механизм приложения в будущие даты.
        for frequency, offset, title, person in (
            ("daily", 1, "Проверить доступность сайта и формы заявки", 3),
            ("weekly", 7, "Обсудить прогресс запуска с командой", 0),
            ("monthly", 28, "Подготовить отчёт по посещаемости и заявкам", 2),
        ):
            start = today + timedelta(days=offset)
            con.execute("INSERT INTO recurrences "
                        "(title, description, assignee, project, priority, frequency, "
                        "weekday, month_day, start_date, next_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (title, "Зафиксировать результаты и необходимые действия.",
                         ASSIGNEES[person], PROJECT, "Средний", frequency,
                         start.weekday() if frequency == "weekly" else None,
                         start.day if frequency == "monthly" else None,
                         start.isoformat(), start.isoformat()))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Явный путь к отдельной пустой SQLite")
    args = parser.parse_args(argv)
    try:
        target = seed_demo(args.db)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, "Заполнение отменено: %s\n" % exc)
    print("Создано 24 задачи и 3 расписания: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
