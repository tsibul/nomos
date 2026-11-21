// Показ оригинала (греческого или английского) рядом с переводом без перезагрузки.
// Без JS ссылка ведёт на ?orig=1; с JS меняется класс и адрес в истории.
// Подписи ссылки — из data-show-text и data-hide-text (на языке интерфейса).

export function initOriginalToggle() {
  const unit = document.querySelector('[data-unit]');
  const toggle = document.querySelector('[data-original-toggle]');
  if (!unit || !toggle) {
    return;
  }
  toggle.addEventListener('click', (event) => {
    event.preventDefault();
    const shown = unit.classList.toggle('unit--with-original');
    const url = new URL(window.location.href);
    if (shown) {
      url.searchParams.set('orig', '1');
    } else {
      url.searchParams.delete('orig');
    }
    window.history.replaceState(null, '', url);
    toggle.textContent = shown ? toggle.dataset.hideText : toggle.dataset.showText;
    toggle.href = shown ? '?' : '?orig=1';
  });
}
