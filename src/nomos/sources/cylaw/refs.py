"""Ссылки внутри закона: упоминания статей и приложений → `<ref id="r1" to="eid">`.

Размечается только однозначное: номер статьи (с пунктами в скобках), «εδάφιο (2) του
άρθρου 6», приложение по порядковому числительному (с частью, таблицей или пунктом).
Упоминание, за которым идёт название другого акта («του περί … Νόμου», «Закона о …»),
не размечается. Цель — существующий фрагмент закона; пункт, которого нет, сводится
к ближайшему существующему предку, статья или приложение, которых нет, — не размечаются.
Ссылка фрагмента на себя или на своего предка не размечается.
"""

import re
from collections import Counter
from dataclasses import dataclass

from . import numbering

# разметка, которую ставит этот модуль; снимается перед повторной разметкой
OWN_REF_RE = re.compile(r'<ref id="r\d+" to="[^"]*">(.*?)</ref>', re.S)

SUB = r'\([0-9A-Za-zΑ-Ωα-ωА-Яа-яё]{1,5}\)'
END = r'(?=[\s(,.;:·˙∙\u0387\u037e)»"\-—–]|$)'

# похожие кириллические буквы в номерах русского перевода → греческие
CYRILLIC_TO_GREEK = str.maketrans('АВГДЕЗКМНОРТХ', 'ΑΒΓΔΕΖΚΜΗΟΡΤΧ')
CYRILLIC_LETTER_RE = re.compile(r'[а-яё]')

EL_ORDINALS = [
    ('Ενδέκατ', 11), ('Δωδέκατ', 12), ('Πρώτ', 1), ('Δεύτερ', 2), ('Τρίτ', 3),
    ('Τέταρτ', 4), ('Πέμπτ', 5), ('Έκτ', 6), ('Έβδομ', 7), ('Όγδο', 8), ('Ογδο', 8),
    ('Ένατ', 9), ('Δέκατ', 10),
]
RU_ORDINALS = [
    ('Одиннадцат', 11), ('Двенадцат', 12), ('Тринадцат', 13), ('Четырнадцат', 14),
    ('Пятнадцат', 15), ('Перв', 1), ('Втор', 2), ('Трет', 3), ('Четвёрт', 4),
    ('Четверт', 4), ('Пят', 5), ('Шест', 6), ('Седьм', 7), ('Восьм', 8), ('Девят', 9),
    ('Десят', 10),
]


@dataclass
class Language:
    article_word: str
    number: str
    separators: str
    paragraph_first: str
    schedule: str
    external: str
    ordinals: list
    # слово перед упоминанием, после которого речь о другом акте («его статьи 10А»)
    external_before: str = r'(?!)'
    # пункт без статьи («εδάφιο (1) πιο πάνω»): не размечается, только считается
    bare: str = r'(?!)'

    def __post_init__(self):
        self.article_re = re.compile(self.article_word)
        self.item_re = re.compile(r'(%s)%s((?:%s)*)' % (self.number, END, SUB))
        self.separator_re = re.compile(self.separators)
        self.subs_re = re.compile(r'(?:%s)+' % SUB)
        self.paragraph_re = re.compile(self.paragraph_first)
        self.schedule_re = re.compile(self.schedule)
        self.external_re = re.compile(self.external)
        self.external_before_re = re.compile(self.external_before)
        self.bare_re = re.compile(self.bare)


def ordinal_pattern(ordinals):
    return '|'.join(stem for stem, _ in ordinals)


