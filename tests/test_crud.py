# -*- coding: utf-8 -*-
"""CRUD задач, валидация форм и защита от повторной отправки."""
from tests.base import TrackerTestCase


class TaskCrudTests(TrackerTestCase):

    def test_create_task(self):
        r = self.post_new_task(title="Новая задача", assignee="Анна",
                               project="Сайт", priority="Высокий",
                               status="Новая", due_date="2026-12-01")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.count_tasks(), 1)
        task = self.get_task(1)
        self.assertEqual(task["title"], "Новая задача")
        self.assertEqual(task["assignee"], "Анна")
        self.assertEqual(task["project"], "Сайт")
        self.assertEqual(task["priority"], "Высокий")
        self.assertEqual(task["due_date"], "2026-12-01")

    def test_create_without_title_rejected(self):
        self.post_new_task(title="", due_date="2026-12-01")
        self.assertEqual(self.count_tasks(), 0)

    def test_create_long_title_rejected(self):
        self.post_new_task(title="А" * 201)
        self.assertEqual(self.count_tasks(), 0)

    def test_create_bad_date_rejected(self):
        self.post_new_task(title="Задача", due_date="31.12.2026")
        self.assertEqual(self.count_tasks(), 0)

    def test_create_bad_priority_rejected(self):
        self.post_new_task(title="Задача", priority="Мега")
        self.assertEqual(self.count_tasks(), 0)

    def test_create_bad_status_rejected(self):
        self.post_new_task(title="Задача", status="Почти готово")
        self.assertEqual(self.count_tasks(), 0)

    def test_create_task_without_due_date(self):
        self.post_new_task(title="Без срока")
        task = self.get_task(1)
        self.assertIsNone(task["due_date"])

    def test_edit_task(self):
        task_id = self.insert_task(title="Старое название", assignee="Борис")
        r = self.post_edit_task(task_id, title="Новое название",
                                assignee="Анна", project="Сайт",
                                priority="Высокий", status="В работе",
                                due_date="2026-12-05")
        self.assertEqual(r.status_code, 200)
        task = self.get_task(task_id)
        self.assertEqual(task["title"], "Новое название")
        self.assertEqual(task["assignee"], "Анна")
        self.assertEqual(task["status"], "В работе")
        self.assertEqual(task["due_date"], "2026-12-05")

    def test_edit_validation_keeps_old_values(self):
        task_id = self.insert_task(title="Старое название")
        self.post_edit_task(task_id, title="")
        self.assertEqual(self.get_task(task_id)["title"], "Старое название")

    def test_delete_task(self):
        task_id = self.insert_task(title="На удаление")
        r = self.client.post("/task/%d/delete" % task_id,
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.get_task(task_id))

    def test_delete_missing_task_404(self):
        self.assertEqual(self.client.post("/task/999/delete").status_code, 404)

    def test_edit_missing_task_404(self):
        self.assertEqual(self.client.get("/task/999/edit").status_code, 404)

    def test_duplicate_post_rejected(self):
        token = self.next_token()
        data = {"form_token": token, "title": "Единственная",
                "priority": "Средний", "status": "Новая"}
        self.client.post("/task/new", data=data, follow_redirects=True)
        self.client.post("/task/new", data=data, follow_redirects=True)
        self.assertEqual(self.count_tasks(), 1)

    def test_forms_render(self):
        self.assertEqual(self.client.get("/task/new").status_code, 200)
        task_id = self.insert_task(title="Задача")
        r = self.client.get("/task/%d/edit" % task_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Задача", r.get_data(as_text=True))
