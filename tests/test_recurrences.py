# -*- coding: utf-8 -*-
"""Повторяющиеся задачи: правила расписаний, генерация, пауза/стоп, дубли."""
import calendar
from datetime import date, timedelta

import app as app_module
from tests.base import TrackerTestCase

TODAY = date.today()


def last_day_of_previous_month(today):
    first = date(today.year, today.month, 1)
    return first - timedelta(days=1)


class RuleTests(TrackerTestCase):
    """Правила расчёта следующей даты (юнит-уровень)."""

    def test_daily_next_day(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 1, 10), "daily", None, None),
            date(2026, 1, 11))

    def test_weekly_same_day_next_week(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 1, 5), "weekly", 0, None),
            date(2026, 1, 12))   # понедельник → следующий понедельник

    def test_weekly_later_this_week(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 1, 5), "weekly", 2, None),
            date(2026, 1, 7))    # понедельник → среда той же недели

    def test_monthly_short_month_clamped(self):
        # 31 января → 28 февраля (2026 не високосный)
        self.assertEqual(
            app_module._next_occurrence(date(2026, 1, 31), "monthly", None, 31),
            date(2026, 2, 28))

    def test_monthly_leap_year(self):
        # 31 января → 29 февраля 2028 (високосный)
        self.assertEqual(
            app_module._next_occurrence(date(2028, 1, 31), "monthly", None, 31),
            date(2028, 2, 29))

    def test_monthly_same_month_ahead(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 3, 5), "monthly", None, 20),
            date(2026, 3, 20))

    def test_monthly_next_month(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 3, 20), "monthly", None, 5),
            date(2026, 4, 5))

    def test_monthly_feb_30_clamped(self):
        self.assertEqual(
            app_module._next_occurrence(date(2026, 1, 30), "monthly", None, 30),
            date(2026, 2, 28))


class GenerationTests(TrackerTestCase):
    """Автосоздание экземпляров: только наступившие даты, без дублей."""

    def _process(self):
        with app_module.app.app_context():
            app_module.process_recurrences()

    def test_generates_due_instances_without_duplicates(self):
        start = TODAY - timedelta(days=5)
        rec_id = self.insert_recurrence(
            title="Ежедневка", frequency="daily",
            start_date=start.isoformat(),
            next_date=(TODAY - timedelta(days=4)).isoformat())
        self._process()
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)), 5)
        self._process()   # повторный прогон не создаёт дублей
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)), 5)
        rec = self.get_recurrence(rec_id)
        self.assertEqual(rec["last_generated"], TODAY.isoformat())
        self.assertEqual(rec["next_date"],
                         (TODAY + timedelta(days=1)).isoformat())

    def test_no_future_instances(self):
        future = TODAY + timedelta(days=3)
        rec_id = self.insert_recurrence(
            title="Будущее", frequency="daily",
            start_date=future.isoformat(), next_date=future.isoformat())
        self._process()
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)), 0)

    def test_paused_not_generated(self):
        past = TODAY - timedelta(days=2)
        rec_id = self.insert_recurrence(
            title="На паузе", frequency="daily", paused=1,
            start_date=past.isoformat(), next_date=past.isoformat())
        self._process()
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)), 0)

    def test_stopped_not_generated(self):
        past = TODAY - timedelta(days=2)
        rec_id = self.insert_recurrence(
            title="Остановлено", frequency="daily", stopped=1,
            start_date=past.isoformat(), next_date=past.isoformat())
        self._process()
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)), 0)

    def test_monthly_generation_clamps_short_month(self):
        prev_last = last_day_of_previous_month(TODAY)
        prev_first = date(prev_last.year, prev_last.month, 1)
        rec_id = self.insert_recurrence(
            title="Ежемесячное 31-го", frequency="monthly", month_day=31,
            start_date=prev_first.isoformat(),
            next_date=prev_first.isoformat())
        self._process()
        dates = self.task_dates(rec_id)
        expected = date(prev_last.year, prev_last.month,
                        min(31, calendar.monthrange(prev_last.year,
                                                    prev_last.month)[1]))
        self.assertIn(expected.isoformat(), dates)

    def test_catch_up_limit(self):
        long_ago = TODAY - timedelta(days=100)
        rec_id = self.insert_recurrence(
            title="После простоя", frequency="daily",
            start_date=long_ago.isoformat(), next_date=long_ago.isoformat())
        self._process()
        self.assertEqual(self.count_tasks("recurrence_id = ?", (rec_id,)),
                         app_module.RECURRENCE_CATCH_UP_LIMIT)


class RecurrenceEndpointsTests(TrackerTestCase):

    def test_create_task_with_monthly_recurrence(self):
        r = self.post_new_task(title="Отчёт", due_date=TODAY.isoformat(),
                               recurrence_frequency="monthly",
                               recurrence_month_day="31")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.count_tasks(), 1)
        rec = self.get_recurrence(1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["frequency"], "monthly")
        self.assertEqual(rec["month_day"], 31)
        self.assertGreater(rec["next_date"], TODAY.isoformat())
        task = self.get_task(1)
        self.assertEqual(task["recurrence_id"], 1)
        self.assertEqual(task["recurrence_date"], TODAY.isoformat())

    def test_monthly_requires_valid_day(self):
        self.post_new_task(title="Плохое число", due_date=TODAY.isoformat(),
                           recurrence_frequency="monthly",
                           recurrence_month_day="99")
        self.assertEqual(self.count_tasks(), 0)

    def test_weekly_requires_weekday(self):
        self.post_new_task(title="Без дня", due_date=TODAY.isoformat(),
                           recurrence_frequency="weekly")
        self.assertEqual(self.count_tasks(), 0)

    def test_recurrence_requires_due_date(self):
        self.post_new_task(title="Без срока", recurrence_frequency="daily")
        self.assertEqual(self.count_tasks(), 0)

    def test_pause_resume_stop_endpoints(self):
        rec_id = self.insert_recurrence(
            title="Управляемое", frequency="daily",
            start_date=TODAY.isoformat(), next_date=TODAY.isoformat())
        r = self.client.post("/recurrence/%d/pause" % rec_id,
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_recurrence(rec_id)["paused"], 1)

        r = self.client.post("/recurrence/%d/resume" % rec_id,
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        rec = self.get_recurrence(rec_id)
        self.assertEqual(rec["paused"], 0)
        self.assertGreater(rec["next_date"], TODAY.isoformat())

        r = self.client.post("/recurrence/%d/stop" % rec_id,
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_recurrence(rec_id)["stopped"], 1)

    def test_edit_turn_off_recurrence_stops_it(self):
        rec_id = self.insert_recurrence(
            title="Выключаемое", frequency="daily",
            start_date=TODAY.isoformat(), next_date=TODAY.isoformat())
        task_id = self.insert_task(title="Выключаемая задача")
        from tests.base import db_connect
        with db_connect(self.db_path) as con:
            con.execute("UPDATE tasks SET recurrence_id = ? WHERE id = ?",
                        (rec_id, task_id))
        self.post_edit_task(task_id, title="Выключаемая задача",
                            recurrence_frequency="none")
        self.assertEqual(self.get_recurrence(rec_id)["stopped"], 1)

    def test_recurrences_page(self):
        self.insert_recurrence(title="Существующее", frequency="daily",
                               start_date=TODAY.isoformat(),
                               next_date=TODAY.isoformat())
        r = self.client.get("/recurrences")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Существующее", r.get_data(as_text=True))
