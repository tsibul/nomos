// Показ греческого оригинала рядом с переводом без перезагрузки.
// Без JS ссылка ведёт на ?orig=1; с JS меняется класс и адрес в истории.
const SHOW_TEXT = 'Показать греческий текст рядом';
const HIDE_TEXT = 'Скрыть греческий текст';

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
    toggle.textContent = shown ? HIDE_TEXT : SHOW_TEXT;
    toggle.href = shown ? '?' : '?orig=1';
  });
}
