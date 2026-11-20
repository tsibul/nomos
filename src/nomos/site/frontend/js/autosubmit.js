// Форма даты отправляется сама при выборе даты; кнопка остаётся для работы без JS.
export function initAutosubmit() {
  const forms = document.querySelectorAll('form[data-autosubmit]');
  forms.forEach((form) => {
    const input = form.querySelector('input[type="date"]');
    if (!input) {
      return;
    }
    input.addEventListener('change', () => {
      if (input.checkValidity()) {
        form.submit();
      }
    });
  });
}
