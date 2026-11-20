"""Язык интерфейса: следует за языком страницы; вне акта — параметр `?lang=` (без cookies)."""

from . import reading

# языки, на которые переведён интерфейс; прочие показываются по-русски
UI_LANGS = ('ru', 'el')


def ui_lang(request):
    """Язык интерфейса страницы: язык текста акта, иначе `?lang=`, иначе русский."""
    lang = getattr(request, 'ui_lang', None) or request.GET.get('lang')
    if lang in UI_LANGS:
        return lang
    return reading.READER_LANG


def lang_query(lang):
    """Хвост адреса страниц вне акта (список, «О проекте»): язык, если он не русский."""
    if lang in UI_LANGS and lang != reading.READER_LANG:
        return f'?lang={lang}'
    return ''


def ui(request):
    """Контекстный процессор: язык интерфейса и хвост адреса для ссылок вне акта."""
    lang = ui_lang(request)
    return {
        'ui_lang': lang,
        'lang_query': lang_query(lang),
    }