EL = Language(
    article_word=r'(?<![\wά-ώ])(?:άρθρο|άρθρου|άρθρα|άρθρων)\s+',
    number=r'\d+(?:ΣΤ|[Α-ΩA-Z]){0,2}',
    separators=r',?\s+(?:και|ή|μέχρι|έως)\s+|,\s+',
    paragraph_first=(
        r'(?<![\wά-ώ])(?:εδάφιο|εδαφίου|παράγραφο|παραγράφου|υποπαράγραφο|υποπαραγράφου)'
        r'\s+((?:%s)+)\s+του\s+άρθρου\s+(\d+(?:ΣΤ|[Α-ΩA-Z]){0,2})%s(?!\()' % (SUB, END)
    ),
    schedule=(
        r'(?<![\wά-ώ])(?:(?P<part>Μέρος|Μέρους)\s+(?P<part_num>[IVXΙΧ]+[Α-ΩA-Z]?)\s+του\s+'
        r'|(?P<table>Πίνακα|Πίνακας)\s+(?P<table_num>[Α-Ω])\s+του\s+'
        r'|(?P<item>παράγραφο|παραγράφου)\s+(?P<item_num>\d+[Α-Ω]?)\s+του\s+)?'
        r'(?P<ord>(?:Δέκατ(?:ο|ου)\s+)?(?:%s)(?:ο|ου))\s+(?:Παράρτημα|Παραρτήματος)'
        r'(?![\wά-ώ])' % ordinal_pattern(EL_ORDINALS)
    ),
    external=(
        r'^(?:\s+(?:του|της|των)\s+(?!παρόντος|άρθρ|εδαφ|παραγρ|υποπαραγρ)'
        r'|\s+αυτού|\s+αυτής|\s+αυτών)'
    ),
    ordinals=EL_ORDINALS,
    bare=r'(?<![\wά-ώ])(?:εδάφιο|εδαφίου|εδάφια|εδαφίων)\s+\(?\d',
)

RU = Language(
    article_word=r'(?<![\wё])[Сс]тат(?:ь(?:я|и|е|ю|ёй|ей|ях|ями|ям)|ей)\s+',
    number=r'\d+(?:ΣΤ|СТ|[Α-ΩA-ZА-Я]){0,2}',
    separators=r',?\s+(?:и|или|либо|по|до)\s+|,\s+|\s*[–—-]\s*',
    paragraph_first=(
        r'(?<![\wё])(?:пункт|подпункт|абзац)\w*'
        r'\s+((?:%s)+)\s+статьи\s+(\d+(?:ΣΤ|СТ|[Α-ΩA-ZА-Я]){0,2})%s(?!\()' % (SUB, END)
    ),
    schedule=(
        r'(?<![\wё])(?:(?P<part>[Чч]аст[ьиюей]{1,2})\s+(?P<part_num>[IVXΙΧ]+[A-ZΑ-ΩА-Я]?)\s+'
        r'|(?P<table>[Тт]аблиц[аыеуой]{1,2})\s+(?P<table_num>[А-ЯΑ-ΩA-Z])\s+'
        r'|(?P<item>(?:пункт|параграф)[аеуом]{0,2})\s+(?P<item_num>\d+[А-ЯΑ-ΩA-Z]?)\s+)?'
        r'(?P<ord>(?:%s)(?:ое|ого|ому|ым|ом|ье|ьего|ьему|ьим|ьем))\s+'
        r'[Пп]риложени(?:е|я|ю|ем|и)(?![\wё])' % ordinal_pattern(RU_ORDINALS)
    ),
    external=(
        r'^\s+(?!настоящего)(?:[А-ЯЁΑ-ΩA-Z]|закона|кодекса|директивы|регламента|указа'
        r'|постановления|упомянутого|указанного|этого|данного|его|её|их'
        r'|к\s+(?!настоящему)[А-ЯЁа-яё])'
    ),
    ordinals=RU_ORDINALS,
    external_before=r'(?<![\wё])(?:его|её|их|этого|указанного|упомянутого|данного)\s+$',
    bare=r'(?<![\wё])пункт\w*\s+\(\d',
)


def number_token(raw):
    """«11Α», «13СТ», «(1Α)», «(α)» → часть eid; None — не разбирается однозначно."""
    raw = raw.strip('()')
    if CYRILLIC_LETTER_RE.search(raw):
        # строчные кириллические буквы в скобках — переложение (α), (β)…: не угадываем
        return None
    raw = raw.replace('СТ', 'ΣΤ').translate(CYRILLIC_TO_GREEK)
    return numbering.eid_token(numbering.normalize_number(raw))


def ordinal_value(text, ordinals):
    words = text.split()
    value = 0
    if len(words) == 2:
        # «Δέκατο Τρίτο» — десять и три
        value = 10
        text = words[1]
    for stem, number in ordinals:
        if text.startswith(stem):
            return value + number
    return None


