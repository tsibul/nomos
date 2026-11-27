"""Раздел «Аналитика»: что меняется 1 января и сравнение «было — стало».

Проект — docs/design/site-navigation.md, 1.4. Страницы строятся только из
редакций фрагментов (FragmentVersion, FragmentText); даты страниц и раздел, по
которому они строятся, — из `<changes>` в nav.xml (NavNode).
"""

import datetime
import difflib
import html

from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy

from nomos.acts import grouping
from nomos.acts.models import (
    Amendment,
    Fragment,
    FragmentText,
    FragmentVersion,
)

from . import access, menu, paths, reading, views

# вид изменения фрагмента — из Amendment.kind
KIND_LABELS = {
    Amendment.Kind.REPLACE: gettext_lazy("заменён"),
    Amendment.Kind.INSERT: gettext_lazy("вставлен"),
    Amendment.Kind.DELETE: gettext_lazy("исключён"),
    Amendment.Kind.RENUMBER: gettext_lazy("перенумерован"),
    Amendment.Kind.CORRECT: gettext_lazy("исправлен"),
}
# редакция «исключён» при любом виде изменения (и при отмене всего акта)
REPEALED_LABEL = gettext_lazy("исключён")

# вид изменения — для модификатора БЭМ
KIND_MODIFIERS = {
    Amendment.Kind.INSERT: "insert",
    Amendment.Kind.DELETE: "delete",
}


def analytics_url():
    return paths.url("/analytics/")


def changes_url(on):
    return paths.url(f"/analytics/changes/{on.isoformat()}/")


def compare_url(on, fragment, lang=None):
    tail = f"{lang}/" if lang else ""
    return changes_url(on) + f"{fragment.uid}/{tail}"


def changes_node(tree, text):
    """Страница «Что меняется» на дату из адреса; нет такой в nav.xml —
    404."""
    try:
        on = datetime.date.fromisoformat(text)
    except ValueError:
        raise Http404("Неверная дата") from None
    for node in tree.changes():
        if node.changes_on == on:
            return node
    raise Http404("Нет страницы на эту дату")


def scope_groups(tree, node):
    """Основные законы раздела страницы и их неосновные акты.

    [(пункт меню, [основной акт, неосновные акты…])]. Неосновной акт стоит у
    каждого основного, к которому ведёт цепочка связей (grouping.Relations);
    основные акты других разделов (акты ЕС у кипрского закона) не входят.
    """
    section = tree.find(node.scope.split("/")) if node.scope else None
    if section is None:
        return []
    relations = grouping.Relations()
    main_ids = grouping.main_act_ids()
    groups = []
    for item in tree.items(section):
        act = item.act
        if act is None or not access.act_visible(act):
            continue
        others = {
            other.id: other
            for acts in relations.blocks(act, main_ids).values()
            for other in acts
            if other.id not in main_ids and access.act_visible(other)
        }
        ordered = sorted(others.values(), key=grouping.sort_key)
        groups.append((item, [act, *ordered]))
    return groups


def starts_on(on):
    """Редакция начинается на дату по оси «применяется» (как на сайте)."""
    return Q(applies_from=on) | Q(applies_from__isnull=True, valid_from=on)


def changed_versions(acts, on):
    """{id фрагмента: редакция, начинающаяся на дату}.

    Базовый текст нового акта изменением не считается; отмена всего акта
    (исключённый базовый текст) — считается. Из двух редакций на одну дату
    берётся первая по порядку загрузки — она и действует (Fragment.version_on).
    """
    versions = (
        FragmentVersion.objects.filter(
            starts_on(on),
            fragment__act__in=acts,
            known_from__lte=datetime.date.today(),
        )
        .exclude(
            amendment__kind=Amendment.Kind.BASE,
            state=FragmentVersion.State.ACTIVE,
        )
        .select_related(
            "amendment__amending_act",
            "amendment__amending_fragment",
        )
        .order_by("id")
    )
    found = {}
    for version in versions:
        found.setdefault(version.fragment_id, version)
    return found


def kind_label(version):
    if version.state == FragmentVersion.State.REPEALED:
        return REPEALED_LABEL
    return KIND_LABELS.get(version.amendment.kind, "")


def kind_modifier(version):
    if version.state == FragmentVersion.State.REPEALED:
        return "delete"
    return KIND_MODIFIERS.get(version.amendment.kind, "replace")


def act_versions(act):
    """Редакции всех фрагментов акта одним запросом: {id фрагмента:
    [редакции]}."""
    found = {}
    for version in FragmentVersion.objects.filter(fragment__act=act).order_by(
        "id"
    ):
        found.setdefault(version.fragment_id, []).append(version)
    return found


def label_version(fragment, by_fragment, version=None):
    """Редакция для подписи: своя, если в ней есть номер или заголовок, иначе
    последняя такая (у исключённой редакции их обычно нет)."""
    if version is not None and (version.num or version.heading):
        return version
    named = [
        found
        for found in by_fragment.get(fragment.id, [])
        if found.num or found.heading
    ]
    return named[-1] if named else version


