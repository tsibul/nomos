import datetime
import re

from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from nomos.acts.models import Act, ActRelation, Expression, Fragment
from nomos.sources.models import Source

from . import menu, notices, reading, ui


def act_list(request, jurisdiction=None):
    """Раздел (или главная): основные акты карточками, изменяющие — свёрнутыми группами."""
    acts = Act.objects.prefetch_related('expressions').order_by('jurisdiction', 'kind', 'uid')
    section = None
    if jurisdiction is not None:
        if jurisdiction not in menu.section_codes():
            raise Http404('Нет такого раздела')
        acts = acts.filter(jurisdiction=jurisdiction)
    acts = sorted(acts, key=reading.act_sort_key)
    lang = reading.interface_lang()
    if jurisdiction is not None:
        section = menu.section_label(jurisdiction)
    amending_ids = set(
        ActRelation.objects.filter(kind=ActRelation.Kind.AMENDS).values_list('from_act', flat=True)
    )
    targets = reading.amendment_targets([act for act in acts if act.id in amending_ids])
    base = []
    amending_laws = []
    regulations = []
    for act in acts:
        entry = act_entry(act, targets.get(act.id, ''), lang)
        if act.id not in amending_ids:
            base.append(entry)
        elif act.kind == Act.Kind.REGULATION_BY_LAW:
            regulations.append(entry)
        else:
            amending_laws.append(entry)
    groups = [
        {'title': _('Изменяющие законы'), 'entries': amending_laws},
        {'title': _('Подзаконные акты (ΚΔΠ)'), 'entries': regulations},
    ]
    return render(
        request,
        'site/act_list.html',
        {
            'base': base,
            'groups': [group for group in groups if group['entries']],
            'section': section,
            'is_home': jurisdiction is None,
            'has_acts': bool(acts),
        },
    )


def act_entry(act, changes='', lang=reading.READER_LANG):
    """Строка списка актов: короткое название, полное — второй строкой, что меняет.

    Ссылка — на языке интерфейса, если у акта есть этот язык, иначе на языке по умолчанию.
    """
    title, title_lang = reading.act_title_lang(act, lang)
    link_lang = lang if lang in reading.act_langs(act) else None
    return {
        'act': act,
        'label': reading.act_label(act),
        'short': reading.act_short_label(act, lang),
        'title': title,
        'title_lang': title_lang,
        'changes': changes,
        'url': reading.build_url(act, lang=link_lang),
    }


def about(request):
    sources = [
        {
            'source': source,
            'count': len(source.expressions.all()),
        }
        for source in Source.objects.prefetch_related('expressions').order_by('name')
    ]
    return render(request, 'site/about.html', {'sources': sources})


def robots(request):
    """Прототип не индексируется."""
    return HttpResponse('User-agent: *\nDisallow: /\n', content_type='text/plain')


def read(request, path):
    route = reading.parse_path(path)
    translation.activate(ui.activation_lang(route.lang))
    if route.explicit_lang and route.lang == reading.default_lang(route.act):
        # язык по умолчанию в адресе не пишется: /ru/ → адрес без языка
        url = reading.build_url(
            route.act,
            on=route.on,
            eid=route.eid,
            lang=route.lang,
            history=route.history,
        )
        if request.GET:
            url += '?' + request.GET.urlencode()
        return redirect(url, permanent=True)
    if 'on' in request.GET or 'lang' in request.GET:
        return redirect(form_target(request, route))
    if route.on is not None and route.on < reading.EARLIEST:
        raise Http404('Редакции раньше 01.01.2024 не прослеживаются')
    if route.lang not in reading.act_langs(route.act):
        raise Http404('Нет такого языка')
    structure = reading.Structure(route.act)
    if route.eid is None:
        return act_page(request, route, structure)
    fragment = structure.find(route.eid)
    if fragment is None:
        raise Http404('Фрагмент не найден')
    if route.history:
        return history_page(request, route, structure, fragment)
    unit = structure.unit_of(fragment)
    if unit.eid != route.eid:
        # постоянная ссылка фрагмента ведёт на его страницу с якорем
        url = reading.build_url(route.act, on=route.on, eid=unit.eid, lang=route.lang)
        return redirect(url + '#' + fragment.eid)
    return unit_page(request, route, structure, unit)


