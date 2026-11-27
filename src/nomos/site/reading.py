"""Чтение актов: разбор адреса страницы и сборка дерева фрагментов для
шаблонов.

Выбор редакции — только методами моделей (`Fragment.version_on`,
`Fragment.future_version`);
здесь лишь обход дерева, подбор текстов по языку и подписи.
"""

import datetime
import re
from dataclasses import dataclass, field

from django.db import models
from django.http import Http404
from django.utils import translation
from django.utils.html import escape
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from nomos.acts.models import (
    Act,
    ActRelation,
    Expression,
    Fragment,
    FragmentAlias,
    FragmentText,
    FragmentVersion,
)
from nomos.translate.models import Note

from . import access, paths

EARLIEST = datetime.date(2024, 1, 1)
LANGS = ("el", "en", "ru")
# язык оригинала (официального текста); язык показа по умолчанию —
# default_lang(act)
DEFAULT_LANG = "el"
READER_LANG = "ru"
HISTORY = "history"

# Фрагменты-разделы: не страница, а заголовок в оглавлении
CONTAINER_KINDS = {
    Fragment.Kind.PART,
    Fragment.Kind.TITLE,
    Fragment.Kind.CHAPTER,
    Fragment.Kind.SECTION,
    Fragment.Kind.SUBSECTION,
}

# Порядок предпочтения текстов одного языка
STATUS_ORDER = (
    Expression.Status.OFFICIAL_TEXT,
    Expression.Status.OFFICIAL_TRANSLATION,
    Expression.Status.OUR_TRANSLATION,
    Expression.Status.UNOFFICIAL_TRANSLATION,
)

KIND_PREFIX = {
    "el": {Fragment.Kind.ARTICLE: "Άρθρο"},
    "ru": {Fragment.Kind.ARTICLE: "Статья"},
    "en": {Fragment.Kind.ARTICLE: "Article"},
}


@dataclass
class Route:
    """Разобранный адрес: /<акт>/[@<дата>/][<eid>/][history/][<язык>/]."""

    act: Act
    on: datetime.date | None = None
    eid: str | None = None
    lang: str = DEFAULT_LANG
    history: bool = False
    # язык задан в адресе явно (/el/, /ru/), а не взят по умолчанию
    explicit_lang: bool = False
    # начало цепочки переходов по ссылкам: {'start': адрес, 'start_at': место}
    # (views.read)
    chain: dict = field(default_factory=dict)

    @property
    def chain_params(self):
        """Параметры начала цепочки для адресов страницы: `start=…&start_at=…`
        или пусто."""
        return urlencode(self.chain)

    @property
    def chain_tail(self):
        """То же после других параметров: `&start=…` или пусто."""
        return "&" + self.chain_params if self.chain else ""

    @property
    def chain_query(self):
        """То же со знаком вопроса — хвост адреса без других параметров."""
        return "?" + self.chain_params if self.chain else ""

    @property
    def date(self):
        return self.on or datetime.date.today()


def parse_date(text):
    """`2024-06-01` — эта дата; `2024` — конец года (текущий год — сегодня)."""
    today = datetime.date.today()
    try:
        if re.fullmatch(r"\d{4}", text):
            year = int(text)
            if year == today.year:
                return today
            return datetime.date(year, 12, 31)
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def parse_path(path):
    segments = [segment for segment in path.strip("/").split("/") if segment]
    act = None
    for size in (4, 3):
        act = Act.objects.filter(uid="/".join(segments[:size])).first()
        if act is not None:
            break
    if act is None:
        raise Http404("Акт не найден")
    route = Route(act=act, lang=default_lang(act))
    rest = segments[size:]
    if rest and rest[0].startswith("@"):
        route.on = parse_date(rest[0][1:])
        if route.on is None:
            raise Http404("Неверная дата")
        rest = rest[1:]
    if rest and rest[0] not in LANGS and rest[0] != HISTORY:
        route.eid = rest[0]
        rest = rest[1:]
    if rest and rest[0] in LANGS:
        route.lang = rest[0]
        route.explicit_lang = True
        rest = rest[1:]
    elif rest and rest[0] == HISTORY and route.eid:
        route.history = True
        rest = rest[1:]
        if rest and rest[0] in LANGS:
            route.lang = rest[0]
            route.explicit_lang = True
            rest = rest[1:]
    if rest:
        raise Http404("Неверный адрес")
    return route