def act_order(structure):
    """Все фрагменты акта по порядку текста."""
    found = []
    for root in structure.roots():
        found.append(root)
        found.extend(views.descendants(structure, root))
    return found


def top_changed(structure, changed):
    """Изменённые фрагменты, у которых нет изменённого предка, по порядку
    акта; к каждому — число изменённых вложенных."""
    found = []
    for fragment in act_order(structure):
        if fragment.id not in changed:
            continue
        ancestors = structure.ancestors(fragment)
        if any(ancestor.id in changed for ancestor in ancestors):
            continue
        nested = sum(
            1
            for kid in views.descendants(structure, fragment)
            if kid.id in changed
        )
        found.append((fragment, nested))
    return found


def fragment_title(structure, fragment, version, by_fragment, lang):
    """«Статья 14. Заголовок», у вложенного — «Статья 14, (3)(α)»."""
    unit = structure.unit_of(fragment) or fragment
    own = version if unit.id == fragment.id else None
    unit_version = label_version(unit, by_fragment, own)
    headings = reading.translated_headings(
        [unit_version], lang, reading.original_lang(structure.act)
    )
    label = reading.fragment_label(unit, unit_version, headings, lang)
    if unit.id == fragment.id:
        return label
    place = views.nested_place(structure, unit, fragment, by_fragment)
    return reading.Label(f"{label.text}, {place}", label.lang)


def act_changes(act, on, lang):
    """Изменения акта на дату: [{'title', 'kind', 'introduced', 'url'…}]."""
    changed = changed_versions([act], on)
    if not changed:
        return []
    structure = reading.Structure(act)
    by_fragment = act_versions(act)
    entries = []
    for fragment, nested in top_changed(structure, changed):
        version = changed[fragment.id]
        entries.append(
            {
                "title": fragment_title(
                    structure, fragment, version, by_fragment, lang
                ),
                "kind": kind_label(version),
                "modifier": kind_modifier(version),
                "introduced": views.introduced_by(version.amendment),
                "nested": nested,
                "url": compare_url(on, fragment),
            }
        )
    return entries


def index(request):
    """Раздел «Аналитика»: список страниц «Что меняется»."""
    tree = menu.Tree()
    section = tree.find(["analytics"])
    pages = [
        {"on": node.changes_on, "url": changes_url(node.changes_on)}
        for node in tree.changes(section)
    ]
    return render(
        request,
        "site/analytics.html",
        {
            "section": menu.label(section) if section else "",
            "pages": pages,
        },
    )


def changes(request, on):
    """Что меняется на дату: фрагменты по основным законам раздела."""
    tree = menu.Tree()
    node = changes_node(tree, on)
    on = node.changes_on
    lang = reading.interface_lang()
    groups = []
    for item, acts in scope_groups(tree, node):
        parts = []
        for act in acts:
            entries = act_changes(act, on, lang)
            if entries:
                parts.append(
                    {
                        "act": views.act_entry(act, "", lang),
                        "main": act.id == item.act_id,
                        "entries": entries,
                    }
                )
        if parts:
            groups.append({"label": menu.label(item), "parts": parts})
    return render(
        request,
        "site/changes.html",
        {
            "on": on,
            "future": on > datetime.date.today(),
            "groups": groups,
            "analytics_url": analytics_url(),
        },
    )


def plain(text):
    """Текст без разметки: теги сняты, сущности раскрыты, пробелы сжаты."""
    return " ".join(html.unescape(strip_tags(text)).split())


def side_versions(fragments, on):
    """Редакции поддерева на дату: [(фрагмент, редакция или None)]."""
    return [(fragment, fragment.version_on(on)) for fragment in fragments]


def texts_by_version(versions):
    """Видимые тексты редакций одним запросом: {id редакции: [тексты]}."""
    ids = [version.id for version in versions if version is not None]
    found = {}
    queryset = access.texts(
        FragmentText.objects.filter(version__in=ids)
    ).select_related("expression")
    for text in queryset:
        found.setdefault(text.version_id, []).append(text)
    return found


def paragraph(act, version, texts, lang, original):
    """Абзац стороны сравнения: номер, заголовок и текст на языке lang."""
    text = reading.pick_text(texts.get(version.id, []), lang)
    if lang == original:
        heading = reading.structure_heading(act, version)
    else:
        heading = text.heading if text is not None else ""
    body = plain(text.body) if text is not None else ""
    return " ".join(part for part in (version.num, heading, body) if part)


def side_paragraphs(act, side, texts, lang, original):
    found = []
    for _fragment, version in side:
        if version is None:
            continue
        line = paragraph(act, version, texts, lang, original)
        if line:
            found.append(line)
    return found


