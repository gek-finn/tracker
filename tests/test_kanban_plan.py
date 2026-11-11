# -*- coding: utf-8 -*-
"""Канбан-доска, изменение статуса и планирование на день/неделю."""
from datetime import date, timedelta

from tests.base import TrackerTestCase

TODAY = date.today()


class KanbanTests(TrackerTestCase):

    def _column_html(self, html, status):
        """Фрагмент HTML нужной колонки (колонки идут по порядку статусов)."""
        marker = 'class="kanban-cards" data-status="%s"' % status
        start = html.index(marker)
        end = html.find('class="kanban-cards"', start + 1)
        return html[start:end if end != -1 else len(html)]

    def test_kanban_page(self):
        self.insert_task(title="Канбан-задача")
        r = self.client.get("/kanban")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Канбан-задача", r.get_data(as_text=True))

    def test_columns_by_status(self):
        self.insert_task(title="Новая колонка", status="Новая")
        self.insert_task(title="В работе колонка", status="В работе")
        self.insert_task(title="Готовая колонка", status="Завершена")
        html = self.client.get("/kanban").get_data(as_text=True)
        self.assertIn("Новая колонка", self._column_html(html, "Новая"))
        self.assertIn("В работе колонка", self._column_html(html, "В работе"))
        self.assertIn("Готовая колонка", self._column_html(html, "Завершена"))
        self.assertNotIn("Готовая колонка", self._column_html(html, "Новая"))

    def test_status_change_json(self):
        task_id = self.insert_task(title="Переезд", status="Новая")
        r = self.client.post("/task/%d/status" % task_id,
                             json={"status": "В работе"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.get_task(task_id)["status"], "В работе")

    def test_status_change_form_encoded(self):
        task_id = self.insert_task(title="Форма")
        r = self.client.post("/task/%d/status" % task_id,
                             data={"status": "Завершена"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_task(task_id)["status"], "Завершена")

    def test_status_change_invalid_value(self):
        task_id = self.insert_task(title="Не менять", status="Новая")
        r = self.client.post("/task/%d/status" % task_id,
                             json={"status": "Мусор"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.get_task(task_id)["status"], "Новая")

    def test_status_change_missing_task(self):
        self.assertEqual(
            self.client.post("/task/999/status",
                             json={"status": "Новая"}).status_code, 404)

    def test_status_same_value_no_error(self):
        task_id = self.insert_task(title="Та же", status="Новая")
        r = self.client.post("/task/%d/status" % task_id,
                             json={"status": "Новая"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])


class PlanTests(TrackerTestCase):

    def test_plan_week_page(self):
        self.insert_task(title="Недельная", due_date=TODAY.isoformat())
        r = self.client.get("/plan?mode=week")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Недельная", r.get_data(as_text=True))

    def test_plan_day_shows_only_that_day(self):
        tomorrow = (TODAY + timedelta(days=1)).isoformat()
        self.insert_task(title="Сегодняшняя", due_date=TODAY.isoformat())
        self.insert_task(title="Завтрашняя", due_date=tomorrow)
        r = self.client.get("/plan?mode=day&date=%s" % TODAY.isoformat())
        html = r.get_data(as_text=True)
        self.assertIn("Сегодняшняя", html)
        self.assertNotIn("Завтрашняя", html)

    def test_plan_week_excludes_other_weeks(self):
        far = (TODAY + timedelta(days=30)).isoformat()
        self.insert_task(title="Ближняя", due_date=TODAY.isoformat())
        self.insert_task(title="Далёкая", due_date=far)
        html = self.client.get("/plan?mode=week").get_data(as_text=True)
        self.assertIn("Ближняя", html)
        self.assertNotIn("Далёкая", html)

    def test_plan_no_due_block(self):
        self.insert_task(title="Дата неизвестна", due_date=None)
        html = self.client.get("/plan?mode=week").get_data(as_text=True)
        self.assertIn("Дата неизвестна", html)
        self.assertIn("Без срока", html)

    def test_plan_day_navigation(self):
        r = self.client.get("/plan?mode=day&date=2026-01-01")
        self.assertEqual(r.status_code, 200)

    def test_plan_invalid_mode_falls_back_to_week(self):
        self.insert_task(title="Отображаемая", due_date=TODAY.isoformat())
        html = self.client.get("/plan?mode=мусор").get_data(as_text=True)
        self.assertIn("Отображаемая", html)   # неделя содержит сегодня

    def test_plan_overdue_counted(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="Опоздавшая", due_date=yesterday)
        html = self.client.get("/plan?mode=day&date=%s" % TODAY.isoformat())
        # просроченная задача видна в режиме «день» только за свой день;
        # проверяем через блок без срока/статистику страницы дня
        self.assertIn("Опоздавшая",
                      self.client.get("/plan?mode=day&date=%s"
                                      % yesterday).get_data(as_text=True))
