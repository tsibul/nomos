"""Доступ к сайту: пока всё — только после входа (решение пользователя).

Одно место для правила: middleware пропускает без входа только адреса из списка исключений,
остальное переадресует на вход с возвратом (`next`). Когда часть страниц станет открытой,
проверка добавится сюда же, а не декораторами на видах.
"""

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.urls import Resolver404, resolve

# виды, открытые без входа (имена адресов)
PUBLIC_URL_NAMES = {
    'site:login',
    'site:robots',
}


def is_public(request):
    """Адрес открыт без входа: статика, админка (свой вход, только при DEBUG), исключения."""
    path = request.path_info
    if path.startswith('/' + settings.STATIC_URL.lstrip('/')):
        return True
    if settings.DEBUG and path.startswith('/admin/'):
        return True
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return match.view_name in PUBLIC_URL_NAMES


class LoginRequiredMiddleware:
    """Без входа — переадресация на страницу входа с возвратом на запрошенный адрес."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or is_public(request):
            return self.get_response(request)
        return redirect_to_login(request.get_full_path())
