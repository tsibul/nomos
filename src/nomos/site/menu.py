"""Главное меню: разделы по юрисдикциям; раздел активен, если в базе есть его акты."""

from nomos.acts.models import Act

SECTIONS = [
    (Act.Jurisdiction.CY, 'Кипр'),
    (Act.Jurisdiction.EU, 'ЕС'),
]


def section_codes():
    return [code for code, _label in SECTIONS]


def menu(request):
    """Контекстный процессор: пункты меню для шапки всех страниц."""
    present = set(Act.objects.values_list('jurisdiction', flat=True).distinct())
    first_segment = request.path.strip('/').split('/')[0]
    sections = [
        {
            'code': code,
            'label': label,
            'url': f'/{code}/',
            'active': code in present,
            'current': code == first_segment,
        }
        for code, label in SECTIONS
    ]
    return {
        'menu_sections': sections,
        'menu_about_current': first_segment == 'about',
    }
