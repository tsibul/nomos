"""Ссылки на статьи и приложения закона → `<ref id="r1" to="eid">`.

Размечается только однозначное: номер статьи (с пунктами в скобках), «εδάφιο (2) του
άρθρου 6», приложение по порядковому числительному (с частью, таблицей или пунктом).
Упоминание, за которым идёт название другого акта («του περί … Νόμου», «Закона о …»),
не размечается. Цель — существующий фрагмент закона; пункт, которого нет, сводится
к ближайшему существующему предку, статья или приложение, которых нет, — не размечаются.
Ссылка фрагмента на себя или на своего предка не размечается.

Пункт без номера статьи («εδάφιο (1) πιο πάνω») — пункт своей статьи, если он в ней есть.

В изменяющем акте размечаются ссылки на базовый закон, `to="<uid закона>#<eid>"`:
с указанием «του βασικού νόμου», «του περί Φόρου Προστιθέμενης Αξίας Νόμου» и т. п.;
без указания акта — внутри кавычек «…» (вставляемый текст закона) и в актах, у которых
нет своих статей с этими номерами (ΚΔΠ: implicit).
"""

import re
from collections import Counter
from dataclasses import dataclass

from . import numbering

# разметка, которую ставит этот модуль; снимается перед повторной разметкой
OWN_REF_RE = re.compile(r'<ref id="r\d+" to="[^"]*">(.*?)</ref>', re.S)

# причины пропуска: другой акт; заглавное слово после упоминания — решает оригинал
FOREIGN = 'похоже на другой акт'
CAPITAL = 'заглавное слово после упоминания'
SUB = r'\([0-9A-Za-zΑ-Ωα-ωА-Яа-яё]{1,5}\)'
END = r'(?=[\s(,.;:·˙∙\u0387\u037e)»"\-—–]|$)'

# похожие кириллические буквы в номерах русского перевода → греческие;
# Б — передача греческой Β по звучанию («статья 13Б»)
CYRILLIC_TO_GREEK = str.maketrans('АБВГДЕЗКМНОРТХ', 'ΑΒΒΓΔΕΖΚΜΗΟΡΤΧ')
CYRILLIC_LETTER_RE = re.compile(r'[а-яё]')
# буквы подпунктов русского перевода по порядку (без ё, й, ъ, ы, ь): n-я буква —
# n-й подпункт оригинала, (в) — (γ), (е) — (στ), (к) — (ι)
RU_POINT_LETTERS = 'абвгдежзиклмнопрстуфхцчшщэюя'

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
    # слово перед пунктом без статьи: «εδάφιο (1) πιο πάνω» — пункт своей статьи
    bare_word: str = r'(?!)'
    # после такого пункта уточнение — другая статья или акт: не размечается
    bare_qualified: str = r'(?!)'
    # указание базового закона после упоминания (в изменяющем акте)
    base: str = r'(?!)'
    # «του παρόντος», «настоящего»: в изменяющем акте вне кавычек — сам этот акт
    this_act: str = r'(?!)'
    # после упоминания слово с заглавной: другой акт, если в оригинале нет той же ссылки
    capital: str = r'(?!)'
    # ΚΔΠ: «Κανονισμοί δυνάμει των άρθρων 10, 78…» — статьи уполномочивающего закона
    enabling_before: str = r'(?!)'
    # «статьи 3 Закона, как…» — без названия: базовый ли закон, решает оригинал
    bare_law: str = r'(?!)'
    # пункты своей части приложения без её названия (Marker.inner_spans)
    inner_paragraphs: str = r'(?!)'
    inner_points: str = r'(?!)'

    def __post_init__(self):
        self.article_re = re.compile(self.article_word)
        self.item_re = re.compile(r'(%s)%s((?:%s)*)' % (self.number, END, SUB))
        self.separator_re = re.compile(self.separators)
        self.subs_re = re.compile(r'(?:%s)+' % SUB)
        self.paragraph_re = re.compile(self.paragraph_first)
        self.schedule_re = re.compile(self.schedule)
        self.external_re = re.compile(self.external)
        self.external_before_re = re.compile(self.external_before)
        self.bare_word_re = re.compile(self.bare_word)
        self.bare_qualified_re = re.compile(self.bare_qualified)
        self.base_re = re.compile(self.base)
        self.this_act_re = re.compile(self.this_act)
        self.capital_re = re.compile(self.capital)
        self.enabling_before_re = re.compile(self.enabling_before)
        self.bare_law_re = re.compile(self.bare_law)
        self.inner_paragraphs_re = re.compile(self.inner_paragraphs)
        self.inner_points_re = re.compile(self.inner_points)