def default_lang(act):
    """Язык адреса без языка: русский, если у акта есть русский текст, иначе
    язык оригинала."""
    restricted = access.is_restricted()
    cache = act.__dict__.setdefault("_default_lang", {})
    if restricted not in cache:
        langs = access.visible_langs(act)
        structure_lang = act.structure_lang or DEFAULT_LANG
        if READER_LANG in langs:
            cache[restricted] = READER_LANG
        elif restricted and structure_lang not in langs and langs:
            # оригинал скрыт: первый видимый язык
            cache[restricted] = [lang for lang in LANGS if lang in langs][0]
        else:
            cache[restricted] = structure_lang
    return cache[restricted]


def build_url(act, on=None, eid=None, lang=None, history=False):
    """Адрес страницы; без даты — действующая сегодня редакция, без языка —
    default_lang."""
    parts = [act.uid]
    if on is not None:
        parts.append("@" + on.isoformat())
    if eid:
        parts.append(eid)
    if history:
        parts.append(HISTORY)
    if lang is not None and lang != default_lang(act):
        parts.append(lang)
    return paths.url("/" + "/".join(parts) + "/")


# языки, на которые переведён весь интерфейс; на прочих строки выходят
# по-русски
UI_LANGS = ("ru", "el")

# виды актов на языке интерфейса (в модели Act.Kind — русские названия)
KIND_NAMES = {
    Act.Kind.LAW: gettext_lazy("Закон"),
    Act.Kind.REGULATION_BY_LAW: gettext_lazy("Подзаконный акт (ΚΔΠ)"),
    Act.Kind.DIRECTIVE: gettext_lazy("Директива"),
    Act.Kind.REGULATION: gettext_lazy("Регламент"),
    Act.Kind.IMPLEMENTING_REGULATION: gettext_lazy(
        "Имплементационный регламент"
    ),
    Act.Kind.STANDARD: gettext_lazy("Стандарт"),
    Act.Kind.INSTRUCTION: gettext_lazy("Инструкция"),
    Act.Kind.CIRCULAR: gettext_lazy("Циркуляр"),
    Act.Kind.DECISION: gettext_lazy("Решение"),
}

# виды фрагментов без номера и заголовка — на языке интерфейса (в модели —
# русские названия)
FRAGMENT_KIND_NAMES = {
    Fragment.Kind.CITATION: gettext_lazy("Преамбула"),
}


def interface_lang():
    """Язык, на котором выходят строки интерфейса (включённый, если он
    переведён)."""
    lang = translation.get_language()
    return lang if lang in UI_LANGS else READER_LANG


def original_lang(act):
    """Язык оригинала акта: у кипрских — el, у актов ЕС — en
    (structure_lang)."""
    return act.structure_lang or DEFAULT_LANG


def act_number(act):
    """«95(I)/2000»; у актов ЕС год уже в номере, как в обозначении:
    «904/2010», «2025/517»."""
    number = act.number or ""
    if not act.year or act.year in number.split("/"):
        return number or act.year or ""
    return f"{number}/{act.year}" if number else act.year


def act_label(act):
    """«Закон 95(I)/2000», «Подзаконный акт (ΚΔΠ) 95/2024»; на el — «Νόμος
    95(I)/2000»."""
    number = act_number(act)
    kind = KIND_NAMES.get(act.kind) or act.get_kind_display()
    return f"{kind} {number}".strip()


def act_title(act, lang=DEFAULT_LANG):
    """Название на языке чтения, иначе на опорном."""
    return act_title_lang(act, lang)[0]


