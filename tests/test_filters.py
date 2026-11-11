# -*- coding: utf-8 -*-
"""Поиск, фильтры и контроль просроченных задач."""
from datetime import date, timedelta

from tests.base import TrackerTestCase

TODAY = date.today()


class SearchTests(TrackerTestCase):

    def test_search_case_insensitive_cyrillic(self):
        self.insert_task(title="Подготовить Отчёт")
        self.insert_task(title="Другое дело", description="тут отчёт упоминается")
        self.insert_task(title="Не подходит")
        html = self.client.get("/?q=ОТЧЁТ").get_data(as_text=True)
        self.assertIn("Подготовить Отчёт", html)
        self.assertIn("Другое дело", html)
        self.assertNotIn("Не подходит", html)

    def test_search_no_results(self):
        self.insert_task(title="Задача")
        html = self.client.get("/?q=нет такого").get_data(as_text=True)
        self.assertIn("Ничего не найдено", html)


class FilterTests(TrackerTestCase):

    def test_filter_by_status(self):
        self.insert_task(title="Альфа-задача", status="Новая")
        self.insert_task(title="Бета-задача", status="Завершена")
        html = self.client.get("/?status=Завершена").get_data(as_text=True)
        self.assertNotIn("Альфа-задача", html)
        self.assertIn("Бета-задача", html)

    def test_filter_by_priority(self):
        self.insert_task(title="Срочная", priority="Высокий")
        self.insert_task(title="Обычная", priority="Средний")
        html = self.client.get("/?priority=Высокий").get_data(as_text=True)
        self.assertIn("Срочная", html)
        self.assertNotIn("Обычная", html)

    def test_filter_by_assignee(self):
        self.insert_task(title="Задача Анны", assignee="Анна")
        self.insert_task(title="Задача Бориса", assignee="Борис")
        html = self.client.get("/?assignee=Анна").get_data(as_text=True)
        self.assertIn("Задача Анны", html)
        self.assertNotIn("Задача Бориса", html)

    def test_filter_by_project(self):
        self.insert_task(title="Сайтовая", project="Сайт")
        self.insert_task(title="Прочая", project="")
        html = self.client.get("/?project=Сайт").get_data(as_text=True)
        self.assertIn("Сайтовая", html)
        self.assertNotIn("Прочая", html)

    def test_filter_without_assignee(self):
        self.insert_task(title="Осиротевшая", assignee="")
        self.insert_task(title="Чья-то", assignee="Анна")
        html = self.client.get("/?assignee=__none__").get_data(as_text=True)
        self.assertIn("Осиротевшая", html)
        self.assertNotIn("Чья-то", html)

    def test_filter_without_project(self):
        self.insert_task(title="Беспризорная", project="")
        self.insert_task(title="Проектная", project="Сайт")
        html = self.client.get("/?project=__none__").get_data(as_text=True)
        self.assertIn("Беспризорная", html)
        self.assertNotIn("Проектная", html)


class OverdueTests(TrackerTestCase):

    def test_overdue_rule(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="Просрочена", due_date=yesterday)
        task = self.get_task(1)
        # единое правило: срок < сегодня и статус не «Завершена»
        self.assertTrue(task["due_date"] < TODAY.isoformat())

    def test_overdue_filter_shows_only_overdue(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="Просрочена", due_date=yesterday)
        self.insert_task(title="Завершённая вчера", due_date=yesterday,
                         status="Завершена")
        self.insert_task(title="Будущая", due_date="2099-01-01")
        self.insert_task(title="Без срока")
        html = self.client.get("/?overdue=on").get_data(as_text=True)
        self.assertIn("Просрочена", html)
        self.assertNotIn("Завершённая вчера", html)
        self.assertNotIn("Будущая", html)
        self.assertNotIn("Без срока", html)

    def test_overdue_marked_in_list(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        self.insert_task(title="Просрочена", due_date=yesterday)
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("row-overdue", html)
        self.assertIn("Просрочена", html)