# пункт в перечислении внутри приложения: «1(1)(α)», «(β)», «(2)(α)»
INNER_ITEM_RE = re.compile(r'(\d+[Α-Ω]?)((?:%s)*)|((?:%s)+)' % (SUB, SUB))
INNER_SEPARATORS = r'(?:,?\s+(?:και|ή|μέχρι|έως)\s+|,\s+)'


def ordinal_pattern(ordinals):
    return '|'.join(stem for stem, _ in ordinals)


EL = Language(
    article_word=r'(?<![\wά-ώ])(?:άρθρο|άρθρου|άρθρα|άρθρων)\s+',
    number=r'\d+(?:ΣΤ|[Α-ΩA-Z]){0,2}',
    separators=r',?\s+(?:και|ή|μέχρι|έως)\s+|,\s+',
    paragraph_first=(
        r'(?<![\wά-ώ])(?:εδάφιο|εδαφίου|παράγραφο|παραγράφου|υποπαράγραφο|υποπαραγράφου)'
        # «του» может не быть: «εδαφίου (3) άρθρου 3» (117(I), ст. 3G)
        r'\s+((?:%s)+)\s+(?:του\s+)?άρθρου\s+(\d+(?:ΣΤ|[Α-ΩA-Z]){0,2})%s(?!\()' % (SUB, END)
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
    bare_word=r'(?<![\wά-ώ])(?:εδάφιο|εδαφίου|εδάφια|εδαφίων)\s+',
    bare_qualified=r'^\s+(?:(?:του|της|των)\s+(?!παρόντος\s+άρθρου)|αυτού|αυτής)',
    base=(
        r'^\s*(?:του|των)\s+(?:βασικ(?:ού|ών)\s+[Νν]όμ(?:ου|ων)'
        r'|[Νν]όμου(?![\wά-ώ])(?!\s+του\s+\d)'
        r'|περί\s+Φόρου\s+Προστιθέμενης\s+Αξίας\s+[Νν]όμ(?:ου|ων)(?![\wά-ώ])'
        r'(?!\s*\(Τροποποιητικ))'
    ),
    this_act=r'^\s+(?:(?:του|της)\s+)?παρόντος',
    enabling_before=r'(?<![\wά-ώ])Κανονισμοί\s+δυνάμει\s+(?:των|του)\s+$',
    # «παραγράφους 1(1)(α) και (β) και (2)(α) και (β) πιο πάνω» (95(Ι), Πρώτο Παράρτημα)
    inner_paragraphs=(
        r'(?<![\wά-ώ])(?:παράγραφο|παραγράφου|παραγράφους|παραγράφων)\s+'
        r'(\d+[Α-Ω]?(?:%s)*(?:%s(?:\d+[Α-Ω]?)?(?:%s)+)*)'
        r'\s+πιο\s+(?:πάνω|κάτω)(?![\wά-ώ])' % (SUB, INNER_SEPARATORS, SUB)
    ),
    # «υποπαραγράφους (α) έως (ε) της παραγράφου (2) του σημείου 5»
    inner_points=(
        r'(?<![\wά-ώ])(?:υποπαράγραφο|υποπαραγράφου|υποπαραγράφους|υποπαραγράφων)\s+'
        r'(%s(?:%s%s)*)\s+της\s+(παραγράφου\s+(%s)\s+του\s+σημείου\s+(\d+[Α-Ω]?))%s'
        % (SUB, INNER_SEPARATORS, SUB, SUB, END)
    ),
)

RU = Language(
    # «пункта 20(1)» — номер статьи сразу после слова «пункт», затем номер пункта
    article_word=(
        r'(?<![\wё])(?:[Сс]тат(?:ь(?:я|и|е|ю|ёй|ей|ях|ями|ям)|ей)'
        r'|[Пп]ункт\w*(?=\s+\d+(?:ΣΤ|СТ|[Α-ΩA-ZА-Я]){0,2}\(\d))\s+'
    ),
    number=r'\d+(?:ΣΤ|СТ|[Α-ΩA-ZА-Я]){0,2}',
    separators=r',?\s+(?:и|или|либо|по|до)\s+|,\s+|\s*[–—-]\s*',
    paragraph_first=(
        r'(?<![\wё])(?:[Пп]ункт|[Пп]одпункт|[Аа]бзац)\w*'
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
        r'^\s+(?!настоящего)(?:[Α-ΩA-Z]'
        r'|(?:Закон|Кодекс|Директив|Регламент|Указ|Постановлени|Конвенци|Правил|Договор'
        r'|Соглашени|Протокол)[а-яё]*(?![\wё])'
        r'|закона|кодекса|директивы|регламента|указа'
        r'|постановления|упомянутого|указанного|этого|данного|его|её|их|т(?:ого|ой)\s+же'
        r'|к\s+(?!настоящему)[А-ЯЁа-яё])'
    ),
    ordinals=RU_ORDINALS,
    external_before=r'(?<![\wё])(?:его|её|их|этого|указанного|упомянутого|данного)\s+$',
    bare_word=r'(?<![\wё])[Пп]ункт\w*\s+',
    # заглавная — название другого акта («ΚΔΠ», «Ν.»), но не латинская аббревиатура
    # организации из 2+ букв («HIO», «NPE»)
    bare_qualified=(
        r'^\s*(?:(?:,\s*)?стат(?:ьи|ей|ье|ья)(?![\wё])|(?![A-Z]{2,}(?![\wё]))[A-ZΑ-Ω]'
        r'|(?:Закона|Законов|Директивы|Регламента|Кодекса|Правил|Постановления|Указа'
        r'|Конвенции|закона|кодекса|директивы|регламента|указа|постановления'
        r'|упомянутого|указанного|этого|данного|его|её|их|той\s+же|того\s+же)(?![\wё]))'
    ),
    base=(
        r'^\s*(?:(?:базового|основного)\s+[Зз]акона|к\s+(?:базовому|основному)\s+[Зз]акону'
        r'|к\s+[Зз]акону(?![\wё])'
        r'|[Зз]акон(?:а|ов)\s+о\s+налоге\s+на\s+добавленную\s+стоимость(?![\wё])'
        r'(?!\s*\(изменя))'
    ),
    this_act=r'^\s+настоящ\w+',
    # «статьи 5 Части…» — другое; «статьи 33 Генеральный директор» — свой закон
    capital=r'^\s+[А-ЯЁ]',
    bare_law=r'^\s+[Зз]акона(?=\s*[,;:.)]|\s+(?:как|в\s+редакции)(?![\wё]))',
)


def number_token(raw):
    """«11Α», «13СТ», «(1Α)», «(α)» → часть eid; None — не разбирается однозначно."""
    raw = raw.strip('()')
    if CYRILLIC_LETTER_RE.search(raw):
        return cyrillic_point_token(raw)
    raw = raw.replace('СТ', 'ΣΤ').translate(CYRILLIC_TO_GREEK)
    return numbering.eid_token(numbering.normalize_number(raw))


def cyrillic_point_token(raw):
    """«в» → «g» (γ), «бб» → «bb» (ββ): буква подпункта перевода по её месту в алфавите;
    None — не одна буква и не удвоенная из первых девяти."""
    if raw[0] not in RU_POINT_LETTERS or raw != raw[0] * len(raw):
        return None
    value = RU_POINT_LETTERS.index(raw[0]) + 1
    if len(raw) > 2 or (len(raw) == 2 and value > 9):
        return None
    return numbering.eid_token(numbering.greek_numeral(value) * len(raw))


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
    """Разметка абзацев одного текста; счётчики пропусков — на весь акт.

    act — uid базового закона, если размечается изменяющий акт (цели — в законе);
    implicit — упоминание без указания акта тоже ссылка на базовый закон (ΚΔΠ);
    mentions — упоминания других актов (acts.Mentions): ссылка на акт целиком."""

    def __init__(self, targets, language, act='', implicit=False, mentions=None):
        self.targets = targets
        self.language = language
        self.act = act
        self.implicit = implicit
        self.skipped = Counter()
        # {(uid фрагмента, редакция): [цели по порядку]} — заполняет сборщик
        self.found = {}
        # глубина кавычек «…» в начале текущего абзаца
        self.depth = 0
        # цели ссылок оригинала в этом тексте — для перевода (mark_translation)
        self.agreed = set()
        self.mentions = mentions
        if mentions is not None:
            mentions.skipped = self.skipped

    def mark_text(self, paragraphs, own):
        """Абзацы одного текста (уже экранированные) → (абзацы с <ref>, цели по порядку)."""
        found = []
        marked = []
        self.depth = 0
        for paragraph in paragraphs:
            plain = OWN_REF_RE.sub(r'\1', paragraph)
            spans = self.spans(plain, own)
            marked.append(self.insert(plain, spans, found))
            self.depth = max(0, self.depth + plain.count('«') - plain.count('»'))
        return marked, found

    def quoted(self, text, position):
        return self.depth + text.count('«', 0, position) - text.count('»', 0, position) > 0

    def insert(self, text, spans, found):
        parts = []
        position = 0
        for start, end, eid in spans:
            # «uid» акта целиком (из mentions) — как есть
            target = '%s#%s' % (self.act, eid) if self.act and '/' not in eid else eid
            found.append(target)
            parts.append(text[position:start])
            parts.append('<ref id="r%d" to="%s">%s</ref>' % (len(found), target, text[start:end]))
            position = end
        parts.append(text[position:])
        return ''.join(parts)

    def spans(self, text, own):
        """Неперекрывающиеся (начало, конец, eid) по порядку."""
        candidates = (
            (self.mentions.spans(text, self.agreed) if self.mentions is not None else [])
            + self.paragraph_spans(text)
            + self.schedule_spans(text)
            + self.article_spans(text)
            + self.inner_spans(text, own)
        )
        taken = []
        for start, end, eid in candidates:
            if not self.overlaps(taken, start, end):
                self.take(taken, start, end, eid, own)
        # пункты без статьи — после всего остального: «εδάφιο (2) του άρθρου 6» уже взято
        for start, end, eid, reason in self.bare_spans(text, own):
            if self.overlaps(taken, start, end):
                continue
            if eid is None:
                self.skipped[reason] += 1
                taken.append((start, end, None))
                continue
            self.take(taken, start, end, eid, own)
        return sorted(item for item in taken if item[2] is not None)

    @staticmethod
    def overlaps(taken, start, end):
        return any(start < other_end and other_start < end for other_start, other_end, _ in taken)

    def take(self, taken, start, end, eid, own):
        # в изменяющем акте own — его собственный фрагмент, цель — в законе
        if eid is not None and own and not self.act and (
            own == eid or own.startswith(eid + '.')
        ):
            self.skipped['ссылка на себя или своего предка'] += 1
            eid = None
        taken.append((start, end, eid))

    def foreign(self, text, start, end):
        """Почему упоминание не размечается (другой акт), или None — размечается."""
        if not self.act:
            return self.external(text, end, start)
        tail = text[end:]
        if self.language.base_re.match(tail):
            return None
        quoted = self.quoted(text, start)
        if self.language.this_act_re.match(tail):
            return None if quoted else 'ссылка на сам изменяющий акт'
        reason = self.external(text, end, start)
        if reason:
            return reason
        if self.implicit and self.language.enabling_before_re.search(text[:start]):
            return FOREIGN
        if quoted or self.implicit:
            return None
        return 'акт не указан'

    def bare_spans(self, text, own):
        """(начало, конец, eid или None, причина): «εδάφιο (1)», «εδάφια (1) και (2)»."""
        spans = []
        language = self.language
        article = own.split('.')[0] if own else ''
        for word in language.bare_word_re.finditer(text):
            items = []
            position = word.end()
            start = word.start()
            while True:
                subs = language.subs_re.match(text, position)
                if subs is None or not subs.group(0)[1].isdigit():
                    break
                items.append((start, subs.end(), re.findall(SUB, subs.group(0))))
                separator = language.separator_re.match(text, subs.end())
                if separator is None:
                    break
                position = separator.end()
                start = position
            if not items:
                continue
            end = items[-1][1]
            reason = None
            if self.act or not article.startswith('art_'):
                reason = 'пункт без номера статьи'
            elif language.bare_qualified_re.match(text[end:]) or (
                language.external_before_re.search(text[:word.start()])
            ):
                reason = 'пункт с уточнением (другая статья или акт)'
            for item_start, item_end, subs in items:
                eid = None
                item_reason = reason
                if reason is None:
                    eid, _ = self.targets.resolve(article, subs)
                    if eid is None:
                        # своего текста нет (изменяющий акт без базового закона в базе)
                        item_reason = 'цели нет в законе'
                    elif eid == article:
                        # первого пункта в своей статье нет — не размечается
                        eid = None
                        item_reason = 'пункта нет в своей статье'
                spans.append((item_start, item_end, eid, item_reason))
        return spans

    def external(self, text, end, start=None):
        """Причина «другой акт», CAPITAL — решает оригинал, или None."""
        if start is not None and self.language.external_before_re.search(text[:start]):
            return FOREIGN
        if self.language.bare_law_re.match(text[end:]):
            return CAPITAL
        if self.language.external_re.match(text[end:]):
            return FOREIGN
        if self.language.capital_re.match(text[end:]):
            return CAPITAL
        return None

    def agreed_target(self, top, subs):
        """Цель найдена точно и есть среди ссылок оригинала этого текста."""
        eid, partial = self.targets.resolve(top, subs)
        # сведённое к предку («статьи 14, или (и)») не подтверждается оригиналом
        if eid is None or partial:
            return False
        return ('%s#%s' % (self.act, eid) if self.act else eid) in self.agreed

    def checked(self, reason, top, subs, start, end):
        """Упоминание с причиной пропуска (или без) → (начало, конец, eid или None)."""
        if reason == CAPITAL:
            reason = None if self.agreed_target(top, subs) else FOREIGN
        if reason:
            self.skipped[reason] += 1
            return (start, end, None)
        return self.target(top, subs, start, end)

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
            reason = self.foreign(text, match.start(), match.end())
            subs = re.findall(SUB, match.group(1))
            top = 'art_%s' % number_token(match.group(2))
            spans.append(self.checked(reason, top, subs, match.start(), match.end()))
        return spans

    def inner_spans(self, text, own):
        """Пункты своей части приложения без её названия (95(Ι), Πρώτο Παράρτημα):
        «παραγράφους 1(1)(α) και (β) και (2)(α) πιο πάνω» — 1(1)(α), 1(1)(β), 1(2)(α);
        «υποπαραγράφους (α) έως (ε) της παραγράφου (2) του σημείου 5» — 5(2)(α), 5(2)(ε).

        Часть — то, что выше параграфов текущего фрагмента (`sch_1.prt_I.sct_1`)."""
        if self.act or not own.startswith('sch_') or '.par_' not in own:
            return []
        root = own.split('.par_')[0]
        spans = []
        for match in self.language.inner_paragraphs_re.finditer(text):
            if self.external(text, match.end(), match.start()):
                continue
            previous = None
            items = INNER_ITEM_RE.finditer(text, match.start(1), match.end(1))
            for item in items:
                if item.group(1):
                    path = [item.group(1)] + re.findall(SUB, item.group(2))
                else:
                    path = continued(previous, re.findall(SUB, item.group(3)))
                if path is None:
                    break
                previous = path
                spans.append(self.inner_target(root, path, item.start(), item.end()))
        for match in self.language.inner_points_re.finditer(text):
            if self.external(text, match.end(), match.start()):
                continue
            paragraph = [match.group(4), match.group(3)]
            for item in re.finditer(SUB, match.group(1)):
                start = match.start(1) + item.start()
                end = match.start(1) + item.end()
                spans.append(self.inner_target(root, paragraph + [item.group()], start, end))
            spans.append(
                self.inner_target(root, paragraph, match.start(2), match.end(2))
            )
        return spans

    def inner_target(self, root, path, start, end):
        token = number_token(path[0])
        top = self.targets.child(root, token) if token else None
        if top is None:
            self.skipped['пункта нет в своей части приложения'] += 1
            return (start, end, None)
        eid, partial = self.targets.resolve(top, path[1:])
        if partial:
            # без названия части предок вместо пункта не подтверждён ничем: не размечается
            self.skipped['пункта нет в своей части приложения'] += 1
            return (start, end, None)
        return (start, end, eid)

    def schedule_spans(self, text):
        spans = []
        for match in self.language.schedule_re.finditer(text):
            number = ordinal_value(match.group('ord'), self.language.ordinals)
            if number is None:
                continue
            reason = self.foreign(text, match.start(), match.end())
            top = 'sch_%d' % number
            subs = []
            for name in ('part', 'table', 'item'):
                if match.group(name):
                    subs = [match.group(name + '_num')]
            spans.append(self.checked(reason, top, subs, match.start(), match.end()))
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
            reason = self.foreign(text, word.start(), position)
            for item_start, end, item_top, subs in items:
                spans.append(self.checked(reason, item_top, subs, item_start, end))
        return spans


def sub_kind(raw):
    """«(1)» — цифра, «(i)» — римская, «(α)» — буква."""
    inner = raw.strip('()')
    if inner[0].isdigit():
        return 'digit'
    if set(inner) <= set('ivx'):
        return 'roman'
    return 'letter'


def continued(previous, subs):
    """Продолжение перечисления: «1(1)(α) και (β)» — (β) вместо (α); «… και (2)(α)» —
    (2) вместо (1). Уровень — первый в предыдущем пункте того же вида; None — не найден."""
    if previous is None or not subs:
        return None
    kind = sub_kind(subs[0])
    for level in range(1, len(previous)):
        if sub_kind(previous[level]) == kind:
            return previous[:level] + subs
    return None


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


# «<text … />» (запись только с заголовком) — не начало текста: иначе тело следующей
# записи размечается по её ключу и целям el
TEXT_RE = re.compile(r'(<text\b[^>]*(?<!/)>)(.*?)(</text>)', re.S)
P_RE = re.compile(r'(<p>)(.*?)(</p>)', re.S)
ATTR_RE = re.compile(r'\b(fragment|version)="([^"]*)"')


def strip_own_refs(text):
    return OWN_REF_RE.sub(r'\1', text)


def mark_translation(
    path, targets, eid_by_uid, el_marks, act='', implicit=False, mentions=None,
):
    """Разметка ссылок в файле перевода; текст вне тегов <ref> не меняется.

    el_marks: {(uid, редакция): [цели el]}; act, implicit, mentions — как у Marker
    (mentions — для русского текста).
    Возвращает (пропуски, расхождения с el, число ссылок)."""
    source = path.read_text(encoding='utf-8')
    marker = Marker(targets, RU, act, implicit, mentions)
    differences = []
    total = [0]

    def mark_block(match):
        attrs = dict(ATTR_RE.findall(match.group(1)))
        key = (attrs.get('fragment'), attrs.get('version'))
        own = eid_by_uid.get(key[0], '')
        # абзацы текста размечаются вместе: кавычки «…» бывают на несколько абзацев
        paragraphs = [item.group(2) for item in P_RE.finditer(match.group(2))]
        el_targets = el_marks.get(key, [])
        marker.agreed = set(el_targets)
        marked, found_all = marker.mark_text(paragraphs, own)
        replaced = iter(marked)
        body = P_RE.sub(
            lambda item: item.group(1) + next(replaced) + item.group(3),
            match.group(2),
        )
        total[0] += len(found_all)
        if key in el_marks and Counter(found_all) != Counter(el_targets):
            differences.append((own, key[1], sorted(el_targets), sorted(found_all)))
        return match.group(1) + align_ids(body, el_targets) + match.group(3)

    result = TEXT_RE.sub(mark_block, source)
    if strip_own_refs(result) != strip_own_refs(source):
        raise ValueError('%s: разметка изменила текст перевода' % path)
    if result != source:
        path.write_text(result, encoding='utf-8')
    return marker.skipped, differences, total[0]