def act_title_lang(act, lang=DEFAULT_LANG):
    """Название и его язык (для атрибута lang): на языке чтения, иначе на
    опорном."""
    expressions = access.expressions(act)
    for wanted in (lang, act.structure_lang or DEFAULT_LANG, DEFAULT_LANG):
        for expression in expressions:
            if expression.lang == wanted and expression.title:
                return expression.title, expression.lang
    if access.is_restricted():
        # оригинал скрыт: название любого видимого выражения
        for expression in expressions:
            if expression.title:
                return expression.title, expression.lang
    return "", wanted


def act_short_label(act, lang=DEFAULT_LANG):
    """Короткое название для списков: «Закон о НДС»; у ΚΔΠ — «ΚΔΠ 95/2024 от
    15.03.2024».

    Законы: short_title выражения на языке чтения, иначе на опорном; пока его
    нет —
    полное название. ΚΔΠ: вычисляется из номера и даты публикации, не хранится.
    """
    if act.kind == Act.Kind.REGULATION_BY_LAW:
        text = f"ΚΔΠ {act.number}/{act.year}"
        day = act.published_on or act.date_adopted
        if day is not None:
            text = f"{text} {_('от')} {day:%d.%m.%Y}"
        return Label(text, interface_lang())
    stored = stored_short_label(act, lang)
    if stored is not None:
        return stored
    amending = amending_short_label(act, lang)
    if amending is not None:
        return amending
    title, title_lang = act_title_lang(act, lang)
    if title:
        return Label(title, title_lang)
    return Label(act_label(act), interface_lang())


def stored_short_label(act, lang):
    """short_title выражения на языке чтения, иначе на опорном; нет — None."""
    expressions = access.expressions(act)
    for wanted in (lang, act.structure_lang or DEFAULT_LANG):
        for expression in expressions:
            if expression.lang == wanted and expression.short_title:
                return Label(expression.short_title, expression.lang)
    return None


AMENDING_SUFFIX = {
    "ru": "(изм.)",
    "el": "(Τροποποιητικός)",
}
EL_SHORT_RE = re.compile(r"^Ο περί (.+) Νόμος$")


def amending_short_label(act, lang):
    """Изменяющий закон: короткое название основного + «(изм.)» + номер.

    «Закон о НДС (изм.) 10(I)/2024», «ΦΠΑ (Τροποποιητικός) 10(I)/2024».
    Вычисляется, не хранится; у основного нет короткого названия — None.
    """
    relation = (
        act.relations_out.filter(kind=ActRelation.Kind.AMENDS)
        .select_related("to_act")
        .order_by("to_act__uid")
        .first()
    )
    if relation is None:
        return None
    base = stored_short_label(relation.to_act, lang)
    if base is None or base.lang not in AMENDING_SUFFIX:
        return None
    text = base.text
    if base.lang == "el":
        match = EL_SHORT_RE.match(text)
        if match:
            text = match.group(1)
    number = act_number(act)
    return Label(f"{text} {AMENDING_SUFFIX[base.lang]} {number}", base.lang)


def act_sort_key(act):
    """Год, затем номер числом: 82/2026 раньше 102/2026."""
    match = re.match(r"\d+", act.number or "")
    number = int(match.group()) if match else 0
    return (int(act.year or 0), number, act.number or "")


TARGET_LABELS = {
    "art": gettext_lazy("ст."),
    "sch": gettext_lazy("прил."),
}


def amendment_targets(acts):
    """Что меняют изменяющие акты: {id акта: «ст. 45, 61; прил. 12»} по данным
    изменений.

    Берутся статьи и приложения других актов, чьи редакции введены актом; нет
    данных —
    нет подписи.
    """
    rows = (
        FragmentVersion.objects.filter(amendment__amending_act__in=acts)
        .exclude(fragment__act=models.F("amendment__amending_act"))
        .values_list("amendment__amending_act_id", "fragment__eid")
    )
    found = {}
    for act_id, eid in rows:
        kind, _, number = eid.split(".")[0].partition("_")
        if kind in TARGET_LABELS and number:
            found.setdefault(act_id, {}).setdefault(kind, set()).add(number)
    result = {}
    for act_id, kinds in found.items():
        parts = []
        for kind, prefix in TARGET_LABELS.items():
            numbers = sorted(kinds.get(kind, ()), key=number_sort_key)
            if numbers:
                parts.append(f"{prefix} {', '.join(numbers)}")
        result[act_id] = "; ".join(parts)
    return result


