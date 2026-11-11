-- Схема базы данных Task Tracker
-- due_date хранится в формате ISO: ГГГГ-ММ-ДД

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    assignee        TEXT    NOT NULL DEFAULT '',
    project         TEXT    NOT NULL DEFAULT '',
    priority        TEXT    NOT NULL DEFAULT 'Средний',
    status          TEXT    NOT NULL DEFAULT 'Новая',
    due_date        TEXT,
    recurrence_id   INTEGER REFERENCES recurrences(id),
    recurrence_date TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Индексы под фильтрацию и задачи исполнителя
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);

-- Уникальный индекс (recurrence_id, recurrence_date), защищающий от дублей
-- экземпляров, создаётся в db.py:init_db ПОСЛЕ миграции столбцов (для баз,
-- созданных до этапа 5, ALTER TABLE выполняется раньше индекса).

-- Этап 5: расписания повторяющихся задач.
-- Экземпляры — обычные строки tasks со ссылкой recurrence_id.
CREATE TABLE IF NOT EXISTS recurrences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    assignee       TEXT    NOT NULL DEFAULT '',
    project        TEXT    NOT NULL DEFAULT '',
    priority       TEXT    NOT NULL DEFAULT 'Средний',
    frequency      TEXT    NOT NULL CHECK (frequency IN ('daily', 'weekly', 'monthly')),
    weekday        INTEGER,             -- weekly: 0=понедельник .. 6=воскресенье
    month_day      INTEGER,             -- monthly: 1..31 (при отсутствии — последний день месяца)
    start_date     TEXT    NOT NULL,    -- дата первого экземпляра (срок задачи-источника)
    end_date       TEXT,                -- NULL = без даты окончания
    paused         INTEGER NOT NULL DEFAULT 0,
    stopped        INTEGER NOT NULL DEFAULT 0,
    last_generated TEXT,                -- последняя дата, на которую создан экземпляр
    next_date      TEXT    NOT NULL,    -- следующая дата генерации (строго после last_generated)
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_recurrences_active ON recurrences(paused, stopped);
