"""Что видит посетитель при `NOMOS_PUBLIC_ONLY`.

Настройка выключена — видно всё. Включена:
- посетитель без входа видит только выражения с `Expression.public = True`;
- вошедший видит всё, кроме непубличных выражений актов МСФО
  (`Act.jurisdiction = "ifrs"`, тексты закрыты по лицензии);
- суперпользователь (`is_superuser`) видит всё.

Режим запроса хранится в контекстной переменной (её ставит
`PublicOnlyMiddleware`), поэтому функции просмотра не передают запрос по
цепочке. Вне запроса (тесты функций, команды) ограничения нет.

Ограничение относится к акту: `limited(act)` — показываются ли у акта только
публичные выражения. Гостю — у всех актов, вошедшему — только у актов МСФО.
"""

from contextvars import ContextVar

from django.conf import settings

from nomos.acts.models import Act

# режимы запроса: всё видно (None), только публичное, закрыты тексты МСФО
PUBLIC = "public"
CLOSED_IFRS = "closed_ifrs"

IFRS = Act.Jurisdiction.IFRS

_mode = ContextVar("nomos_access_mode", default=None)


def is_restricted():
    """Гость при NOMOS_PUBLIC_ONLY: показывать только публичные выражения
    всех актов."""
    return _mode.get() == PUBLIC


def mode_for(request):
    """Режим посетителя запроса: None — видно всё."""
    if not settings.NOMOS_PUBLIC_ONLY:
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return PUBLIC
    if user.is_superuser:
        return None
    return CLOSED_IFRS


def restricted_for(request):
    """Ограничен ли посетитель запроса публичным: настройка включена и вход
    не выполнен."""
    return mode_for(request) == PUBLIC


class PublicOnlyMiddleware:
    """Ставит режим на время запроса (после AuthenticationMiddleware)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _mode.set(mode_for(request))
        try:
            return self.get_response(request)
        finally:
            _mode.reset(token)


def limited(act):
    """Показываются ли у акта в текущем запросе только публичные
    выражения."""
    mode = _mode.get()
    if mode == PUBLIC:
        return True
    return mode == CLOSED_IFRS and act.jurisdiction == IFRS


def expression_visible(expression, act):
    """Видно ли выражение акта в текущем запросе."""
    return expression.public or not limited(act)


def expressions(act):
    """Видимые выражения акта (из act.expressions.all() — prefetch не
    теряется)."""
    return [
        expression
        for expression in act.expressions.all()
        if expression_visible(expression, act)
    ]


def visible_langs(act):
    """Языки видимых выражений акта; запоминаются на объекте акта для каждого
    режима."""
    mode = _mode.get()
    cache = act.__dict__.setdefault("_visible_langs", {})
    if mode not in cache:
        cache[mode] = {expression.lang for expression in expressions(act)}
    return cache[mode]


def act_visible(act):
    """Виден ли акт: без ограничения — всегда, иначе — есть публичное
    выражение."""
    return not limited(act) or bool(visible_langs(act))


def lang_visible(act, lang):
    """Виден ли текст акта на языке: без ограничения — всегда."""
    return not limited(act) or lang in visible_langs(act)


def texts(queryset):
    """Тексты фрагментов (FragmentText) только видимых выражений."""
    mode = _mode.get()
    if mode == PUBLIC:
        return queryset.filter(expression__public=True)
    if mode == CLOSED_IFRS:
        return queryset.exclude(
            expression__public=False, expression__act__jurisdiction=IFRS
        )
    return queryset


def expression_queryset(queryset):
    """Выражения (queryset) — только видимые."""
    mode = _mode.get()
    if mode == PUBLIC:
        return queryset.filter(public=True)
    if mode == CLOSED_IFRS:
        return queryset.exclude(public=False, act__jurisdiction=IFRS)
    return queryset
