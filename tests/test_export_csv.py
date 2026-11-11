# -*- coding: utf-8 -*-
"""Экспорт CSV (этап 6.1): кодировка, экранирование, фильтры, инъекции."""
import csv
import io
from datetime import date
from urllib.parse import quote

from tests.base import TrackerTestCase

TODAY = date.today()


class CsvExportTests(TrackerTestCase):

    def get_rows(self, url="/export/csv"):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        raw = r.get_data()
        return r, raw, list(csv.reader(
            io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))

    def test_response_headers(self):
        r, raw, rows = self.get_rows()
        self.assertIn("attachment",
                      r.headers.get("Content-Disposition", ""))
        self.assertIn("text/csv", r.headers.get("Content-Type", ""))

    def test_utf8_bom(self):
        _, raw, _ = self.get_rows()
        self.assertEqual(raw[:3], b"\xef\xbb\xbf")   # BOM для Excel

    def test_russian_headers(self):
        _, _, rows = self.get_rows()
        self.assertEqual(rows[0], ["ID", "Название", "Описание",
                                   "Исполнитель", "Проект", "Статус",
                                   "Приоритет", "Срок выполнения"])

    def test_all_tasks_exported(self):
        for i in range(3):
            self.insert_task(title="Задача %d" % i)
        _, _, rows = self.get_rows()
        self.assertEqual(len(rows), 4)   # шапка + 3

    def test_quotes_semicolon_newline_escaped(self):
        self.insert_task(title="Кавычки и разделители",
                         description='первая"строка";вторая\nтретья')
        _, _, rows = self.get_rows()
        row = [r for r in rows if r[1] == "Кавычки и разделители"][0]
        self.assertEqual(row[2], 'первая"строка";вторая\nтретья')

    def test_formula_injection_neutralized(self):
        self.insert_task(title='=ГИПЕРССЫЛКА("http://evil";"жми")',
                         description="+1", assignee="@Анна", project="-Проект")
        _, _, rows = self.get_rows()
        row = [r for r in rows if "ГИПЕРССЫЛКА" in r[1]][0]
        self.assertTrue(row[1].startswith("'="))
        self.assertTrue(row[2].startswith("'"))
        self.assertTrue(row[3].startswith("'"))
        self.assertTrue(row[4].startswith("'"))

    def test_plain_values_untouched(self):
        self.insert_task(title="Обычная задача", description="Обычное описание",
                         assignee="Анна", project="Сайт")
        _, _, rows = self.get_rows()
        row = [r for r in rows if r[1] == "Обычная задача"][0]
        self.assertEqual(row[3], "Анна")
        self.assertEqual(row[4], "Сайт")

    def test_due_date_format(self):
        self.insert_task(title="Со сроком", due_date=TODAY.isoformat())
        _, _, rows = self.get_rows()
        row = [r for r in rows if r[1] == "Со сроком"][0]
        self.assertEqual(row[7], TODAY.strftime("%d.%m.%Y"))

    def test_assignee_filter_applied(self):
        self.insert_task(title="Анны", assignee="Анна")
        self.insert_task(title="Бориса", assignee="Борис")
        _, _, rows = self.get_rows("/export/csv?assignee="
                                   + quote("Анна"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "Анны")

    def test_search_filter_applied(self):
        self.insert_task(title="Квартальный отчёт")
        self.insert_task(title="Другая")
        _, _, rows = self.get_rows("/export/csv?q=" + quote("квартальный"))
        self.assertEqual(len(rows), 2)

    def test_period_filter_applied(self):
        from datetime import timedelta
        self.insert_task(title="Сегодня", due_date=TODAY.isoformat())
        self.insert_task(title="Давно",
                         due_date=(TODAY - timedelta(days=40)).isoformat())
        _, _, rows = self.get_rows("/export/csv?period=7d")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "Сегодня")