def has_lang(side, texts, lang, original):
    """Есть ли у стороны текст на языке lang везде, где есть оригинал."""
    for _fragment, version in side:
        if version is None:
            continue
        own = texts.get(version.id, [])
        source = reading.pick_text(own, original)
        if source is None or not plain(source.body):
            continue
        translated = reading.pick_text(own, lang)
        if translated is None or not plain(translated.body):
            return False
    return True


def word_diff(old, new):
    """Разница по словам: абзацы обеих сторон как [[{'text', 'changed'}]]."""
    old_words = [
        (index, word)
        for index, line in enumerate(old)
        for word in line.split()
    ]
    new_words = [
        (index, word)
        for index, line in enumerate(new)
        for word in line.split()
    ]
    matcher = difflib.SequenceMatcher(
        None,
        [word for _index, word in old_words],
        [word for _index, word in new_words],
        autojunk=False,
    )
    old_changed = set()
    new_changed = set()
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed.update(range(old_start, old_end))
        new_changed.update(range(new_start, new_end))
    return (
        segments(old_words, old_changed, len(old)),
        segments(new_words, new_changed, len(new)),
    )


def segments(words, changed, count):
    """Слова по абзацам, соседние с одинаковой пометкой — одним отрезком."""
    paragraphs = [[] for _index in range(count)]
    for position, (index, word) in enumerate(words):
        mark = position in changed
        current = paragraphs[index]
        if current and current[-1]["changed"] == mark:
            current[-1]["text"] += " " + word
        else:
            current.append({"text": word, "changed": mark})
    return paragraphs


def compare_lang(act, lang, sides, texts):
    """Язык сравнения и языки переключателя.

    По умолчанию — оригинал; русский — если он есть у обеих редакций.
    """
    original = reading.original_lang(act)
    langs = []
    if access.lang_visible(act, original):
        langs.append(original)
    reader = reading.READER_LANG
    if (
        reader != original
        and access.lang_visible(act, reader)
        and all(has_lang(side, texts, reader, original) for side in sides)
    ):
        langs.append(reader)
    if lang is None:
        lang = langs[0] if langs else None
    if lang not in langs:
        raise Http404("Нет текста на этом языке")
    return lang, langs


def side_url(act, side, on, unit, lang, anchor):
    """Страница сайта с редакцией стороны на дату; сторона пуста — ''."""
    if all(version is None for _fragment, version in side):
        return ""
    return reading.build_url(act, on=on, eid=unit.eid, lang=lang) + anchor


def compare(request, on, uid, lang=None):
    """Было — стало: редакция накануне даты и новая, разница по словам."""
    tree = menu.Tree()
    node = changes_node(tree, on)
    on = node.changes_on
    fragment = Fragment.objects.filter(uid=uid).select_related("act").first()
    if fragment is None:
        raise Http404("Нет такого фрагмента")
    act = fragment.act
    scope_ids = {
        other.id for _item, acts in scope_groups(tree, node) for other in acts
    }
    if act.id not in scope_ids:
        raise Http404("Фрагмент не из раздела страницы")
    version = changed_versions([act], on).get(fragment.id)
    if version is None:
        raise Http404("Фрагмент на эту дату не меняется")
    structure = reading.Structure(act)
    fragment = structure.by_id[fragment.id]
    subtree = [fragment, *views.descendants(structure, fragment)]
    before = on - datetime.timedelta(days=1)
    old = side_versions(subtree, before)
    new = side_versions(subtree, on)
    texts = texts_by_version([found for _fragment, found in old + new])
    lang, langs = compare_lang(act, lang, (old, new), texts)
    original = reading.original_lang(act)
    old_paragraphs, new_paragraphs = word_diff(
        side_paragraphs(act, old, texts, lang, original),
        side_paragraphs(act, new, texts, lang, original),
    )
    by_fragment = act_versions(act)
    title = fragment_title(structure, fragment, version, by_fragment, lang)
    unit = structure.unit_of(fragment) or fragment
    anchor = "" if unit.id == fragment.id else "#" + fragment.eid
    return render(
        request,
        "site/compare.html",
        {
            "on": on,
            "before": before,
            "title": title,
            "act": views.act_entry(act, "", reading.interface_lang()),
            "kind": kind_label(version),
            "introduced": views.introduced_by(version.amendment),
            "old": old_paragraphs,
            "new": new_paragraphs,
            "lang": lang,
            "langs": [
                {
                    "code": code,
                    "url": compare_url(
                        on, fragment, None if code == langs[0] else code
                    ),
                    "current": code == lang,
                }
                for code in langs
            ],
            # было пусто (вставлен) или стало пусто (исключён) — читать нечего
            "old_url": side_url(act, old, before, unit, lang, anchor),
            "new_url": side_url(act, new, on, unit, lang, anchor),
            "changes_url": changes_url(on),
            "analytics_url": analytics_url(),
        },
    )
