import datetime
import re

from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, gettext_noop

from nomos.acts import grouping
from nomos.acts.models import (
    Act,
    ActRelation,
    Expression,
    Fragment,
    FragmentVersion,
    Reference,
)
from nomos.sources.models import Source

from . import access, menu, notices, paths, reading, ui


def act_list(request, jurisdiction=None, subsection=None):
    """Главная или раздел меню: пункты основных актов, у каждого — его акты по
    связям."""
    tree = menu.Tree()
    keys = [key for key in (jurisdiction, subsection) if key]
    node = None
    if keys:
        node = tree.find(keys)
        if node is None:
            raise Http404("Нет такого раздела")
    lang = reading.interface_lang()
    relations = grouping.Relations()
    main_ids = grouping.main_act_ids()
    groups = []
    for group in section_groups(tree, node):
        items = [
            nav_item(item, relations, main_ids, lang)
            for item in tree.kids(group)
        ]
        items = [item for item in items if item is not None]
        if items:
            groups.append(
                {
                    "title": group_title(group, node, tree),
                    "url": menu.section_url(group),
                    "items": items,
                }
            )
    entries = [
        item["entry"]
        for group in groups
        for item in group["items"]
        if item["entry"]
    ]
    return render(
        request,
        "site/act_list.html",
        {
            "groups": groups,
            "first": entries[0] if entries else None,
            "section": menu.label(node) if node is not None else None,
            "is_home": node is None,
            "has_acts": bool(groups),
            "disclaimer": notices.disclaimer(keys[0] if keys else None),
        },
    )


def section_groups(tree, node):
    """Разделы с пунктами актов внутри node (и сам node), по порядку меню."""
    found = []
    kids = tree.kids(node)
    if node is not None and any(not kid.is_section for kid in kids):
        found.append(node)
    for kid in kids:
        if kid.is_section:
            found.extend(section_groups(tree, kid))
    return found


def group_title(group, node, tree):
    """Заголовок группы от открытого раздела вниз: на главной «Кипр: Налоги», в
    разделе Кипр —
    «Налоги»; группа — сам открытый раздел — без заголовка."""
    labels = []
    while group is not None and group != node:
        labels.append(menu.label(group))
        group = group.parent
    return ": ".join(reversed(labels))


def nav_item(item, relations, main_ids, lang):
    """Пункт меню: карточка акта с его блоками; нет акта — «не загружен» (без
    входа — ничего)."""
    act = item.act
    if access.is_restricted() and (act is None or not access.act_visible(act)):
        return None
    if act is None:
        return {"entry": None, "label": menu.label(item) or item.act_uid}
    entry = act_entry(act, "", lang)
    entry["nav_label"] = menu.label(item)
    return {
        "entry": entry,
        "blocks": act_blocks(act, relations, main_ids, lang),
    }


# заголовки блоков у основного акта (docs/design/site-navigation.md, раздел 2)
BLOCK_TITLES = {
    grouping.SUBSIDIARY: gettext_lazy("Подзаконные акты"),
    grouping.FORMS: gettext_lazy("Формы и руководства"),
    grouping.EU: gettext_lazy("Связанные акты ЕС"),
    grouping.OTHER: gettext_lazy("Прочее"),
}


def block_title(block, act):
    if block != grouping.AMENDING:
        return BLOCK_TITLES[block]
    if act.jurisdiction == Act.Jurisdiction.CY:
        return _("Изменяющие законы")
    return _("Изменяющие акты")


def act_blocks(act, relations, main_ids, lang):
    """Непустые блоки актов у акта: [{'title', 'entries'}] по порядку
    проекта."""
    found = relations.blocks(act, main_ids)
    visible = {
        block: [other for other in acts if access.act_visible(other)]
        for block, acts in found.items()
    }
    targets = reading.amendment_targets(visible.get(grouping.AMENDING, []))
    return [
        {
            "title": block_title(block, act),
            "entries": [
                act_entry(other, targets.get(other.id, ""), lang)
                for other in visible[block]
            ],
        }
        for block in grouping.BLOCKS
        if visible.get(block)
    ]