def number_sort_key(number):
    match = re.match(r"\d+", number)
    return (int(match.group()) if match else 0, number)


def act_langs(act):
    """Языки переключателя: язык оригинала и те, на которые есть тексты
    акта."""
    present = set(access.visible_langs(act))
    if not access.is_restricted():
        present.add(act.structure_lang or DEFAULT_LANG)
    return [lang for lang in LANGS if lang in present]


@dataclass
class Label:
    """Подпись фрагмента и её язык (для атрибута lang)."""

    text: str
    lang: str = DEFAULT_LANG

    def __str__(self):
        return self.text


def fragment_label(fragment, version, headings=None, lang=None):
    """«Άρθρο 45. Παραβάσεις»; с переведённым заголовком — «Статья 45.
    Нарушения».

    headings — {id редакции: заголовок на языке lang} (см.
    translated_headings).
    lang — язык просмотра; не задан — язык оригинала.
    """
    structure_lang = fragment.act.structure_lang or DEFAULT_LANG
    lang = lang or structure_lang
    if version is None:
        return Label(fragment.eid, structure_lang)
    heading = structure_heading(fragment.act, version)
    label_lang = structure_lang
    if not access.lang_visible(fragment.act, structure_lang):
        # оригинал скрыт: приставка («Статья») — на языке перевода или
        # интерфейса
        label_lang = lang if lang != structure_lang else interface_lang()
    elif (
        not heading
        and lang != structure_lang
        and lang in access.visible_langs(fragment.act)
    ):
        # заголовка нет вовсе, перевод акта есть: «Статья 1», а не «Article 1»
        label_lang = lang
    translated = (headings or {}).get(version.id)
    if translated:
        heading = translated
        label_lang = lang
    prefix = KIND_PREFIX.get(label_lang, {}).get(fragment.kind)
    if prefix and version.num:
        text = f"{prefix} {version.num}"
        if heading:
            text = f"{text}. {heading}"
        return Label(text, label_lang)
    if heading:
        return Label(heading, label_lang)
    if version.num:
        return Label(version.num, structure_lang)
    name = FRAGMENT_KIND_NAMES.get(fragment.kind)
    if name:
        return Label(str(name), interface_lang())
    return Label(fragment.get_kind_display(), structure_lang)


def structure_heading(act, version):
    """Заголовок из редакции (на языке оригинала); оригинал скрыт — пустой."""
    if access.lang_visible(act, act.structure_lang or DEFAULT_LANG):
        return version.heading
    return ""


def translated_headings(versions, lang, original_lang=DEFAULT_LANG):
    """Заголовки перевода одним запросом: {id редакции: заголовок}.

    original_lang — язык оригинала акта (у актов ЕС — en): на нём перевода нет.
    """
    if lang == original_lang:
        return {}
    ids = [version.id for version in versions if version is not None]
    texts = (
        access.texts(
            FragmentText.objects.filter(version__in=ids, expression__lang=lang)
        )
        .exclude(heading="")
        .values_list("version_id", "heading")
    )
    return dict(texts)


def translated_units(structure, lang):
    """id страниц, где переведён хоть один текст (любой редакции); один
    заголовок не в счёт."""
    if lang == (structure.act.structure_lang or DEFAULT_LANG):
        return None
    texts = FragmentText.objects.filter(
        version__fragment__act=structure.act,
        expression__lang=lang,
    )
    fragment_ids = (
        access.texts(texts)
        .exclude(body="")
        .values_list("version__fragment_id", flat=True)
    )
    units = set()
    for fragment_id in set(fragment_ids):
        unit = structure.unit_of(structure.by_id[fragment_id])
        if unit is not None:
            units.add(unit.id)
    return units


