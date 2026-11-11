// Task Tracker — этап 1 (MVP).
// Подтверждение удаления задачи и автоскрытие уведомлений.

document.addEventListener("DOMContentLoaded", function () {
  // --- Автоскрытие уведомлений (flash) через 4 секунды ---
  document.querySelectorAll(".flash").forEach(function (el) {
    setTimeout(function () {
      el.classList.add("flash-hide");
    }, 4000);
  });

  // --- Защита от повторной отправки формы задачи ---
  // Первое нажатие «Сохранить» блокирует кнопку до завершения запроса,
  // поэтому двойной клик или повторный Enter не отправляет форму дважды.
  var taskForm = document.querySelector(".js-task-form");
  if (taskForm) {
    taskForm.addEventListener("submit", function (event) {
      if (taskForm.dataset.sent === "1") {
        event.preventDefault(); // повторная отправка во время обработки запроса
        return;
      }
      taskForm.dataset.sent = "1";
      var submitButton = taskForm.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Сохранение…";
      }
    });
  }

  // --- Диалог подтверждения (удаление задач и другие необратимые действия) ---
  var modal = document.getElementById("confirm-modal");
  if (!modal) return;

  var text = document.getElementById("confirm-modal-text");
  var title = document.getElementById("confirm-modal-title");
  var defaultTitle = title.textContent;
  var currentForm = null;

  function openConfirm(form) {
    currentForm = form;
    var customTitle = form.getAttribute("data-confirm-title");
    title.textContent = customTitle || defaultTitle;
    text.textContent = form.getAttribute("data-confirm-text")
      || ("Удалить задачу «" + (form.getAttribute("data-task-title") || "эту задачу")
          + "»? Это действие нельзя отменить.");
    var customButton = form.getAttribute("data-confirm-button");
    document.getElementById("confirm-yes").textContent = customButton || "Удалить";
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  document.querySelectorAll(".js-delete-form, .js-confirm-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      openConfirm(form);
    });
  });

  // --- Настройка «Повторение» в форме задачи: показ нужных полей ---
  function syncRecurrenceFields(select) {
    var scope = select.closest("form") || document;
    var frequency = select.value;
    var weekday = scope.querySelector(".js-recurrence-weekday");
    var monthday = scope.querySelector(".js-recurrence-monthday");
    var end = scope.querySelector(".js-recurrence-end");
    var hint = scope.querySelector(".js-recurrence-hint");
    if (weekday) weekday.classList.toggle("is-hidden", frequency !== "weekly");
    if (monthday) monthday.classList.toggle("is-hidden", frequency !== "monthly");
    if (end) end.classList.toggle("is-hidden", frequency === "none");
    if (hint) hint.classList.toggle("is-hidden", frequency === "none");
  }

  document.querySelectorAll(".js-recurrence-frequency").forEach(function (select) {
    syncRecurrenceFields(select);
    select.addEventListener("change", function () { syncRecurrenceFields(select); });
  });

  function closeModal() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    currentForm = null;
  }

  document.getElementById("confirm-yes").addEventListener("click", function () {
    if (currentForm) currentForm.submit();
  });

  document.getElementById("confirm-no").addEventListener("click", closeModal);

  modal.addEventListener("click", function (event) {
    if (event.target === modal) closeModal();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
  });
});
