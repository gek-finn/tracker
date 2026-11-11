# -*- coding: utf-8 -*-
"""Отчёты по исполнителям/проектам и фильтрация по периодам (этап 6.1)."""
import re
from datetime import date, timedelta
from urllib.parse import quote

import app as app_module
from tests.base import TrackerTestCase

TODAY = date.today()


class GroupReportTests(TrackerTestCase):

    def _tasks(self):
        rows = app_module._filtered_task_rows(app_module._empty_filters())
        return app_module._with_overdue_flag(rows)

    def _with_app_context(self):
        return self.client.application.app_context()

    def test_assignee_report(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="1", assignee="Анна", status="Завершена",
                         due_date=yesterday)
        self.insert_task(title="2", assignee="Анна", due_date=yesterday)
        self.insert_task(title="3", assignee="Борис", due_date=yesterday)
        self.insert_task(title="4", assignee="", due_date=yesterday)
        with self._with_app_context():
            report = app_module._group_report(self._tasks(), "assignee")
        by_name = {r["name"]: r for r in report}
        anna = by_name["Анна"]
        self.assertEqual(anna["total"], 2)
        self.assertEqual(anna["done"], 1)
        self.assertEqual(anna["undone"], 1)
        self.assertEqual(anna["percent"], 50)
        empty = by_name[""]
        self.assertEqual(empty["total"], 1)

    def test_assignee_overdue_unified_rule(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="открытая", assignee="Борис", due_date=yesterday)
        self.insert_task(title="закрытая", assignee="Борис", status="Завершена",
                         due_date=yesterday)
        with self._with_app_context():
            report = app_module._group_report(self._tasks(), "assignee")
        boris = {r["name"]: r for r in report}["Борис"]
        self.assertEqual(boris["overdue"], 1)   # завершённая не просрочена

    def test_project_report(self):
        self.insert_task(title="1", project="Сайт", status="В работе")
        self.insert_task(title="2", project="Сайт", status="Завершена")
        self.insert_task(title="3", project="")
        with self._with_app_context():
            report = app_module._group_report(self._tasks(), "project")
        by_name = {r["name"]: r for r in report}
        site = by_name["Сайт"]
        self.assertEqual(site["total"], 2)
        self.assertEqual(site["done"], 1)
        self.assertEqual(site["in_progress"], 1)
        self.assertEqual(site["percent"], 50)
        self.assertEqual(by_name[""]["total"], 1)

    def test_report_sorting_empty_group_last(self):
        self.insert_task(title="1", assignee="Анна")
        self.insert_task(title="2", assignee="Анна")
        self.insert_task(title="3", assignee="")
        with self._with_app_context():
            report = app_module._group_report(self._tasks(), "assignee")
        self.assertEqual(report[0]["name"], "Анна")   # больше задач — выше
        self.assertEqual(report[-1]["name"], "")      # пустая группа — в конце


class PeriodTests(TrackerTestCase):

    def test_period_7d_bounds(self):
        with app_module.app.test_request_context("/dashboard?period=7d"):
            period, f, t, err = app_module._report_period_from_request()
        self.assertEqual(period, "7d")
        self.assertIsNone(err)
        self.assertEqual((t - f).days, 6)
        self.assertEqual(t, TODAY)

    def test_period_30d_bounds(self):
        with app_module.app.test_request_context("/dashboard?period=30d"):
            period, f, t, err = app_module._report_period_from_request()
        self.assertEqual(period, "30d")
        self.assertEqual((t - f).days, 29)

    def test_period_custom_valid(self):
        with app_module.app.test_request_context(
                "/dashboard?period=custom&date_from=2026-01-01"
                "&date_to=2026-01-31"):
            period, f, t, err = app_module._report_period_from_request()
        self.assertEqual(period, "custom")
        self.assertEqual(f, date(2026, 1, 1))
        self.assertEqual(t, date(2026, 1, 31))

    def test_period_custom_inverted(self):
        with app_module.app.test_request_context(
                "/dashboard?period=custom&date_from=2026-02-10"
                "&date_to=2026-01-01"):
            period, f, t, err = app_module._report_period_from_request()
        self.assertIsNotNone(err)
        self.assertEqual(period, "all")   # откат на «все время»

    def test_period_custom_incomplete(self):
        with app_module.app.test_request_context(
                "/dashboard?period=custom&date_from=мусор"):
            period, f, t, err = app_module._report_period_from_request()
        self.assertIsNotNone(err)
        self.assertEqual(period, "all")


class DashboardReportTests(TrackerTestCase):

    def test_dashboard_period_filtering(self):
        old = (TODAY - timedelta(days=40)).isoformat()
        self.insert_task(title="Недавняя", assignee="Анна",
                         due_date=TODAY.isoformat())
        self.insert_task(title="Древняя", assignee="Ветеран", due_date=old)
        html = self.client.get("/dashboard?period=7d").get_data(as_text=True)
        self.assertIn("Анна", html)
        self.assertNotIn("Ветеран", html)
        self.assertIn("По исполнителям", html)
        self.assertIn("По проектам", html)
        self.assertIn("сроку выполнения", html)   # подпись правила периода

    def test_dashboard_custom_period(self):
        self.insert_task(title="Сегодняшняя", assignee="ИмяСегодня",
                         due_date=TODAY.isoformat())
        self.insert_task(title="Вчерашняя", assignee="ИмяВчера",
                         due_date=(TODAY - timedelta(days=1)).isoformat())
        url = ("/dashboard?period=custom&date_from=%s&date_to=%s"
               % (TODAY.isoformat(), TODAY.isoformat()))
        html = self.client.get(url).get_data(as_text=True)
        self.assertIn("ИмяСегодня", html)
        self.assertNotIn("ИмяВчера", html)

    def test_dashboard_all_period_includes_no_due(self):
        self.insert_task(title="Бессрочная", assignee="Безымянный",
                         due_date=None)
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Безымянный", html)

    def test_report_link_to_filtered_list(self):
        self.insert_task(title="Задача Анны", assignee="Анна")
        self.insert_task(title="Чужая задача", assignee="Борис")
        html = self.client.get("/dashboard").get_data(as_text=True)
        m = re.search(
            r'href="(/[^"]*assignee=[^"]*%D0%90%D0%BD%D0%BD%D0%B0[^"]*)"', html)
        self.assertIsNotNone(m)
        r = self.client.get(m.group(1))
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Задача Анны", page)
        self.assertNotIn("Чужая задача", page)

    def test_report_link_no_assignee_group(self):
        self.insert_task(title="Осиротевшая", assignee="")
        self.insert_task(title="Именная", assignee="Анна")
        html = self.client.get("/dashboard").get_data(as_text=True)
        m = re.search(r'href="(/[^"]*assignee=__none__[^"]*)"', html)
        self.assertIsNotNone(m)
        page = self.client.get(m.group(1)).get_data(as_text=True)
        self.assertIn("Осиротевшая", page)
        self.assertNotIn("Именная", page)
