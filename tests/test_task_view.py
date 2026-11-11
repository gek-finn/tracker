# -*- coding: utf-8 -*-
"""Модальное окно «Просмотр задачи» (JSON endpoint)."""
from datetime import date, timedelta

from tests.base import TrackerTestCase

TODAY = date.today()


class TaskViewTests(TrackerTestCase):

    def test_view_json_fields(self):
        yesterday_date = TODAY - timedelta(days=1)
        yesterday = yesterday_date.isoformat()
        self.insert_task(title="Просматриваемая", description="Описание",
                         assignee="Анна", project="Сайт",
                         priority="Высокий", status="В работе",
                         due_date=yesterday)
        r = self.client.get("/task/1/view")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        task = data["task"]
        self.assertEqual(task["id"], 1)
        self.assertEqual(task["title"], "Просматриваемая")
        self.assertEqual(task["description"], "Описание")
        self.assertEqual(task["assignee"], "Анна")
        self.assertEqual(task["project"], "Сайт")
        self.assertEqual(task["priority"], "Высокий")
        self.assertEqual(task["priority_class"], "priority-high")
        self.assertEqual(task["status"], "В работе")
        self.assertEqual(task["status_class"], "status-progress")
        self.assertEqual(task["due_date"],
                         yesterday_date.strftime("%d.%m.%Y"))
        self.assertTrue(task["overdue"])          # срок вчера, не завершена
        self.assertIsNone(task["recurrence"])

    def test_view_recurrence_info(self):
        rec_id = self.insert_recurrence(
            title="Еженедельный отчёт", frequency="weekly", weekday=4,
            start_date=TODAY.isoformat(), next_date=TODAY.isoformat())
        # задача-экземпляр с ссылкой на расписание
        from tests.base import db_connect
        with db_connect(self.db_path) as con:
            con.execute(
                "INSERT INTO tasks (title, assignee, project, priority, "
                "status, due_date, recurrence_id, recurrence_date) "
                "VALUES ('Экземпляр', '', '', 'Средний', 'Новая', ?, ?, ?)",
                (TODAY.isoformat(), rec_id, TODAY.isoformat()))
        r = self.client.get("/task/1/view")
        rec = r.get_json()["task"]["recurrence"]
        self.assertIsNotNone(rec)
        self.assertIn("каждую неделю", rec["text"])
        self.assertIn("пятницу", rec["text"])
        self.assertFalse(rec["paused"])
        self.assertFalse(rec["stopped"])

    def test_view_missing_task_404(self):
        r = self.client.get("/task/999/view")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.get_json()["ok"])