class Targets:
    """Фрагменты закона: поиск потомка по части номера."""

    def __init__(self, eids):
        self.eids = set(eids)
        self.children = {}
        for eid in self.eids:
            parent, _, last = eid.rpartition('.')
            token = last.split('_', 1)[-1]
            self.children.setdefault((parent, token), []).append(eid)

    def child(self, parent, token):
        found = self.children.get((parent, token), [])
        return found[0] if len(found) == 1 else None

    def resolve(self, top, subs):
        """(eid, сведено ли к предку) или (None, причина)."""
        if top not in self.eids:
            return None, 'цели нет'
        eid = top
        for raw in subs:
            token = number_token(raw)
            child = self.child(eid, token) if token else None
            if child is None:
                return eid, True
            eid = child
        return eid, False


class Marker:
    """Разметка абзацев одного текста; счётчики пропусков — на весь закон."""

    def __init__(self, targets, language):
        self.targets = targets
        self.language = language
        self.skipped = Counter()
        # {(uid фрагмента, редакция): [цели по порядку]} — заполняет сборщик
        self.found = {}

    def mark_text(self, paragraphs, own):
        """Абзацы (уже экранированные) → (абзацы с <ref>, цели по порядку)."""
        found = []
        marked = []
        for paragraph in paragraphs:
            plain = OWN_REF_RE.sub(r'\1', paragraph)
            spans = self.spans(plain, own)
            marked.append(self.insert(plain, spans, found))
        return marked, found

    def insert(self, text, spans, found):
        parts = []
        position = 0
        for start, end, eid in spans:
            found.append(eid)
            parts.append(text[position:start])
            parts.append('<ref id="r%d" to="%s">%s</ref>' % (len(found), eid, text[start:end]))
            position = end
        parts.append(text[position:])
        return ''.join(parts)

    def spans(self, text, own):
        """Неперекрывающиеся (начало, конец, eid) по порядку."""
        candidates = (
            self.paragraph_spans(text)
            + self.schedule_spans(text)
            + self.article_spans(text)
        )
        taken = []
        for start, end, eid in candidates:
            if any(start < other_end and other_start < end for other_start, other_end, _ in taken):
                continue
            if eid is None:
                taken.append((start, end, None))
                continue
            if own and (own == eid or own.startswith(eid + '.')):
                self.skipped['ссылка на себя или своего предка'] += 1
                taken.append((start, end, None))
                continue
            taken.append((start, end, eid))
        for match in self.language.bare_re.finditer(text):
            if not any(start <= match.start() < end for start, end, _ in taken):
                self.skipped['пункт без номера статьи'] += 1
        return sorted(item for item in taken if item[2] is not None)

    def external(self, text, end, start=None):
        if start is not None and self.language.external_before_re.search(text[:start]):
            return True
        return self.language.external_re.match(text[end:]) is not None

    def target(self, top, subs, start, end):
        eid, partial = self.targets.resolve(top, subs)
        if eid is None:
            self.skipped['цели нет в законе'] += 1
            return (start, end, None)
        if partial:
            self.skipped['пункта нет, ссылка на статью или приложение'] += 1
        return (start, end, eid)

    def paragraph_spans(self, text):
        spans = []
        for match in self.language.paragraph_re.finditer(text):
            if self.external(text, match.end(), match.start()):
                self.skipped['похоже на другой акт'] += 1
                spans.append((match.start(), match.end(), None))
                continue
            subs = re.findall(SUB, match.group(1))
            top = 'art_%s' % number_token(match.group(2))
            spans.append(self.target(top, subs, match.start(), match.end()))
        return spans

    def schedule_spans(self, text):
        spans = []
        for match in self.language.schedule_re.finditer(text):
            number = ordinal_value(match.group('ord'), self.language.ordinals)
            if number is None:
                continue
            if self.external(text, match.end(), match.start()):
                self.skipped['похоже на другой акт'] += 1
                spans.append((match.start(), match.end(), None))
                continue
            top = 'sch_%d' % number
            subs = []
            for name in ('part', 'table', 'item'):
                if match.group(name):
                    subs = [match.group(name + '_num')]
            spans.append(self.target(top, subs, match.start(), match.end()))
        return spans

    def article_spans(self, text):
        """Статьи с перечислениями: «άρθρα 8 και 9», «άρθρο 9(4), (5) ή (10)»."""
        spans = []
        language = self.language
        for word in language.article_re.finditer(text):
            items = []
            position = word.end()
            start = word.start()
            top = None
            while True:
                item = language.item_re.match(text, position)
                if item is None:
                    break
                top = 'art_%s' % number_token(item.group(1))
                items.append((start, item.end(), top, re.findall(SUB, item.group(2))))
                position = item.end()
                # «9(4), (5) ή (10)» — пункты той же статьи
                while True:
                    separator = language.separator_re.match(text, position)
                    if separator is None:
                        break
                    subs = language.subs_re.match(text, separator.end())
                    if subs is None:
                        break
                    items.append((
                        separator.end(), subs.end(), top, re.findall(SUB, subs.group(0)),
                    ))
                    position = subs.end()
                separator = language.separator_re.match(text, position)
                if separator is None or not language.item_re.match(text, separator.end()):
                    break
                position = separator.end()
                start = position
            if not items:
                continue
            if self.external(text, position, word.start()):
                self.skipped['похоже на другой акт'] += len(items)
                spans.extend((item_start, end, None) for item_start, end, _, _ in items)
                continue
            for item_start, end, item_top, subs in items:
                spans.append(self.target(item_top, subs, item_start, end))
        return spans