def form_target(request, route):
    """Форма даты и языка (GET) → постоянный адрес страницы."""
    on = route.on
    text = request.GET.get('on', '').strip()
    if text:
        on = reading.parse_date(text) or on
    if on is not None and on < reading.EARLIEST:
        on = reading.EARLIEST
    if on == datetime.date.today():
        on = None
    lang = request.GET.get('lang', route.lang)
    if lang not in reading.act_langs(route.act):
        lang = route.lang
    url = reading.build_url(
        route.act,
        on=on,
        eid=route.eid,
        lang=lang,
        history=route.history,
    )
    if request.GET.get('orig'):
        url += '?orig=1'
    return url


def base_context(request, route):
    act = route.act
    on = route.on
    # переключатель ведёт на ту же страницу, дату и режим «оригинал рядом»;
    # якорь добавляет JS (data-keep-hash)
    kept = {key: request.GET[key] for key in ('orig', 'from', 'at') if request.GET.get(key)}
    query = '?' + urlencode(kept) if kept else ''
    langs = [
        {
            'code': lang,
            'url': reading.build_url(
                act,
                on=on,
                eid=route.eid,
                lang=lang,
                history=route.history,
            )
            + query,
            'current': lang == route.lang,
        }
        for lang in reading.act_langs(act)
    ]
    title, title_lang = reading.act_title_lang(act, route.lang)
    return {
        'act': act,
        'route': route,
        'label': reading.act_label(act),
        'title': title,
        'title_lang': title_lang,
        'structure_lang': act.structure_lang or reading.DEFAULT_LANG,
        'act_url': reading.build_url(act, on=on, lang=route.lang),
        'today_url': reading.build_url(act, eid=route.eid, lang=route.lang),
        'langs': langs,
        'date': route.date,
        'is_today': route.on is None or route.on == datetime.date.today(),
        'earliest': reading.EARLIEST,
        'show_original': bool(request.GET.get('orig')),
        'back': back_link(request, route.lang),
        'source_credit': source_credit(act),
    }


def source_credit(act):
    """Подпись об источнике текста — у акта, текст которого взят из сводной редакции CyLaw."""
    if act.expressions.filter(source__code='cylaw').exists():
        return notices.CYLAW_CREDIT
    return None


BACK_AT_RE = re.compile(r'^[\w.-]+$')


def back_link(request, lang):
    """«← Назад к статье 2»: возврат на страницу, откуда пришли по ссылке (`?from=&at=`).

    Адрес возврата собирается заново из разобранного `from` — только страницы актов сайта.
    Язык сменили на странице цели — возврат на том же языке, если он есть у исходного акта,
    к пункту ссылки: якоря ссылок (`r1`, `r2`…) в каждом языке свои.
    """
    origin = request.GET.get('from', '')
    if not origin.startswith('/') or origin.startswith('//'):
        return None
    try:
        route = reading.parse_path(origin)
    except Http404:
        return None
    at = request.GET.get('at', '')
    if not BACK_AT_RE.match(at):
        at = ''
    if lang != route.lang and lang in reading.act_langs(route.act):
        route.lang = lang
        at = fragment_of_anchor(at)
    url = reading.page_url(route)
    if at:
        url += '#' + at
    return {
        'url': url,
        'label': back_label(route.eid),
    }


def fragment_of_anchor(at):
    """`ref-art_5.par_5-r1` → `art_5.par_5`: пункт, в котором стоит ссылка."""
    if not at.startswith('ref-'):
        return at
    eid, _sep, _anchor = at.removeprefix('ref-').rpartition('-')
    return eid


def back_label(eid):
    """«← Назад к статье 2», иначе «← Назад к тексту»."""
    kind, _sep, number = (eid or '').split('.')[0].partition('_')
    if kind == 'art' and number:
        return _('← Назад к статье %(number)s') % {'number': number}
    return _('← Назад к тексту')