class Structure:
    """Дерево фрагментов акта одним запросом: дети по порядку, предки,
    страницы."""

    def __init__(self, act):
        self.act = act
        # Порядок загрузки = порядок в act.xml. order_key не годится: у братьев
        # разных
        # видов ключи совпадают (art_2 и prt_II — оба 0002).
        fragments = list(act.fragments.order_by("id"))
        self.by_id = {fragment.id: fragment for fragment in fragments}
        self.by_eid = {fragment.eid: fragment for fragment in fragments}
        self.children = {}
        for fragment in fragments:
            self.children.setdefault(fragment.parent_id, []).append(fragment)

    def roots(self):
        return self.children.get(None, [])

    def kids(self, fragment):
        return self.children.get(fragment.id, [])

    def parent(self, fragment):
        return self.by_id.get(fragment.parent_id)

    def is_unit(self, fragment):
        """Страница — статья, приложение и т. п.: не раздел и не внутри
        не-раздела."""
        if fragment.kind in CONTAINER_KINDS:
            return False
        parent = self.parent(fragment)
        return parent is None or parent.kind in CONTAINER_KINDS

    def unit_of(self, fragment):
        while fragment is not None and not self.is_unit(fragment):
            fragment = self.parent(fragment)
        return fragment

    def first_unit(self, fragment):
        """Первая страница внутри раздела (по порядку акта); у пустого раздела
        — None."""
        for kid in self.kids(fragment):
            if self.is_unit(kid):
                return kid
            unit = self.first_unit(kid)
            if unit is not None:
                return unit
        return None

    def ancestors(self, fragment):
        chain = []
        parent = self.parent(fragment)
        while parent is not None:
            chain.insert(0, parent)
            parent = self.parent(parent)
        return chain

    def find(self, eid):
        """Фрагмент по eid или по прежнему адресу (FragmentAlias)."""
        fragment = self.by_eid.get(eid)
        if fragment is not None:
            return fragment
        alias = (
            FragmentAlias.objects.filter(fragment__act=self.act, eid=eid)
            .select_related("fragment")
            .first()
        )
        return alias.fragment if alias else None


@dataclass
class TocItem:
    fragment: Fragment
    version: object
    is_unit: bool
    label: Label | None = None
    url: str = ""
    translated: bool = True
    current: bool = False
    children: list = field(default_factory=list)


def build_toc(structure, route):
    """Оглавление на дату: разделы и страницы, отсутствующие на дату — не
    показываются.

    На языке перевода — переведённые заголовки и пометка у страниц без
    перевода.
    """
    on = route.date
    flat = []

    def walk(fragments):
        items = []
        for fragment in fragments:
            if fragment.kind not in CONTAINER_KINDS and not structure.is_unit(
                fragment
            ):
                continue
            version = fragment.version_on(on)
            if version is None:
                continue
            item = TocItem(
                fragment=fragment,
                version=version,
                is_unit=structure.is_unit(fragment),
            )
            flat.append(item)
            if item.is_unit:
                item.url = build_url(
                    structure.act,
                    on=route.on,
                    eid=fragment.eid,
                    lang=route.lang,
                )
            else:
                item.children = walk(structure.kids(fragment))
            items.append(item)
        return items

    items = walk(structure.roots())
    headings = translated_headings(
        [item.version for item in flat],
        route.lang,
        structure.act.structure_lang or DEFAULT_LANG,
    )
    translated = translated_units(structure, route.lang)
    for item in flat:
        item.label = fragment_label(
            item.fragment, item.version, headings, route.lang
        )
        if translated is not None and item.is_unit:
            item.translated = item.fragment.id in translated
    return items


def toc_items_by_id(items, found=None):
    """Пункты оглавления по id фрагмента — подписи для крошек и заголовка
    страницы."""
    if found is None:
        found = {}
    for item in items:
        found[item.fragment.id] = item
        toc_items_by_id(item.children, found)
    return found