def align_ids(text, el_targets):
    """id в переводе — как у ссылки el на ту же цель (n-я по счёту), иначе новые."""
    seen = Counter()
    extra = [len(el_targets)]
    el_ids = {}
    counts = Counter()
    for index, eid in enumerate(el_targets, start=1):
        counts[eid] += 1
        el_ids[(eid, counts[eid])] = index

    def replace(match):
        eid = match.group(2)
        seen[eid] += 1
        index = el_ids.get((eid, seen[eid]))
        if index is None:
            extra[0] += 1
            index = extra[0]
        return '<ref id="r%d" to="%s">' % (index, eid)

    return re.sub(r'<ref id="r(\d+)" to="([^"]*)">', replace, text)


TEXT_RE = re.compile(r'(<text\b[^>]*>)(.*?)(</text>)', re.S)
P_RE = re.compile(r'(<p>)(.*?)(</p>)', re.S)
ATTR_RE = re.compile(r'\b(fragment|version)="([^"]*)"')


def strip_own_refs(text):
    return OWN_REF_RE.sub(r'\1', text)


def mark_translation(path, targets, eid_by_uid, el_marks):
    """Разметка ссылок в файле перевода; текст вне тегов <ref> не меняется.

    el_marks: {(uid, редакция): [цели el]}.
    Возвращает (пропуски, расхождения с el, число ссылок)."""
    source = path.read_text(encoding='utf-8')
    marker = Marker(targets, RU)
    differences = []
    total = [0]

    def mark_block(match):
        attrs = dict(ATTR_RE.findall(match.group(1)))
        key = (attrs.get('fragment'), attrs.get('version'))
        own = eid_by_uid.get(key[0], '')
        found_all = []

        def mark_paragraph(paragraph):
            marked, found = marker.mark_text([paragraph.group(2)], own)
            shifted = renumber(marked[0], len(found_all))
            found_all.extend(found)
            return paragraph.group(1) + shifted + paragraph.group(3)

        body = P_RE.sub(mark_paragraph, match.group(2))
        total[0] += len(found_all)
        el_targets = el_marks.get(key, [])
        if key in el_marks and Counter(found_all) != Counter(el_targets):
            differences.append((own, key[1], sorted(el_targets), sorted(found_all)))
        return match.group(1) + align_ids(body, el_targets) + match.group(3)

    result = TEXT_RE.sub(mark_block, source)
    if strip_own_refs(result) != strip_own_refs(source):
        raise ValueError('%s: разметка изменила текст перевода' % path)
    if result != source:
        path.write_text(result, encoding='utf-8')
    return marker.skipped, differences, total[0]


def renumber(text, offset):
    return re.sub(
        r'<ref id="r(\d+)"',
        lambda match: '<ref id="r%d"' % (int(match.group(1)) + offset),
        text,
    )
