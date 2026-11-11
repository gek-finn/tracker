// Task Tracker: модальное окно «Просмотр задачи».
// Открывается из списка, канбана и планирования без перезагрузки страницы.
// Данные приходят JSON-ом с сервера и вставляются через textContent —
// HTML или JavaScript из текста задачи не исполняется.

(function () {
  var modal = document.getElementById("task-view-modal");
  if (!modal) return;

  var titleEl = document.getElementById("tv-title");
  var descEl = document.getElementById("tv-desc");
  var dueEl = document.getElementById("tv-due");
  var overdueEl = document.getElementById("tv-overdue");
  var assigneeEl = document.getElementById("tv-assignee");
  var projectEl = document.getElementById("tv-project");
  var priorityEl = document.getElementById("tv-priority");
  var statusEl = document.getElementById("tv-status");
  var editBtn = document.getElementById("tv-edit");
  var closeBtn = document.getElementById("tv-close");
  var recurrenceEl = document.getElementById("tv-recurrence");

  var opener = null; // кнопка «Просмотр», на которую вернётся фокус

  function setText(el, value) {
    el.textContent = (value === null || value === undefined || value === "") ? "—" : value;
  }

  function open(taskId, btn) {
    opener = btn || null;

    // Состояние «загрузка», пока идёт запрос
    titleEl.textContent = "Загрузка…";
    setText(assigneeEl, null);
    setText(projectEl, null);
    priorityEl.textContent = "";
    statusEl.textContent = "";
    dueEl.textContent = "";
    overdueEl.classList.add("is-hidden");
    descEl.textContent = "";
    descEl.classList.remove("empty");
    editBtn.removeAttribute("href");

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    closeBtn.focus(); // фокус внутрь окна

    fetch("/task/" + taskId + "/view", { headers: { "Accept": "application/json" } })
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.httpOk || !result.data.ok) {
          throw new Error(result.data.error || "задача не найдена");
        }
        var task = result.data.task;
        titleEl.textContent = task.title;
        setText(assigneeEl, task.assignee);
        setText(projectEl, task.project);
        priorityEl.textContent = task.priority;
        priorityEl.className = "badge " + (task.priority_class || "");
        statusEl.textContent = task.status;
        statusEl.className = "badge " + (task.status_class || "");
        dueEl.textContent = task.due_date || "—";
        overdueEl.classList.toggle("is-hidden", !task.overdue);
        if (task.recurrence) {
          var recText = "↻ повторяется — " + task.recurrence.text;
          if (!task.recurrence.paused && !task.recurrence.stopped) {
            recText += " (следующий экземпляр: " + task.recurrence.next + ")";
          } else if (task.recurrence.paused) {
            recText += " — приостановлено";
          } else {
            recText += " — остановлено";
          }
          recurrenceEl.textContent = recText;
        } else {
          recurrenceEl.textContent = "—";
        }
        if (task.description) {
          descEl.textContent = task.description; // только текст, переносы сохранятся через pre-wrap
          descEl.classList.remove("empty");
        } else {
          descEl.textContent = "Описание не указано";
          descEl.classList.add("empty");
        }
        editBtn.setAttribute("href", "/task/" + task.id + "/edit");
      })
      .catch(function (error) {
        titleEl.textContent = "Ошибка";
        descEl.textContent = "Не удалось загрузить задачу: " + error.message + ".";
        descEl.classList.remove("empty");
      });
  }

  function close() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    if (opener && document.contains(opener)) {
      opener.focus(); // возвращаем фокус на кнопку «Просмотр»
    }
    opener = null;
  }

  // Открытие: делегирование — работает и для перетащенных карточек канбана
  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".js-task-view");
    if (btn) {
      event.preventDefault();
      open(btn.getAttribute("data-task-id"), btn);
      return;
    }
    // Клик по затемнению вне окна закрывает его
    if (modal.classList.contains("open") && event.target === modal) {
      close();
    }
  });

  closeBtn.addEventListener("click", close);

  document.addEventListener("keydown", function (event) {
    if (!modal.classList.contains("open")) return;

    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }

    // Цикл фокуса внутри окна (Tab / Shift+Tab)
    if (event.key === "Tab") {
      var items = [];
      if (editBtn.hasAttribute("href")) items.push(editBtn);
      items.push(closeBtn);
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