def toc_units(items):
    """Страницы оглавления подряд — для «предыдущая / следующая»."""
    units = []
    for item in items:
        if item.is_unit:
            units.append(item)
        units.extend(toc_units(item.children))
    return units


@dataclass
class Node:
    """Фрагмент страницы на дату и язык."""

    fragment: Fragment
    version: object
    text: FragmentText | None = None
    original: FragmentText | None = None
    no_translation: bool = False
    future: object = None
    body: str = ""
    original_body: str = ""
    notes: list = field(default_factory=list)
    children: list = field(default_factory=list)
    history_url: str = ""
    future_url: str = ""
    introduced_url: str = ""
    introduced_label: str = ""

    @property
    def unknown(self):
        """Текст неизвестен: текст редакции записан пустым (у разделов текста
        нет вовсе).

        Текст из одного заголовка (перевод заголовка статьи) — не «неизвестен».
        """
        shown = self.text or self.original
        return (
            shown is not None and not shown.body.strip() and not shown.heading
        )

    @property
    def heading(self):
        """Заголовок на языке текста: перевода, иначе опорный из редакции."""
        if self.text is not None and self.text.heading:
            return Label(self.text.heading, self.text.expression.lang)
        structure_lang = self.fragment.act.structure_lang or DEFAULT_LANG
        return Label(
            structure_heading(self.fragment.act, self.version), structure_lang
        )

    @property
    def start(self):
        return self.version.start(Fragment.Axis.APPLIES)

    @property
    def future_start(self):
        if self.future is None:
            return None
        return self.future.start(Fragment.Axis.APPLIES)

    @property
    def introduced(self):
        """Изменение, которым введена редакция; базовый текст — None."""
        amendment = self.version.amendment
        if amendment.kind == amendment.Kind.BASE:
            return None
        return amendment


def pick_text(texts, lang):
    found = [text for text in texts if text.expression.lang == lang]
    found.sort(key=lambda text: STATUS_ORDER.index(text.expression.status))
    return found[0] if found else None


HIDDEN_NOTES = (Note.Kind.SOURCE_COMMENT, Note.Kind.EDITOR_COMMENT)


REF_RE = re.compile(r"<ref\b([^>]*)>(.*?)</ref>", re.S)
REF_ID_RE = re.compile(r'\bid="([^"]*)"')


def render_body(text, route, place=None, marked=True):
    """Тело — разметка из своих XML; `<ref>` с найденной целью становится
    ссылкой.

    place — eid фрагмента. У ссылки в основном тексте (marked) — id
    «ref-<eid>-<anchor>»:
    на него ведёт «← Назад» со страницы цели. Внутренняя ссылка несёт адрес
    возврата
    в параметрах `from` (эта страница) и `at` (место ссылки), а также начало
    цепочки
    переходов (`route.chain`) — для «К началу».
    """
    if text is None:
        return ""
    references = {
        reference.anchor: reference for reference in text.references.all()
    }

    def replace(match):
        found = REF_ID_RE.search(match.group(1))
        anchor = found.group(1) if found else None
        reference = references.get(anchor) if anchor else None
        inner = match.group(2)
        href = reference_url(reference, route) if reference else None
        if href is None:
            return f'<span class="ref ref--unresolved">{inner}</span>'
        ref_id = f"ref-{place}-{anchor}" if place and marked else None
        if reference.to_act is not None:
            params = {"from": page_url(route), "at": ref_id or place or ""}
            href += "?" + urlencode(params | route.chain)
        id_attr = f' id="{escape(ref_id)}"' if ref_id else ""
        return f'<a class="ref"{id_attr} href="{escape(href)}">{inner}</a>'

    return REF_RE.sub(replace, text.body)


def page_url(route):
    """Адрес открытой страницы акта: дата, положение, язык."""
    return build_url(route.act, on=route.on, eid=route.eid, lang=route.lang)