def act_entry(act, changes="", lang=reading.READER_LANG):
    """Строка списка актов: короткое название, полное — второй строкой, что
    меняет.

    Ссылка — на языке интерфейса, если у акта есть этот язык, иначе на языке по
    умолчанию.
    """
    title, title_lang = reading.act_title_lang(act, lang)
    link_lang = lang if lang in reading.act_langs(act) else None
    return {
        "act": act,
        "label": reading.act_label(act),
        "short": reading.act_short_label(act, lang),
        "title": title,
        "title_lang": title_lang,
        "changes": changes,
        "url": reading.build_url(act, lang=link_lang),
    }


def about(request):
    sources = [
        {
            "source": source,
            "name": source_text(source.name),
            "count": len(
                [
                    expression
                    for expression in source.expressions.all()
                    if expression.public or not access.is_restricted()
                ]
            ),
        }
        for source in Source.objects.prefetch_related("expressions").order_by(
            "name"
        )
    ]
    context = {
        "sources": sources,
        # ссылка внутри переводимой фразы: в каталоге — только место {{ link }}
        "cylaw_link": format_html(
            '<a href="{}" rel="external">{}</a>',
            "https://www.cylaw.org",
            "www.cylaw.org",
        ),
    }
    return render(request, "site/about.html", context)


def robots(request):
    """Прототип не индексируется."""
    return HttpResponse(
        f"User-agent: *\nDisallow: {paths.url('/')}\n",
        content_type="text/plain",
    )


def read(request, path):
    route = reading.parse_path(path)
    if not access.act_visible(route.act):
        raise Http404("Акт не найден")
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
            url += "?" + request.GET.urlencode()
        return redirect(url, permanent=True)
    if "on" in request.GET or "lang" in request.GET:
        return redirect(form_target(request, route))
    if route.on is not None and route.on < reading.EARLIEST:
        raise Http404("Редакции раньше 01.01.2024 не прослеживаются")
    if route.lang not in reading.act_langs(route.act):
        raise Http404("Нет такого языка")
    route.chain = chain_params(request)
    structure = reading.Structure(route.act)
    if route.eid is None:
        return act_page(request, route, structure)
    fragment = structure.find(route.eid)
    if fragment is None:
        raise Http404("Фрагмент не найден")
    if route.history:
        return history_page(request, route, structure, fragment)
    unit = structure.unit_of(fragment)
    anchor = "#" + fragment.eid
    if unit is None:
        # у раздела своей страницы нет: адрес раздела ведёт на первую страницу
        # в нём
        unit = structure.first_unit(fragment)
        anchor = ""
        if unit is None:
            raise Http404("В разделе нет страниц")
    if unit.eid != route.eid:
        # постоянная ссылка фрагмента ведёт на его страницу с якорем
        # строка запроса сохраняется: from/at — путь «← Назад» к месту ссылки,
        # orig — режим
        url = reading.build_url(
            route.act, on=route.on, eid=unit.eid, lang=route.lang
        )
        if request.GET:
            url += "?" + request.GET.urlencode()
        return redirect(url + anchor)
    return unit_page(request, route, structure, unit)


def form_target(request, route):
    """Форма даты и языка (GET) → постоянный адрес страницы."""
    on = route.on
    text = request.GET.get("on", "").strip()
    if text:
        on = reading.parse_date(text) or on
    if on is not None and on < reading.EARLIEST:
        on = reading.EARLIEST
    if on == datetime.date.today():
        on = None
    lang = request.GET.get("lang", route.lang)
    if lang not in reading.act_langs(route.act):
        lang = route.lang
    url = reading.build_url(
        route.act,
        on=on,
        eid=route.eid,
        lang=lang,
        history=route.history,
    )
    # режим «оригинал рядом» и начало цепочки ссылок переходят на новую
    # страницу
    keys = (*original_keys(), *CHAIN_KEYS)
    kept = {key: request.GET[key] for key in keys if request.GET.get(key)}
    if kept:
        url += "?" + urlencode(kept)
    return url