def nav_context(route, toc, current=None):
    """Левая колонка открытого акта: оглавление, текущая страница, путь к списку актов."""
    jurisdiction = route.act.jurisdiction
    sections = dict(menu.SECTIONS)
    query = ui.lang_query(route.lang)
    if jurisdiction in sections:
        section_url = f'/{jurisdiction}/{query}'
        section_label = menu.section_label(jurisdiction)
    else:
        section_url = '/' + query
        section_label = _('Акты')
    if current is not None:
        for item in reading.toc_units(toc):
            item.current = item.fragment.id == current.id
    return {
        'nav_toc': toc,
        'sidenav_title': _('Оглавление акта'),
        'section_url': section_url,
        'section_label': section_label,
    }


def act_page(request, route, structure):
    act = route.act
    toc = reading.build_toc(structure, route)
    amended_by = [
        relation.from_act
        for relation in act.relations_in.filter(kind=ActRelation.Kind.AMENDS)
        .select_related('from_act')
        .order_by('from_act__published_on', 'from_act__uid')
    ]
    amends = [
        relation.to_act
        for relation in act.relations_out.filter(kind=ActRelation.Kind.AMENDS)
        .select_related('to_act')
    ]
    context = base_context(request, route)
    context.update(
        {
            'toc': toc,
            'amended_by': [related_entry(other, route) for other in amended_by],
            'amends': [related_entry(other, route) for other in amends],
            'short': reading.act_short_label(act, route.lang),
            'sources': text_sources(act),
        }
    )
    context.update(nav_context(route, toc))
    context['sidenav_wide_only'] = True
    # на странице самого акта его метка в шапке — не ссылка на себя
    context['act_head_current'] = True
    return render(request, 'site/act_detail.html', context)


# статусы текстов на языке интерфейса (в модели Expression.Status — русские названия)
STATUS_NAMES = {
    Expression.Status.OFFICIAL_TEXT: gettext_lazy('Официальный текст'),
    Expression.Status.OFFICIAL_TRANSLATION: gettext_lazy('Официальный перевод'),
    Expression.Status.UNOFFICIAL_TRANSLATION: gettext_lazy('Неофициальный перевод'),
    Expression.Status.OUR_TRANSLATION: gettext_lazy('Наш перевод'),
}


def text_sources(act):
    """Откуда тексты акта: источник и статус каждого выражения."""
    return [
        {
            'lang': expression.lang,
            'status': STATUS_NAMES.get(expression.status) or expression.get_status_display(),
            'source': expression.source,
        }
        for expression in act.expressions.select_related('source').order_by('lang')
    ]


def related_entry(act, route):
    title, title_lang = reading.act_title_lang(act, route.lang)
    return {
        'label': reading.act_label(act),
        'short': reading.act_short_label(act, route.lang),
        'title': title,
        'title_lang': title_lang,
        'url': reading.build_url(act),
    }


def unit_page(request, route, structure, unit):
    root = reading.build_nodes(structure, unit, route)
    if root is None:
        raise Http404('Фрагмента нет на эту дату')
    toc = reading.build_toc(structure, route)
    units = reading.toc_units(toc)
    eids = [item.fragment.eid for item in units]
    previous_unit = None
    next_unit = None
    if unit.eid in eids:
        position = eids.index(unit.eid)
        if position > 0:
            previous_unit = units[position - 1]
        if position + 1 < len(units):
            next_unit = units[position + 1]
    toc_items = reading.toc_items_by_id(toc)
    crumbs = [
        toc_label(toc_items, ancestor, ancestor.version_on(route.date))
        for ancestor in structure.ancestors(unit)
    ]
    context = base_context(request, route)
    context.update(
        {
            'unit': unit,
            'root': root,
            'unit_label': toc_label(toc_items, unit, root.version),
            'crumbs': crumbs,
            'previous': unit_link(previous_unit, route),
            'next': unit_link(next_unit, route),
            'future_dates': future_dates(root),
            'untranslated': (
                route.lang != reading.DEFAULT_LANG and not reading.has_translation(root)
            ),
            'translation': translation_expression(route),
        }
    )
    context.update(nav_context(route, toc, unit))
    return render(request, 'site/unit.html', context)


