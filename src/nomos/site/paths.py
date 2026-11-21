"""Адреса сайта с учётом подпути (`NOMOS_URL_PREFIX`, например `/resources/nomos`).

Пути внутри приложения пишутся от корня сайта (`/about/`); наружу, в ссылки и
перенаправления, они выходят с подпутём. Пустой подпуть — сайт в корне домена.
"""

from django.conf import settings


def url(path):
    """Внешний адрес пути сайта: `/about/` → `/resources/nomos/about/`."""
    return settings.NOMOS_URL_PREFIX + path


def local(address):
    """Путь сайта из внешнего адреса или None, если адрес вне подпути."""
    prefix = settings.NOMOS_URL_PREFIX
    if not prefix:
        return address
    if address.startswith(prefix + '/'):
        return address.removeprefix(prefix)
    return None