def base_context(request, route):
    act = route.act
    on = route.on
    # переключатель ведёт на ту же страницу, дату и режим «оригинал рядом»;
    # якорь добавляет JS (data-keep-hash)
    keys = (*original_keys(), "from", "at", *CHAIN_KEYS)
    kept = {key: request.GET[key] for key in keys if request.GET.get(key)}
    query = "?" + urlencode(kept) if kept else ""
    langs = [
        {
            "code": lang,
            "url": reading.build_url(
                act,
                on=on,
                eid=route.eid,
                lang=lang,
                history=route.history,
            )
            + query,
            "current": lang == route.lang,
        }
        for lang in reading.act_langs(act)
    ]
    title, title_lang = reading.act_title_lang(act, route.lang)
    return {
        "act": act,
        "route": route,
        "label": reading.act_label(act),
        "title": title,
        "title_lang": title_lang,
        "structure_lang": act.structure_lang or reading.DEFAULT_LANG,
        "act_url": reading.build_url(act, on=on, lang=route.lang),
        "today_url": reading.build_url(act, eid=route.eid, lang=route.lang),
        "langs": langs,
        "date": route.date,
        "is_today": route.on is None or route.on == datetime.date.today(),
        "earliest": reading.EARLIEST,
        "show_original": show_original(request, route),
        "original_source": original_source(route),
        "back": back_link(request, route.lang, route.chain),
        "chain_start": chain_start_link(route),
        "source_credit": source_credit(act),
        "disclaimer": notices.disclaimer(act.jurisdiction),
    }


def original_keys():
    """Параметр режима «оригинал рядом» — если режим доступен посетителю."""
    return () if access.is_restricted() else ("orig",)


def show_original(request, route):
    """Режим «греческий рядом»: только если оригинал виден посетителю."""
    if not request.GET.get("orig"):
        return False
    return access.lang_visible(
        route.act, route.act.structure_lang or reading.DEFAULT_LANG
    )


def original_source(route):
    """Источник скрытого оригинала — для ссылки «Оригинал: <источник>»; виден —
    None.

    Адреса статьи у источника в данных нет: ссылка ведёт на адрес акта у
    источника
    (выражения, затем ELI), а если он неизвестен — на адрес источника.
    """
    lang = route.act.structure_lang or reading.DEFAULT_LANG
    if route.lang == lang or access.lang_visible(route.act, lang):
        return None
    expression = (
        route.act.expressions.select_related("source")
        .filter(lang=lang)
        .first()
    )
    if expression is None:
        return None
    return {
        "name": source_text(expression.source.name),
        "url": expression.url or route.act.eli or expression.source.url,
    }


def source_credit(act):
    """Подпись об источнике текста — у акта, текст которого взят из сводной
    редакции CyLaw."""
    if act.expressions.filter(source__code="cylaw").exists():
        return notices.CYLAW_CREDIT
    return None


# подпись ссылки «Источник:» у актов из сводной редакции CyLaw
CYLAW_NAME = "CyLaw (cylaw.org)"


def cylaw_source(act):
    """Строка «Источник: CyLaw (cylaw.org)» у акта из сводной редакции CyLaw:
    ссылка — адрес акта у CyLaw, если он есть, иначе адрес CyLaw; не CyLaw —
    None."""
    expression = (
        act.expressions.select_related("source")
        .filter(source__code="cylaw")
        .first()
    )
    if expression is None:
        return None
    return {
        "name": CYLAW_NAME,
        "url": expression.url or expression.source.url,
    }


BACK_AT_RE = re.compile(r"^[\w.-]+$")

# параметры начала цепочки переходов по ссылкам: страница и место первой ссылки
CHAIN_KEYS = ("start", "start_at")


def return_target(path, at, lang):
    """Страница возврата из параметра адреса и место на ней: (route, at) или
    None.

    Адрес собирается заново из разобранного пути — только страницы актов сайта.
    Язык сменили на странице цели — возврат на том же языке, если он есть у
    исходного акта:
    к месту ссылки, если такая ссылка есть и в тексте этого языка, иначе к её
    пункту.
    """
    if not path.startswith("/") or path.startswith("//"):
        return None
    path = paths.local(path)
    if path is None:
        return None
    try:
        route = reading.parse_path(path)
    except Http404:
        return None
    if not BACK_AT_RE.match(at):
        at = ""
    if lang != route.lang and lang in reading.act_langs(route.act):
        route.lang = lang
        if not anchor_in_lang(route.act, at, lang):
            at = fragment_of_anchor(at)
    return route, at


