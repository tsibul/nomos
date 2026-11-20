import datetime

from django.http import Http404
from django.shortcuts import redirect, render

from nomos.acts.models import Act, ActRelation, Fragment
from nomos.sources.models import Source

from . import menu, reading


def act_list(request, jurisdiction=None):
    acts = Act.objects.prefetch_related('expressions').order_by('jurisdiction', 'kind', 'uid')
    section = None
    if jurisdiction is not None:
        if jurisdiction not in menu.section_codes():
            raise Http404('Нет такого раздела')
        section = dict(menu.SECTIONS)[jurisdiction]
        acts = acts.filter(jurisdiction=jurisdiction)
    amending_ids = set(
        ActRelation.objects.filter(kind=ActRelation.Kind.AMENDS).values_list('from_act', flat=True)
    )
    base = []
    amending = []
    for act in acts:
        title, title_lang = reading.act_title_lang(act)
        entry = {
            'act': act,
            'label': reading.act_label(act),
            'short': reading.act_short_label(act, reading.DEFAULT_LANG),
            'title': title,
            'title_lang': title_lang,
            'url': reading.build_url(act),
        }
        if act.id in amending_ids:
            amending.append(entry)
        else:
            base.append(entry)
    amending.sort(key=lambda entry: (entry['act'].published_on or datetime.date.min))
    return render(
        request,
        'site/act_list.html',
        {
            'base': base,
            'amending': amending,
            'section': section,
            'sidenav_title': 'Список актов',
            'nav_groups': [
                {'title': 'Основные', 'entries': base},
                {'title': 'Изменяющие', 'entries': amending},
            ],
        },
    )


def about(request):
    sources = []
    for source in Source.objects.prefetch_related('expressions').order_by('name'):
        statuses = sorted(
            {expression.get_status_display().lower() for expression in source.expressions.all()}
        )
        sources.append(
            {
                'source': source,
                'statuses': statuses,
                'count': len(source.expressions.all()),
            }
        )
    return render(request, 'site/about.html', {'sources': sources})


def read(request, path):
    route = reading.parse_path(path)
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
    if lang not in reading.LANGS:
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
    langs = [
        {
            'code': lang,
            'url': reading.build_url(act, on=on, eid=route.eid, lang=lang),
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
    }


def nav_context(route, toc, current=None):
    """Левая колонка открытого акта: оглавление, текущая страница, путь к списку актов."""
    jurisdiction = route.act.jurisdiction
    sections = dict(menu.SECTIONS)
    if jurisdiction in sections:
        section_url = f'/{jurisdiction}/'
        section_label = sections[jurisdiction]
    else:
        section_url = '/'
        section_label = 'Акты'
    if current is not None:
        for item in reading.toc_units(toc):
            item.current = item.fragment.id == current.id
    return {
        'nav_toc': toc,
        'sidenav_title': 'Оглавление акта',
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
            'sources': text_sources(act),
        }
    )
    context.update(nav_context(route, toc))
    return render(request, 'site/act_detail.html', context)


def text_sources(act):
    """Откуда тексты акта: источник и статус каждого выражения."""
    return [
        {
            'lang': expression.lang,
            'status': expression.get_status_display(),
            'source': expression.source,
        }
        for expression in act.expressions.select_related('source').order_by('lang')
    ]


def related_entry(act, route):
    title, title_lang = reading.act_title_lang(act, route.lang)
    return {
        'label': reading.act_label(act),
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
        versions.append(
            {
                'version': version,
                'start': start,
                'introduced': introduced,
                'current': current is not None and version.id == current.id,
                'url': reading.build_url(route.act, on=read_on, eid=fragment.eid),
            }
        )
    versions.sort(
        key=lambda entry: (entry['start'] or datetime.date.min, entry['version'].known_from)
    )
    context = base_context(request, route)
    toc = reading.build_toc(structure, route)
    context.update(nav_context(route, toc, structure.unit_of(fragment)))
    context.update(
        {
            'fragment': fragment,
            'fragment_label': reading.fragment_label(fragment, current or fragment.versions.last()),
            'versions': versions,
            'fragment_url': reading.build_url(route.act, on=route.on, eid=fragment.eid),
        }
    )
    return render(request, 'site/history.html', context)


def amendment_url(amendment):
    """Вводящее положение изменяющего акта (или сам акт)."""
    eid = amendment.amending_fragment.eid if amendment.amending_fragment else None
    return reading.build_url(amendment.amending_act, eid=eid)