def reference_url(reference, route):
    if not reference.resolved:
        return None
    if reference.to_act is None:
        return reference.external_url or None
    if not access.act_visible(reference.to_act):
        # цель скрыта от посетителя: ссылка остаётся текстом
        return None
    on = route.on
    if reference.mode == reference.Mode.AT_DATE and reference.to_date:
        on = reference.to_date
    elif reference.mode == reference.Mode.VERSION and reference.to_version:
        on = reference.to_version.start(Fragment.Axis.APPLIES)
    eid = reference.to_fragment.eid if reference.to_fragment else None
    lang = route.lang if route.lang in act_langs(reference.to_act) else None
    return build_url(reference.to_act, on=on, eid=eid, lang=lang)


def build_nodes(structure, unit, route):
    """Дерево страницы: редакции на дату, затем тексты всех редакций одним
    запросом."""
    on = route.date
    today = datetime.date.today()
    nodes = []

    def walk(fragment):
        version = fragment.version_on(on)
        future = fragment.future_version(today=today)
        if future is not None and future.start(Fragment.Axis.APPLIES) <= on:
            future = None
        if version is None and future is None:
            return None
        node = Node(fragment=fragment, version=version, future=future)
        link_node(node, route)
        if version is None:
            return node
        nodes.append(node)
        for child in structure.kids(fragment):
            child_node = walk(child)
            if child_node is not None:
                node.children.append(child_node)
        return node

    root = walk(unit)
    attach_texts(nodes, route)
    return root


def has_translation(node):
    """Есть ли на странице хоть один переведённый текст (одного заголовка
    мало)."""
    text = node.text
    original = original_lang(node.fragment.act)
    if text is not None and text.expression.lang != original and text.body:
        return True
    return any(has_translation(child) for child in node.children)


def link_node(node, route):
    """Адреса у фрагмента: история редакций, будущая редакция, вводящее
    положение."""
    act = route.act
    eid = node.fragment.eid
    node.history_url = build_url(
        act, on=route.on, eid=eid, lang=route.lang, history=True
    )
    node.history_url += route.chain_query
    if node.future is not None:
        node.future_url = build_url(
            act,
            on=node.future.start(Fragment.Axis.APPLIES),
            eid=eid,
            lang=route.lang,
        )
        node.future_url += route.chain_query
    amendment = node.introduced if node.version is not None else None
    if amendment is not None:
        provision = amendment.amending_fragment
        if access.act_visible(amendment.amending_act):
            # скрытый изменяющий акт — номером, без ссылки
            node.introduced_url = build_url(
                amendment.amending_act,
                eid=provision.eid if provision else None,
            )
        node.introduced_label = act_label(amendment.amending_act)
        if provision is not None:
            node.introduced_label += f", {provision.eid}"


def attach_texts(nodes, route):
    texts = (
        access.texts(
            FragmentText.objects.filter(
                version__in=[node.version for node in nodes]
            )
        )
        .select_related("expression", "translated_from")
        .prefetch_related(
            "notes", "references__to_act", "references__to_fragment"
        )
    )
    by_version = {}
    for text in texts:
        by_version.setdefault(text.version_id, []).append(text)
    for node in nodes:
        found = by_version.get(node.version.id, [])
        original = pick_text(found, original_lang(route.act))
        if route.lang == original_lang(route.act):
            node.text = original
        else:
            node.text = pick_text(found, route.lang)
            node.original = original
            node.no_translation = node.text is None and original is not None
        place = node.fragment.eid
        node.body = render_body(node.text or node.original, route, place)
        if node.text is not None and node.original is not None:
            node.original_body = render_body(
                node.original, route, place, marked=False
            )
        notes = []
        for text in (node.text, node.original):
            if text is not None:
                # на сайте — только сомнения перевода: сноски источника и
                # рабочие
                # заметки редактора не показываются
                notes.extend(
                    note
                    for note in text.notes.all()
                    if note.kind not in HIDDEN_NOTES
                )
        node.notes = notes
