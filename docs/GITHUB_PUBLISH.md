# Публикация проекта на GitHub

Проект уже подготовлен: `.gitignore` исключает рабочие базы, окружение,
резервные копии и секреты; `.env.example` показывает переменные без
реальных значений; зависимости зафиксированы версиями.

## 1. Установите Git (если нет)

<https://git-scm.com/download/win> — далее всё в терминале из папки
проекта (`E:\vibecoding\tracker`).

## 2. Проверка «что попадёт в репозиторий»

```
git init
git add .
git status
```

В списке **не должно быть**: `tasks.db`, `.venv/`, `backups/`,
`__pycache__/`, `.env`, `.test_tmp/`, `.pip_tmp/`.
Если что-то из этого появилось — проверьте `.gitignore`.

Быстрая проверка на секреты (не должно быть реальных значений):

```
git grep -iE "password|secret|token" -- .
```

Допустимо: `.env.example` с плейсхолдерами, `TRACKER_ADMIN_PASSWORD`
в коде (читается из окружения), упоминания в документации.

## 3. Первый коммит

```
git add .
git commit -m "Task Tracker: CRUD, kanban, planning, recurrences, reports, export, backups, tests"
git branch -M main
```

## 4. Создание репозитория на GitHub

1. <https://github.com/new> → имя, например `task-tracker`; **Private**
   или **Public** (для портфолио — Public).
2. Без README/.gitignore/license (уже есть в проекте).

## 5. Загрузка

```
git remote add origin https://github.com/ВАШ_ЛОГИН/task-tracker.git
git push -u origin main
```

Альтернатива без командной строки: **GitHub Desktop** → Add Existing
Repository → Publish.

## 6. После публикации

- Убедитесь, что `tasks.db` не виден в репозитории на сайте.
- В Settings → Secrets ничего добавлять не нужно (секреты задаются
  только на Railway при деплое).
- Дальше: [DEPLOY_RAILWAY.md](DEPLOY_RAILWAY.md).

## Чек-лист перед пушем

- [ ] `git status` чистый от баз/окружения/копий
- [ ] `.env` отсутствует (есть только `.env.example` с плейсхолдерами)
- [ ] Реальный пароль администратора нигде не записан
- [ ] Скриншоты (если добавляете в README) без реальных персональных данных