def toc_label(toc_items, fragment, version):
    """Подпись из оглавления (там уже переведённые заголовки), иначе — опорная."""
    item = toc_items.get(fragment.id)
    if item is not None and item.version == version:
        return item.label
    return reading.fragment_label(fragment, version)


def translation_expression(route):
    """Выражение перевода страницы — для строки «Источник перевода»."""
    if route.lang == reading.DEFAULT_LANG:
        return None
    return route.act.expressions.select_related('source').filter(lang=route.lang).first()


def unit_link(item, route):
    if item is None:
        return None
    return {
        'label': item.label,
        'url': item.url,
    }


def future_dates(root):
    """Даты принятых будущих редакций на странице — ссылки «будущая редакция»."""
    dates = set()

    def walk(node):
        if node.future is not None:
            dates.add(node.future.start(Fragment.Axis.APPLIES))
        for child in node.children:
            walk(child)

    walk(root)
    return sorted(dates)


def history_page(request, route, structure, fragment):
    current = fragment.version_on(route.date)
    in_force = fragment.version_on(datetime.date.today())
    unit = structure.unit_of(fragment) or fragment
    anchor = '' if unit.id == fragment.id else '#' + fragment.eid
    versions = []
    for version in fragment.versions.select_related(
        'amendment__amending_act',
        'amendment__amending_fragment',
    ):
        amendment = version.amendment
        start = version.start(Fragment.Axis.APPLIES)
        introduced = None
        if amendment.kind != amendment.Kind.BASE:
            label = reading.act_label(amendment.amending_act)
            if amendment.amending_fragment is not None:
                label += ', ' + amendment.amending_fragment.eid
            introduced = {
                'label': label,
                'url': amendment_url(amendment),
                'note': amendment.note,
            }
        read_on = start if start is not None and start >= reading.EARLIEST else None
        if in_force is not None and version.id == in_force.id:
            # действующая сегодня редакция — адрес без даты
            read_on = None
        versions.append(
            {
                'version': version,
                'start': start,
                'introduced': introduced,
                'current': current is not None and version.id == current.id,
                'url': reading.build_url(
                    route.act,
                    on=read_on,
                    eid=unit.eid,
                    lang=route.lang,
                )
                + anchor,
            }
        )
    versions.sort(
        key=lambda entry: (entry['start'] or datetime.date.min, entry['version'].known_from)
    )
    context = base_context(request, route)
    toc = reading.build_toc(structure, route)
    context.update(nav_context(route, toc, unit))
    context.update(
        {
            'fragment': fragment,
            'fragment_label': history_label(structure, fragment, route),
            'has_changes': any(entry['introduced'] for entry in versions),
            'versions': versions,
            # путь назад: к действующему тексту того же пункта на том же языке
            'current_url': reading.build_url(route.act, eid=unit.eid, lang=route.lang) + anchor,
            # и к редакции на дату, с которой пришли в историю
            'dated_url': (
                reading.build_url(route.act, on=route.on, eid=unit.eid, lang=route.lang) + anchor
                if route.on is not None
                else ''
            ),
        }
    )
    return render(request, 'site/history.html', context)


def history_label(structure, fragment, route):
    """«Статья 5. Заголовок перевода» (без перевода — «Статья 5»), у пункта — «Статья 5, 1»."""
    unit = structure.unit_of(fragment) or fragment
    version = unit.version_on(route.date) or unit.versions.last()
    headings = reading.translated_headings([version], route.lang)
    label = reading.fragment_label(unit, version, headings, route.lang)
    prefix = reading.KIND_PREFIX.get(route.lang, {}).get(unit.kind)
    if label.lang != route.lang and prefix and version is not None and version.num:
        label = reading.Label(f'{prefix} {version.num}', route.lang)
    if fragment.id != unit.id:
        own = fragment.version_on(route.date) or fragment.versions.last()
        if own is not None and own.num:
            label = reading.Label(f'{label.text}, {own.num}', label.lang)
    return label


def amendment_url(amendment):
    """Вводящее положение изменяющего акта (или сам акт)."""
    eid = amendment.amending_fragment.eid if amendment.amending_fragment else None
    return reading.build_url(amendment.amending_act, eid=eid)
