# -*- coding: utf-8 -*-
"""Production-конфигурация, healthcheck и защита изменяющих операций."""
import base64
import os
from unittest.mock import patch

import app as app_module
from tests.base import TrackerTestCase


def basic_auth(password, user="admin"):
    token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    return {"Authorization": "Basic " + token}


class AppConfigTests(TrackerTestCase):

    def _create_app(self):
        # create_app читает переменные окружения в момент вызова;
        # init_db выполняется на текущей тестовой базе (безопасно)
        return app_module.create_app()

    def test_debug_off_by_default(self):
        # production-дефолт: debug выключен, пока явно не включён
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TASK_TRACKER_DEBUG", None)
            fresh = self._create_app()
        self.assertFalse(fresh.config["DEBUG"])

    def test_debug_enabled_by_env(self):
        with patch.dict(os.environ, {"TASK_TRACKER_DEBUG": "1"}):
            fresh = self._create_app()
        self.assertTrue(fresh.config["DEBUG"])

    def test_secret_key_from_env(self):
        with patch.dict(os.environ,
                        {"TASK_TRACKER_SECRET": "unit-test-secret-123"}):
            fresh = self._create_app()
        self.assertEqual(fresh.config["SECRET_KEY"], "unit-test-secret-123")

    def test_secret_key_random_without_env(self):
        # секрет не «зашит» в код: без переменной — случайный
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TASK_TRACKER_SECRET", None)
            fresh = self._create_app()
        key = fresh.config["SECRET_KEY"]
        self.assertNotEqual(key, "task-tracker-dev")
        self.assertGreaterEqual(len(key), 64)

    def test_https_sets_secure_cookies(self):
        with patch.dict(os.environ, {"TRACKER_HTTPS": "1"}):
            fresh = self._create_app()
        self.assertTrue(fresh.config["SESSION_COOKIE_SECURE"])

    def test_cookie_safety_defaults(self):
        fresh = self._create_app()
        self.assertTrue(fresh.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(fresh.config["SESSION_COOKIE_SAMESITE"], "Lax")


class HealthcheckTests(TrackerTestCase):

    def test_healthz_ok(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["db"], "ok")


class WriteProtectionTests(TrackerTestCase):
    """Если задан TRACKER_ADMIN_PASSWORD: запись — только для админа."""

    PASSWORD = "correct horse battery staple"

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ,
                             {"TRACKER_ADMIN_PASSWORD": self.PASSWORD})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_are_public(self):
        self.insert_task(title="Открытый просмотр")
        for url in ["/", "/dashboard", "/kanban", "/plan", "/recurrences",
                    "/export/csv", "/export/xlsx", "/healthz",
                    "/task/1/view"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_post_without_auth_rejected(self):
        r = self.client.post("/task/new", data={
            "form_token": "t1", "title": "Захват", "priority": "Средний",
            "status": "Новая"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.count_tasks(), 0)

    def test_post_wrong_password_rejected(self):
        r = self.client.post("/task/new", headers=basic_auth("wrong"),
                             data={"form_token": "t2", "title": "Захват",
                                   "priority": "Средний", "status": "Новая"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.count_tasks(), 0)

    def test_post_with_auth_allowed(self):
        r = self.client.post("/task/new", headers=basic_auth(self.PASSWORD),
                             data={"form_token": "t3", "title": "От админа",
                                   "priority": "Средний", "status": "Новая"},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.count_tasks(), 1)

    def test_status_change_requires_auth(self):
        task_id = self.insert_task(title="На канбане")
        r = self.client.post("/task/%d/status" % task_id,
                             json={"status": "В работе"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/task/%d/status" % task_id,
                             headers=basic_auth(self.PASSWORD),
                             json={"status": "В работе"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_task(task_id)["status"], "В работе")

    def test_delete_requires_auth(self):
        task_id = self.insert_task(title="Не удалить")
        r = self.client.post("/task/%d/delete" % task_id)
        self.assertEqual(r.status_code, 401)
        self.assertIsNotNone(self.get_task(task_id))

    def test_form_pages_require_auth(self):
        # страницы форм изменения открывают Basic-диалог браузера,
        # после которого креды кэшируются для всех запросов сайта
        self.insert_task(title="Существующая")
        for url in ["/task/new", "/task/1/edit"]:
            self.assertEqual(self.client.get(url).status_code, 401, url)
            r = self.client.get(url, headers=basic_auth(self.PASSWORD))
            self.assertEqual(r.status_code, 200, url)

    def test_recurrence_actions_require_auth(self):
        from datetime import date
        self.insert_recurrence(title="Расписание", frequency="daily",
                               start_date=date.today().isoformat(),
                               next_date=date.today().isoformat())
        for url in ["/recurrence/1/pause", "/recurrence/1/resume",
                    "/recurrence/1/stop"]:
            r = self.client.post(url)
            self.assertEqual(r.status_code, 401, url)

    def test_no_password_means_local_mode(self):
        # без переменной (локальный запуск) авторизации нет
        with patch.dict(os.environ, {"TRACKER_ADMIN_PASSWORD": ""}):
            r = self.client.post("/task/new", data={
                "form_token": "t4", "title": "Локально",
                "priority": "Средний", "status": "Новая"},
                follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.count_tasks(), 1)


class ProductionModeTests(TrackerTestCase):
    """Режим production: fail closed и обязательная конфигурация."""

    def test_fail_closed_without_password(self):
        environ = {"TRACKER_ENV": "production",
                   "TASK_TRACKER_SECRET": "s" * 32,
                   "TASK_TRACKER_DB": self.db_path}
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                app_module.create_app()
        self.assertIn("TRACKER_ADMIN_PASSWORD", str(ctx.exception))

    def test_fail_closed_without_secret(self):
        environ = {"TRACKER_ENV": "production",
                   "TRACKER_ADMIN_PASSWORD": "some-password",
                   "TASK_TRACKER_DB": self.db_path}
        with patch.dict(os.environ, environ, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                app_module.create_app()
        self.assertIn("TASK_TRACKER_SECRET", str(ctx.exception))

    def test_production_config(self):
        environ = {"TRACKER_ENV": "production",
                   "TRACKER_ADMIN_PASSWORD": "some-password",
                   "TASK_TRACKER_SECRET": "s" * 32,
                   "TASK_TRACKER_DEBUG": "1",       # должен игнорироваться
                   "TASK_TRACKER_DB": self.db_path}
        with patch.dict(os.environ, environ, clear=True):
            fresh = app_module.create_app()
        self.assertTrue(fresh.config["TRACKER_PRODUCTION"])
        self.assertFalse(fresh.config["DEBUG"])
        self.assertEqual(fresh.config["SECRET_KEY"], "s" * 32)
        self.assertTrue(fresh.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(fresh.config["SESSION_COOKIE_HTTPONLY"])

    def test_railway_autodetected_as_production(self):
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "abc"}):
            self.assertTrue(app_module._is_production())
        # явный локальный режим главнее автодетекта
        with patch.dict(os.environ, {"TRACKER_ENV": "local",
                                     "RAILWAY_SERVICE_ID": "abc"}):
            self.assertFalse(app_module._is_production())

    def test_explicit_local_mode(self):
        with patch.dict(os.environ, {"TRACKER_ENV": "local"}):
            self.assertFalse(app_module._is_production())

    def test_dev_server_binds_localhost_in_local_mode(self):
        with patch.dict(os.environ, {"TRACKER_ENV": "local"}):
            host, debug = app_module._dev_server_config()
        self.assertEqual(host, "127.0.0.1")
        with patch.dict(os.environ, {"TRACKER_ENV": "local",
                                     "TASK_TRACKER_DEBUG": "0"}):
            self.assertFalse(app_module._dev_server_config()[1])

    def test_dev_server_never_debug_in_production(self):
        with patch.dict(os.environ, {"TRACKER_ENV": "production",
                                     "TASK_TRACKER_DEBUG": "1"}):
            host, debug = app_module._dev_server_config()
        self.assertFalse(debug)


class ProductionAuthTests(TrackerTestCase):
    """Production: всё закрыто Basic Auth, кроме /healthz и статики."""

    PASSWORD = "secret-pass-123"

    def setUp(self):
        super().setUp()
        self.insert_task(title="Секретная задача производства")
        patcher = patch.dict(os.environ, {
            "TRACKER_ENV": "production",
            "TRACKER_ADMIN_PASSWORD": self.PASSWORD,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_pages_401_without_auth(self):
        for url in ["/", "/dashboard", "/kanban", "/plan", "/recurrences",
                    "/task/1/view", "/task/1/edit", "/task/new",
                    "/export/csv", "/export/xlsx"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 401, url)

    def test_write_operations_401_without_auth(self):
        r = self.client.post("/task/1/delete")
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/task/1/status", json={"status": "В работе"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.count_tasks(), 1)

    def test_wrong_password_401(self):
        r = self.client.get("/", headers=basic_auth("wrong"))
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/", headers=basic_auth(self.PASSWORD, user="root"))
        self.assertEqual(r.status_code, 401)

    def test_healthz_public_and_safe(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        # healthcheck не раскрывает данные приложения
        self.assertNotIn("Секретная задача", r.get_data(as_text=True))

    def test_static_public(self):
        r = self.client.get("/static/css/style.css")
        self.assertEqual(r.status_code, 200)

    def test_correct_password_grants_access(self):
        headers = basic_auth(self.PASSWORD)
        r = self.client.get("/", headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Секретная задача", r.get_data(as_text=True))
        r = self.client.get("/export/csv", headers=headers)
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/task/new", headers=headers, data={
            "form_token": "p1", "title": "Из production",
            "priority": "Средний", "status": "Новая"},
            follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.count_tasks(), 2)
