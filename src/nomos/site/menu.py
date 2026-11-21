"""Главное меню: разделы по юрисдикциям; раздел активен, если в базе есть его акты."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from nomos.acts.models import Act
from nomos.site import paths

SECTIONS = [
    (Act.Jurisdiction.CY, _('Кипр')),
    (Act.Jurisdiction.EU, _('ЕС')),
]


def section_codes():
    return [code for code, _label in SECTIONS]


def section_label(code):
    """Название раздела на языке интерфейса."""
    return str(dict(SECTIONS)[code])


def menu(request):
    """Контекстный процессор: пункты меню для шапки всех страниц."""
    present = set(Act.objects.values_list('jurisdiction', flat=True).distinct())
    first_segment = request.path_info.strip('/').split('/')[0]
    sections = [
        {
            'code': code,
            'label': label,
            'url': paths.url(f'/{code}/'),
            'active': code in present,
            'current': code == first_segment,
        }
        for code, label in SECTIONS
    ]
    return {
        'menu_sections': sections,
        'menu_about_current': first_segment == 'about',
        'menu_home_url': settings.NOMOS_HOME_URL,
        'menu_home_label': settings.NOMOS_HOME_LABEL,
    }
