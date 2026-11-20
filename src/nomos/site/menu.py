"""Главное меню: разделы по юрисдикциям; раздел активен, если в базе есть его акты."""

from nomos.acts.models import Act

from . import strings, ui

SECTIONS = [
    (Act.Jurisdiction.CY, 'section_cy'),
    (Act.Jurisdiction.EU, 'section_eu'),
]


def section_codes():
    return [code for code, _label in SECTIONS]


def section_label(code, lang):
    """Название раздела на языке интерфейса."""
    return strings.text(dict(SECTIONS)[code], lang)


def menu(request):
    """Контекстный процессор: пункты меню для шапки всех страниц."""
    lang = ui.ui_lang(request)
    present = set(Act.objects.values_list('jurisdiction', flat=True).distinct())
    first_segment = request.path.strip('/').split('/')[0]
    sections = [
        {
            'code': code,
            'label': strings.text(key, lang),
            'url': f'/{code}/',
            'active': code in present,
            'current': code == first_segment,
        }
        for code, key in SECTIONS
    ]
    return {
        'menu_sections': sections,
        'menu_about_current': first_segment == 'about',
    }
