// Task Tracker — этап 3: канбан-доска.
// Перетаскивание карточек и смена статуса выпадающим списком
// используют один и тот же серверный обработчик /task/<id>/status.

document.addEventListener("DOMContentLoaded", function () {
  var board = document.getElementById("kanban-board");
  if (!board) return;

  var STATUS_DONE = "Завершена";
  var draggedCard = null;

  // Дата сервера (ГГГГ-ММ-ДД) — единый источник для расчёта просрочки.
  // Браузер НЕ вычисляет дату сам: это исключает ошибки из-за часового
  // пояса или системных часов клиента. Правило то же, что в списке задач
  // и на дашборде: просрочена = незавершённая задача со сроком строго
  // раньше этой даты.
  var TODAY = board.getAttribute("data-today") || "";

  function isOverdue(card, status) {
    var due = card.getAttribute("data-due");
    return !!due && due !== "" && TODAY !== "" && due < TODAY && status !== STATUS_DONE;
  }

  // Приводим вид карточки в соответствие статусу (после смены статуса)
  function applyCardState(card, status) {
    var overdue = isOverdue(card, status);
    card.setAttribute("data-status", status);
    card.classList.toggle("card-done", status === STATUS_DONE);
    card.classList.toggle("card-overdue", overdue);
    var badge = card.querySelector(".kanban-overdue");
    if (badge) badge.classList.toggle("is-hidden", !overdue);
    var due = card.querySelector(".kanban-due");
    if (due) due.classList.toggle("due-overdue", overdue);
    var select = card.querySelector(".kanban-status-select");
    if (select) select.value = status;
  }

  function updateCounters() {
    board.querySelectorAll(".kanban-column").forEach(function (col) {
      var n = col.querySelectorAll(".kanban-card").length;
      var counter = col.querySelector(".kanban-count");
      if (counter) counter.textContent = n;
      var empty = col.querySelector(".kanban-empty");
      if (empty) empty.classList.toggle("is-hidden", n > 0);
    });
  }

  function showError(message) {
    var host = document.querySelector(".main-content");
    if (!host) return;
    var note = document.createElement("div");
    note.className = "flash flash-error";
    note.textContent = message;
    host.insertBefore(note, host.firstChild);
    note.addEventListener("click", function () { note.remove(); });
    setTimeout(function () { note.classList.add("flash-hide"); }, 4500);
    setTimeout(function () { note.remove(); }, 5200);
  }

  // Оптимистичный перенос: карточка переезжает сразу, а если сервер
  // не сохранил изменение — возвращается на место с сообщением об ошибке.
  function moveCard(card, newStatus) {
    var oldZone = card.closest(".kanban-cards");
    var oldStatus = card.getAttribute("data-status");
    if (!oldZone || oldStatus === newStatus) return;

    var newZone = board.querySelector('.kanban-cards[data-status="' + newStatus + '"]');
    if (!newZone) return;

    newZone.appendChild(card);
    applyCardState(card, newStatus);
    updateCounters();

    fetch("/task/" + card.getAttribute("data-task-id") + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (result.httpOk && result.data.ok) return; // успешно сохранено
        throw new Error(result.data.error || "сервер отклонил изменение");
      })
      .catch(function (error) {
        oldZone.appendChild(card);
        applyCardState(card, oldStatus);
        updateCounters();
        showError("Не удалось сохранить статус (" + error.message + "). Карточка возвращена на место.");
      });
  }

  // --- Перетаскивание мышью ---
  board.querySelectorAll(".kanban-card").forEach(function (card) {
    card.addEventListener("dragstart", function (event) {
      draggedCard = card;
      card.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        try {
          event.dataTransfer.setData("text/plain", card.getAttribute("data-task-id"));
        } catch (e) { /* старые браузеры */ }
      }
    });

    card.addEventListener("dragend", function () {
      card.classList.remove("dragging");
      draggedCard = null;
      board.querySelectorAll(".kanban-cards.drop-hover").forEach(function (zone) {
        zone.classList.remove("drop-hover");
      });
    });
  });

  board.querySelectorAll(".kanban-cards").forEach(function (zone) {
    zone.addEventListener("dragover", function (event) {
      event.preventDefault(); // без этого браузер не разрешит drop
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      zone.classList.add("drop-hover");
    });

    zone.addEventListener("dragleave", function () {
      zone.classList.remove("drop-hover");
    });

    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      zone.classList.remove("drop-hover");
      var card = draggedCard || board.querySelector(".kanban-card.dragging");
      if (!card) return;
      draggedCard = null;
      moveCard(card, zone.getAttribute("data-status"));
    });
  });

  // --- Смена статуса списком (для устройств без Drag & Drop) ---
  board.querySelectorAll(".kanban-status-select").forEach(function (select) {
    select.addEventListener("change", function () {
      moveCard(select.closest(".kanban-card"), select.value);
    });
  });
});
