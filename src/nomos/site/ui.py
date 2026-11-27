"""Язык интерфейса: следует за языком страницы; вне акта — параметр `?lang=`
(без cookies).

Строки интерфейса — средствами Django (`gettext`, `{% translate %}`), каталоги
в
`nomos/locale/`. Исходный язык строк — русский; полностью переведён греческий,
на английский —
только отказ от ответственности (прочие строки выходят по-русски).
"""

from django.utils import translation

from . import access, reading

UI_LANGS = reading.UI_LANGS

# языки, которые можно включить: есть каталог или это исходный язык
ACTIVE_LANGS = ("ru", "el", "en")


def activation_lang(lang):
    """Язык, который включается для страницы на языке lang: свой, если есть,
    иначе русский."""
    return lang if lang in ACTIVE_LANGS else reading.READER_LANG


def lang_query(lang):
    """Хвост адреса страниц вне акта (список, «О проекте»): язык, если он не
    русский."""
    if lang in UI_LANGS and lang != reading.READER_LANG:
        return f"?lang={lang}"
    return ""


class InterfaceLanguageMiddleware:
    """Включает язык интерфейса из `?lang=` (ru, el), иначе русский. Cookies не
    ставит.

    Страница акта уточняет язык сама — по языку текста в адресе (`views.read`).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = request.GET.get("lang")
        if lang not in UI_LANGS:
            lang = reading.READER_LANG
        with translation.override(lang):
            return self.get_response(request)


def ui(request):
    """Контекстный процессор: язык интерфейса, хвост адреса для ссылок вне акта
    и режим
    «только публичное» (посетитель без входа при NOMOS_PUBLIC_ONLY)."""
    lang = reading.interface_lang()
    return {
        "ui_lang": lang,
        "lang_query": lang_query(lang),
        "public_only": access.is_restricted(),
    }