def anchor_in_lang(act, at, lang):
    """Есть ли место ссылки `ref-<пункт>-<якорь>` в тексте акта на языке lang.

    id у ссылки ставится, только когда её цель найдена (`reading.render_body`).
    """
    if not at.startswith("ref-"):
        return False
    eid, _sep, anchor = at.removeprefix("ref-").rpartition("-")
    return Reference.objects.filter(
        from_text__version__fragment__act=act,
        from_text__version__fragment__eid=eid,
        from_text__expression__lang=lang,
        anchor=anchor,
        resolved=True,
    ).exists()


def chain_target(request, lang):
    """Начало цепочки ссылок: `start`/`start_at`; на первом шаге — страница
    `from`/`at`."""
    if request.GET.get("start"):
        return return_target(
            request.GET["start"], request.GET.get("start_at", ""), lang
        )
    return return_target(
        request.GET.get("from", ""), request.GET.get("at", ""), lang
    )


def chain_params(request):
    """Начало цепочки для адресов страницы (`Route.chain`): {'start': …,
    'start_at': …}.

    Передаётся как пришло, без смены языка: иначе после смены языка якорь
    ссылки
    (`ref-art_9.par_1-r1`) усекается до пункта и при возврате на исходный язык
    не восстановим.
    Язык учитывает только сама ссылка «К началу» (`chain_start_link`).
    """
    target = chain_target(request, None)
    if target is None:
        return {}
    route, at = target
    params = {"start": reading.page_url(route)}
    if at:
        params["start_at"] = at
    return params


def chain_start_link(route):
    """«К началу: статья 2» — к месту первой ссылки цепочки; виден на любом её
    шаге."""
    if not route.chain:
        return None
    target = return_target(
        route.chain["start"], route.chain.get("start_at", ""), route.lang
    )
    if target is None:
        return None
    start, at = target
    url = reading.page_url(start)
    if at:
        url += "#" + at
    return {
        "url": url,
        "label": chain_start_label(start.eid),
    }


def chain_start_label(eid):
    """«К началу: статья 2», иначе «К началу: текст акта»."""
    kind, _sep, number = (eid or "").split(".")[0].partition("_")
    if kind == "art" and number:
        return _("К началу: статья %(number)s") % {"number": number}
    return _("К началу: текст акта")


def back_link(request, lang, chain=None):
    """«← Назад к статье 2»: на шаг назад, на страницу, откуда пришли по ссылке
    (`?from=&at=`).

    Возврат несёт начало цепочки, если это не оно само: «К началу» остаётся на
    экране.
    """
    target = return_target(
        request.GET.get("from", ""), request.GET.get("at", ""), lang
    )
    if target is None:
        return None
    back, at = target
    url = reading.page_url(back)
    if chain and chain["start"] != url:
        url += "?" + urlencode(chain)
    if at:
        url += "#" + at
    return {
        "url": url,
        "label": back_label(back.eid),
    }


def fragment_of_anchor(at):
    """`ref-art_5.par_5-r1` → `art_5.par_5`: пункт, в котором стоит ссылка."""
    if not at.startswith("ref-"):
        return at
    eid, _sep, _anchor = at.removeprefix("ref-").rpartition("-")
    return eid


def back_label(eid):
    """«← Назад к статье 2», иначе «← Назад к тексту»."""
    kind, _sep, number = (eid or "").split(".")[0].partition("_")
    if kind == "art" and number:
        return _("← Назад к статье %(number)s") % {"number": number}
    return _("← Назад к тексту")


def nav_context(route, toc, current=None):
    """Левая колонка открытого акта: оглавление, текущая страница, путь к
    разделу меню.

    Раздел — тот, где стоит пункт акта; акт не основной — раздел его
    юрисдикции.
    """
    tree = menu.Tree()
    item = tree.item_of(route.act)
    section = (
        item.parent
        if item is not None
        else tree.find([route.act.jurisdiction])
    )
    query = ui.lang_query(route.lang)
    if section is not None:
        section_url = menu.section_url(section) + query
        section_label = menu.label(section)
    else:
        section_url = paths.url("/") + query
        section_label = _("Акты")
    if current is not None:
        for item in reading.toc_units(toc):
            item.current = item.fragment.id == current.id
    return {
        "nav_toc": toc,
        "sidenav_title": _("Оглавление акта"),
        "section_url": section_url,
        "section_label": section_label,
    }


