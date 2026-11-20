// Ссылки с data-keep-hash (переключатель языка, «действующая редакция») ведут на то же
// место страницы: к адресу добавляется текущий якорь.
export function initKeepHash() {
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-keep-hash]');
    if (!link || !window.location.hash) {
      return;
    }
    const url = new URL(link.href);
    url.hash = window.location.hash;
    link.href = url.toString();
  });
}
