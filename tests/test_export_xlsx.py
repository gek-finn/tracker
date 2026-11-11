# -*- coding: utf-8 -*-
"""Экспорт Excel (этап 6.2): оформление, печать, защита от формул."""
import io
from datetime import date, timedelta

from openpyxl import load_workbook

from tests.base import TrackerTestCase

TODAY = date.today()

XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")


class XlsxExportTests(TrackerTestCase):

    def get_sheet(self, url="/export/xlsx"):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.get_data()))
        return r, wb.active

    def fill(self):
        self.insert_task(title="Обычная задача", description="Описание",
                         assignee="Анна", project="Сайт",
                         priority="Высокий", status="В работе",
                         due_date=TODAY.isoformat())
        self.insert_task(title="Вторая задача",
                         due_date=(TODAY + timedelta(days=1)).isoformat())

    def test_response_headers(self):
        self.fill()
        r = self.client.get("/export/xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn(XLSX_MIME, r.headers.get("Content-Type", ""))
        self.assertIn("attachment",
                      r.headers.get("Content-Disposition", ""))
        self.assertIn(".xlsx", r.headers.get("Content-Disposition", ""))

    def test_headers_and_data(self):
        self.fill()
        _, ws = self.get_sheet()
        headers = [ws.cell(row=1, column=c).value for c in range(1, 9)]
        self.assertEqual(headers, ["ID", "Название", "Описание",
                                   "Исполнитель", "Проект", "Статус",
                                   "Приоритет", "Срок выполнения"])
        self.assertEqual(ws.max_row, 3)          # шапка + 2 задачи
        self.assertEqual(ws.cell(row=2, column=2).value, "Обычная задача")
        self.assertEqual(ws.cell(row=2, column=4).value, "Анна")  # кириллица

    def test_header_style(self):
        self.fill()
        _, ws = self.get_sheet()
        cell = ws.cell(row=1, column=1)
        self.assertTrue(cell.font.bold)
        self.assertEqual(cell.font.size, 12)          # читаемая шапка
        self.assertIn("FFFFFF", str(cell.font.color.rgb))
        self.assertIn("2563EB", str(cell.fill.fgColor.rgb))
        # высоты хватает на перенос заголовка «Срок выполнения»
        self.assertGreaterEqual(ws.row_dimensions[1].height, 30)
        # «Срок выполнения» не разбивается посреди слова
        self.assertEqual(ws.cell(row=1, column=8).value, "Срок выполнения")

    def test_data_font_size(self):
        self.fill()
        _, ws = self.get_sheet()
        self.assertEqual(ws.cell(row=2, column=2).font.size, 11)

    def test_autofilter_and_freeze(self):
        self.fill()
        _, ws = self.get_sheet()
        self.assertEqual(ws.auto_filter.ref, "A1:H3")
        self.assertEqual(ws.freeze_panes, "A2")

    def test_stripe_fill_and_borders(self):
        self.fill()
        _, ws = self.get_sheet()
        first = ws.cell(row=2, column=2)
        second = ws.cell(row=3, column=2)
        self.assertIsNone(first.fill.patternType)          # 1-я строка чистая
        self.assertIn("F1F5F9", str(second.fill.fgColor.rgb))  # 2-я — полоса
        self.assertEqual(first.border.left.style, "thin")
        self.assertEqual(second.border.bottom.style, "thin")

    def test_wrap_text_in_text_columns(self):
        self.fill()
        _, ws = self.get_sheet()
        self.assertTrue(ws.cell(row=2, column=2).alignment.wrap_text)
        self.assertTrue(ws.cell(row=2, column=3).alignment.wrap_text)

    def test_real_excel_dates(self):
        self.fill()
        _, ws = self.get_sheet()
        cell = ws.cell(row=2, column=8)
        value = cell.value
        if hasattr(value, "date"):          # openpyxl может вернуть datetime
            value = value.date()
        self.assertEqual(value, TODAY)               # настоящая дата Excel
        self.assertEqual(cell.number_format, "DD.MM.YYYY")

    def test_empty_due_is_blank(self):
        self.insert_task(title="Без срока")
        _, ws = self.get_sheet()
        self.assertIsNone(ws.cell(row=2, column=8).value)

    def test_formula_values_stay_text(self):
        self.insert_task(title='=СУММ(A1:A2)', description="=1+1",
                         assignee="+Анна", project="-Проект")
        _, ws = self.get_sheet()
        title_cell = ws.cell(row=2, column=2)
        self.assertEqual(title_cell.data_type, "s")          # текст, не формула
        self.assertEqual(title_cell.value, "=СУММ(A1:A2)")
        for col in (3, 4, 5):
            self.assertEqual(ws.cell(row=2, column=col).data_type, "s")

    def test_row_height_follows_text(self):
        self.insert_task(title="Короткая")
        self.insert_task(
            title="Очень длинное название задачи, которое не поместится "
                  "в одну строку колонки и должно переноситься",
            description="строка раз\nстрока два\nстрока три")
        _, ws = self.get_sheet()
        heights = sorted([ws.row_dimensions[2].height,
                          ws.row_dimensions[3].height])
        self.assertEqual(heights[0], 15)               # короткая строка
        self.assertGreater(heights[1], 15)             # перенос учтён
        self.assertLessEqual(heights[1], 105)          # но не чрезмерная

    def test_print_setup(self):
        self.fill()
        _, ws = self.get_sheet()
        self.assertEqual(ws.page_setup.orientation, "landscape")
        self.assertEqual(int(ws.page_setup.paperSize), 9)   # A4
        self.assertEqual(int(ws.page_setup.fitToWidth), 1)
        self.assertEqual(int(ws.page_setup.fitToHeight), 0)
        self.assertTrue(ws.sheet_properties.pageSetUpPr.fitToPage)
        self.assertEqual(str(ws.print_title_rows).replace("$", ""), "1:1")
        area = ws.print_area
        area = area[0] if isinstance(area, list) else area
        self.assertEqual(str(area).split("!")[-1].replace("$", ""), "A1:H3")
        self.assertLessEqual(ws.page_margins.left, 0.4)
        self.assertLessEqual(ws.page_margins.top, 0.5)

    def test_column_widths(self):
        self.fill()
        _, ws = self.get_sheet()
        for letter in "ABCDEFGH":
            width = ws.column_dimensions[letter].width
            self.assertIsNotNone(width)
            self.assertGreater(width, 0)
        # текстовые колонки заметно шире служебных
        self.assertGreater(ws.column_dimensions["C"].width,
                           ws.column_dimensions["A"].width)

    def test_assignee_filter_applied(self):
        self.insert_task(title="Анны", assignee="Анна")
        self.insert_task(title="Бориса", assignee="Борис")
        from urllib.parse import quote
        _, ws = self.get_sheet("/export/xlsx?assignee=" + quote("Анна"))
        self.assertEqual(ws.max_row, 2)
        self.assertEqual(ws.cell(row=2, column=2).value, "Анны")

    def test_period_filter_applied(self):
        self.insert_task(title="Сегодня", due_date=TODAY.isoformat())
        self.insert_task(title="Давно",
                         due_date=(TODAY - timedelta(days=40)).isoformat())
        _, ws = self.get_sheet("/export/xlsx?period=7d")
        self.assertEqual(ws.max_row, 2)
        self.assertEqual(ws.cell(row=2, column=2).value, "Сегодня")

    def test_empty_export_still_valid(self):
        r, ws = self.get_sheet()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ws.max_row, 1)      # только шапка
        self.assertEqual(ws.cell(row=1, column=1).value, "ID")