def act_page(request, route, structure):
    act = route.act
    toc = reading.build_toc(structure, route)
    amends = [
        relation.to_act
        for relation in act.relations_out.filter(
            kind=ActRelation.Kind.AMENDS
        ).select_related("to_act")
    ]
    context = base_context(request, route)
    context.update(
        {
            "toc": toc,
            "blocks": act_blocks(
                act,
                grouping.Relations(),
                grouping.main_act_ids(),
                route.lang,
            ),
            "amends": [
                related_entry(other, route)
                for other in amends
                if access.act_visible(other)
            ],
            "short": reading.act_short_label(act, route.lang),
            "sources": text_sources(act),
            "cylaw_source": cylaw_source(act),
        }
    )
    context.update(nav_context(route, toc))
    context["sidenav_wide_only"] = True
    # на странице самого акта его метка в шапке — не ссылка на себя
    context["act_head_current"] = True
    return render(request, "site/act_detail.html", context)


# статусы текстов на языке интерфейса (в модели Expression.Status — русские
# названия)
STATUS_NAMES = {
    Expression.Status.OFFICIAL_TEXT: gettext_lazy("Официальный текст"),
    Expression.Status.OFFICIAL_TRANSLATION: gettext_lazy(
        "Официальный перевод"
    ),
    Expression.Status.UNOFFICIAL_TRANSLATION: gettext_lazy(
        "Неофициальный перевод"
    ),
    Expression.Status.OUR_TRANSLATION: gettext_lazy("Наш перевод"),
}


# названия и указания источников хранятся в данных по-русски
# (load_texts.SOURCES) и
# переводятся как msgid; список — чтобы makemessages их нашёл
SOURCE_TEXTS = (
    gettext_noop("CyLaw — неофициальная сводная редакция"),
    gettext_noop(
        "Сводная редакция CyLaw (www.cylaw.org, ΚΙΝΟΠ), неофициальная"
    ),
    gettext_noop("Официальная газета Республики Кипр"),
    gettext_noop("Неофициальный перевод"),
    gettext_noop("Неофициальный перевод проекта Nomos, CC BY 4.0"),
)


def source_text(text):
    """Строка источника из данных на языке интерфейса; пустая — пустой (не
    заголовок каталога)."""
    return _(text) if text else ""


def text_sources(act):
    """Откуда тексты акта: источник и статус каждого выражения, на языке
    интерфейса."""
    return [
        {
            "lang": expression.lang,
            "status": STATUS_NAMES.get(expression.status)
            or expression.get_status_display(),
            "source": expression.source,
            "name": source_text(expression.source.name),
            "attribution": source_text(expression.source.attribution),
        }
        for expression in access.expression_queryset(
            act.expressions.select_related("source").order_by("lang")
        )
    ]


def related_entry(act, route):
    title, title_lang = reading.act_title_lang(act, route.lang)
    return {
        "label": reading.act_label(act),
        "short": reading.act_short_label(act, route.lang),
        "title": title,
        "title_lang": title_lang,
        "url": reading.build_url(act),
    }


def unit_page(request, route, structure, unit):
    root = reading.build_nodes(structure, unit, route)
    if root is None:
        repeal = unit.repeal_on(route.date)
        if repeal is None:
            raise Http404("Фрагмента нет на эту дату")
        return repealed_page(request, route, structure, unit, *repeal)
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
            "unit": unit,
            "root": root,
            "unit_label": toc_label(toc_items, unit, root.version),
            "crumbs": crumbs,
            "previous": unit_link(previous_unit, route),
            "next": unit_link(next_unit, route),
            "future_dates": future_dates(root),
            "untranslated": (
                route.lang != reading.original_lang(route.act)
                and not reading.has_translation(root)
            ),
            "translation": translation_expression(route),
        }
    )
    context.update(nav_context(route, toc, unit))
    return render(request, "site/unit.html", context)


