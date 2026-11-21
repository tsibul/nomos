// Меню в шапке: на телефоне сворачивается под кнопку. Без JS видно целиком.
export function initMenu() {
  const header = document.querySelector('[data-header]');
  if (!header) {
    return;
  }
  const toggle = header.querySelector('[data-menu-toggle]');
  // кнопки нет — сворачивать нечего
  if (!toggle) {
    return;
  }
  header.classList.add('header--collapsible');
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const open = header.classList.toggle('header--open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}
