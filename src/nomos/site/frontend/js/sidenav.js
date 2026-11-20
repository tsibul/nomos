// Левая колонка: на широком экране всегда раскрыта, текущая статья видна в колонке.
// Без JS — обычный раскрывающийся блок.
const WIDE = '(min-width: 1024px)';

export function initSidenav() {
  const sidenav = document.querySelector('[data-sidenav]');
  if (!sidenav) {
    return;
  }
  const details = sidenav.querySelector('details');
  const media = window.matchMedia(WIDE);
  const sync = () => {
    sidenav.classList.toggle('sidenav--fixed', media.matches);
    details.open = media.matches;
  };
  sync();
  media.addEventListener('change', sync);
  const current = sidenav.querySelector('[aria-current="page"]');
  if (current && media.matches) {
    sidenav.scrollTop = current.offsetTop - sidenav.clientHeight / 2;
  }
}