def repealed_page(request, route, structure, unit, repealing, last):
    """Страница исключённого на дату положения: с какой даты, каким актом,
    прежняя редакция.

    Ответ 200: адрес законный — на другие даты по нему есть текст, есть история
    редакций.
    """
    since = repealing.start(Fragment.Axis.APPLIES)
    last_url = ""
    if last is not None and since is not None and since > reading.EARLIEST:
        # последний день действия прежней редакции; раньше 01.01.2024 редакции
        # не прослеживаются
        last_url = (
            reading.build_url(
                route.act,
                on=since - datetime.timedelta(days=1),
                eid=unit.eid,
                lang=route.lang,
            )
            + route.chain_query
        )
    amendment = repealing.amendment
    toc = reading.build_toc(structure, route)
    context = base_context(request, route)
    context.update(nav_context(route, toc, unit))
    context.update(
        {
            "unit": unit,
            "unit_label": history_label(
                structure, unit, route, last or repealing
            ),
            "since": since,
            "repealed_by": (
                introduced_by(amendment)
                if amendment.kind != amendment.Kind.BASE
                else None
            ),
            "last_url": last_url,
            "last_until": since - datetime.timedelta(days=1)
            if last_url
            else None,
            "history_url": (
                reading.build_url(
                    route.act, eid=unit.eid, lang=route.lang, history=True
                )
                + route.chain_query
            ),
        }
    )
    return render(request, "site/repealed.html", context)


def toc_label(toc_items, fragment, version):
    """Подпись из оглавления (там уже переведённые заголовки), иначе —
    опорная."""
    item = toc_items.get(fragment.id)
    if item is not None and item.version == version:
        return item.label
    return reading.fragment_label(fragment, version)


def translation_expression(route):
    """Выражение перевода страницы — для строки «Источник перевода»."""
    if route.lang == reading.original_lang(route.act):
        return None
    expressions = route.act.expressions.select_related("source").filter(
        lang=route.lang
    )
    return access.expression_queryset(expressions).first()


def unit_link(item, route):
    if item is None:
        return None
    return {
        "label": item.label,
        "url": item.url,
    }


def future_dates(root):
    """Даты принятых будущих редакций на странице — ссылки «будущая
    редакция»."""
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
    anchor = "" if unit.id == fragment.id else "#" + fragment.eid
    versions = []
    for version in fragment.versions.select_related(
        "amendment__amending_act",
        "amendment__amending_fragment",
    ):
        amendment = version.amendment
        start = version.start(Fragment.Axis.APPLIES)
        introduced = None
        if amendment.kind != amendment.Kind.BASE:
            introduced = introduced_by(amendment)
        read_on = (
            start if start is not None and start >= reading.EARLIEST else None
        )
        if in_force is not None and version.id == in_force.id:
            # действующая сегодня редакция — адрес без даты
            read_on = None
        versions.append(
            {
                "version": version,
                "start": start,
                "introduced": introduced,
                "current": current is not None and version.id == current.id,
                "url": reading.build_url(
                    route.act,
                    on=read_on,
                    eid=unit.eid,
                    lang=route.lang,
                )
                + route.chain_query
                + anchor,
            }
        )
    versions.sort(
        key=lambda entry: (
            entry["start"] or datetime.date.min,
            entry["version"].known_from,
        )
    )
    nested = nested_changes(structure, fragment, route)
    context = base_context(request, route)
    toc = reading.build_toc(structure, route)
    context.update(nav_context(route, toc, unit))
    context.update(
        {
            "fragment": fragment,
            "fragment_label": history_label(structure, fragment, route),
            "has_changes": bool(nested)
            or any(entry["introduced"] for entry in versions),
            "versions": versions,
            "nested": nested,
            # путь назад: к действующему тексту того же пункта на том же языке
            "current_url": (
                reading.build_url(route.act, eid=unit.eid, lang=route.lang)
                + route.chain_query
                + anchor
            ),
            # и к редакции на дату, с которой пришли в историю
            "dated_url": (
                reading.build_url(
                    route.act, on=route.on, eid=unit.eid, lang=route.lang
                )
                + route.chain_query
                + anchor
                if route.on is not None
                else ""
            ),
        }
    )
    return render(request, "site/history.html", context)


def descendants(structure, fragment):
    """Все вложенные фрагменты по порядку текста."""
    found = []
    for kid in structure.kids(fragment):
        found.append(kid)
        found.extend(descendants(structure, kid))
    return found


