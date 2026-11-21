"""Что видит посетитель: при `NOMOS_PUBLIC_ONLY` аноним — только публичные выражения.

Настройка выключена — видно всё. Включена — посетитель без входа видит только выражения
с `Expression.public = True`; вошедший (`request.user.is_authenticated`) — всё. Страницы
входа для этого не нужны: проверяется только пользователь запроса.

Ограничение запроса хранится в контекстной переменной (её ставит `PublicOnlyMiddleware`),
поэтому функции просмотра не передают запрос по цепочке. Вне запроса (тесты функций,
команды) ограничения нет.
"""

from contextvars import ContextVar

from django.conf import settings

_restricted = ContextVar('nomos_restricted', default=False)


def is_restricted():
    """Показывать ли только публичные выражения в текущем запросе."""
    return _restricted.get()


def restricted_for(request):
    """Ограничен ли посетитель запроса: настройка включена и вход не выполнен."""
    if not settings.NOMOS_PUBLIC_ONLY:
        return False
    user = getattr(request, 'user', None)
    return not (user is not None and user.is_authenticated)


class PublicOnlyMiddleware:
    """Ставит ограничение на время запроса (после AuthenticationMiddleware)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _restricted.set(restricted_for(request))
        try:
            return self.get_response(request)
        finally:
            _restricted.reset(token)


def expressions(act):
    """Видимые выражения акта (из act.expressions.all() — prefetch не теряется)."""
    found = list(act.expressions.all())
    if is_restricted():
        found = [expression for expression in found if expression.public]
    return found


def visible_langs(act):
    """Языки видимых выражений акта; запоминаются на объекте акта для каждого режима."""
    restricted = is_restricted()
    cache = act.__dict__.setdefault('_visible_langs', {})
    if restricted not in cache:
        cache[restricted] = {expression.lang for expression in expressions(act)}
    return cache[restricted]


def act_visible(act):
    """Виден ли акт: без ограничения — всегда, иначе — есть публичное выражение."""
    return not is_restricted() or bool(visible_langs(act))


def lang_visible(act, lang):
    """Виден ли текст акта на языке: без ограничения — всегда."""
    return not is_restricted() or lang in visible_langs(act)


def texts(queryset):
    """Тексты фрагментов (FragmentText) только видимых выражений."""
    if is_restricted():
        return queryset.filter(expression__public=True)
    return queryset


def expression_queryset(queryset):
    """Выражения (queryset) — только видимые."""
    if is_restricted():
        return queryset.filter(public=True)
    return queryset