def nested_changes(structure, fragment, route):
    """Изменения вложенных пунктов и подпунктов: дата, пункт, изменяющий акт,
    ссылки.

    Базовый текст изменением не считается. Номер пункта — путь номеров от
    фрагмента
    страницы истории вниз («(3)(α)»); без номера — последняя часть eid.
    """
    nested = descendants(structure, fragment)
    if not nested:
        return []
    by_fragment = {}
    for version in FragmentVersion.objects.filter(
        fragment__in=nested
    ).select_related(
        "amendment__amending_act",
        "amendment__amending_fragment",
    ):
        by_fragment.setdefault(version.fragment_id, []).append(version)
    unit = structure.unit_of(fragment) or fragment
    in_force = {}
    changes = []
    for kid in nested:
        for version in by_fragment.get(kid.id, []):
            amendment = version.amendment
            if amendment.kind == amendment.Kind.BASE:
                continue
            start = version.start(Fragment.Axis.APPLIES)
            if kid.id not in in_force:
                in_force[kid.id] = kid.version_on(datetime.date.today())
            current = in_force[kid.id]
            read_on = (
                start
                if start is not None and start >= reading.EARLIEST
                else None
            )
            if current is not None and version.id == current.id:
                read_on = None
            label = reading.act_label(amendment.amending_act)
            if amendment.amending_fragment is not None:
                label += ", " + amendment.amending_fragment.eid
            changes.append(
                {
                    "start": start,
                    "version": version,
                    "place": nested_place(
                        structure, fragment, kid, by_fragment
                    ),
                    "url": (
                        reading.build_url(
                            route.act,
                            on=read_on,
                            eid=unit.eid,
                            lang=route.lang,
                        )
                        + "#"
                        + kid.eid
                    ),
                    "history_url": reading.build_url(
                        route.act,
                        eid=kid.eid,
                        lang=route.lang,
                        history=True,
                    ),
                    "introduced": {
                        "label": label,
                        "url": amendment_url(amendment),
                        "note": amendment.note,
                    },
                }
            )
    changes.sort(
        key=lambda entry: (entry["start"] or datetime.date.min, entry["place"])
    )
    return changes


def nested_place(structure, fragment, kid, by_fragment):
    """«(3)(α)»: номера от фрагмента страницы истории (не включая его) до
    kid."""
    chain = []
    while kid is not None and kid.id != fragment.id:
        chain.insert(0, kid)
        kid = structure.parent(kid)
    parts = []
    for level in chain:
        nums = [
            version.num
            for version in by_fragment.get(level.id, [])
            if version.num
        ]
        parts.append(nums[-1] if nums else level.eid.rsplit(".", 1)[-1])
    return "".join(parts)


def history_label(structure, fragment, route, version=None):
    """«Статья 5. Заголовок перевода» (без перевода — «Статья 5»), у пункта —
    «Статья 5, 1».

    version — редакция страницы для подписи; по умолчанию — действующая на
    дату.
    """
    unit = structure.unit_of(fragment) or fragment
    version = version or unit.version_on(route.date) or unit.versions.last()
    headings = reading.translated_headings(
        [version],
        route.lang,
        route.act.structure_lang or reading.DEFAULT_LANG,
    )
    label = reading.fragment_label(unit, version, headings, route.lang)
    prefix = reading.KIND_PREFIX.get(route.lang, {}).get(unit.kind)
    if (
        label.lang != route.lang
        and prefix
        and version is not None
        and version.num
    ):
        label = reading.Label(f"{prefix} {version.num}", route.lang)
    if fragment.id != unit.id:
        own = fragment.version_on(route.date) or fragment.versions.last()
        if own is not None and own.num:
            label = reading.Label(f"{label.text}, {own.num}", label.lang)
    return label


def introduced_by(amendment):
    """Кем введена редакция: «Закон 2(I)/2024, art_2», адрес вводящего
    положения, примечание."""
    label = reading.act_label(amendment.amending_act)
    if amendment.amending_fragment is not None:
        label += ", " + amendment.amending_fragment.eid
    return {
        "label": label,
        "url": amendment_url(amendment),
        "note": amendment.note,
    }


def amendment_url(amendment):
    """Вводящее положение изменяющего акта (или сам акт); акт скрыт — пустой
    адрес."""
    if not access.act_visible(amendment.amending_act):
        return ""
    eid = (
        amendment.amending_fragment.eid
        if amendment.amending_fragment
        else None
    )
    return reading.build_url(amendment.amending_act, eid=eid)
