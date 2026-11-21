"""Ссылки в текстах актов ЕС: «Article 4(1)», «point (g) of Article 17(1)», «Articles 33 to 37»,
«paragraph 2», «Annex I», «Article 262 of Directive 2006/112/EC»; по-гречески — «άρθρο 4
παράγραφος 1», «στοιχείο ζ) του άρθρου 17 παράγραφος 1», «άρθρα 33 έως 37», «παράρτημα I».

Цель внутри известного акта проверяется по его адресам; если точного адреса нет, берётся
ближайший существующий предок. Цель в акте, которого нет в базе (директива 2006/112), строится
по номерам без проверки и остаётся неразрешённой, пока акт не загружен.
Ссылки ставятся на акты из EU_ACTS; на прочие («Article 24 of Regulation (EU) 2017/1939») — нет.
"""

import re
from dataclasses import dataclass

from nomos.sources.eurlex.formex import strip_accents

DIRECTIVE = 'eu/dir/2006/112'

# акты ЕС базы: номер в обозначении → адрес; ссылки «of Regulation (EU) No 904/2010» ставятся
EU_ACTS = {
    '2006/112': DIRECTIVE,
    '904/2010': 'eu/reg/2010/904',
    '282/2011': 'eu/reg/2011/282',
}

# греческие буквенные номера: α=1 … θ=9, ι=10, κ=20; στ=6
GREEK_VALUES = [
    ('στ', 6),
    ('α', 1),
    ('β', 2),
    ('γ', 3),
    ('δ', 4),
    ('ε', 5),
    ('ζ', 7),
    ('η', 8),
    ('θ', 9),
    ('ι', 10),
    ('κ', 20),
    ('λ', 30),
    ('μ', 40),
]

EN_NUM = r'\d+[a-z]{0,2}\b(?:\(\w{1,4}\))*'
EN_ARTICLE = re.compile(
    r'(?P<pre>points? \((?P<pnt>[a-z0-9]{1,4})\)(?:(?:, | and | or )\([a-z0-9]{1,4}\))*'
    r'(?: of)?(?: the \w+ subparagraph(?: of)?)? )?'
    r'(?P<word>Articles?) '
    r'(?P<list>' + EN_NUM + r'(?:(?:, | and | or | to )' + EN_NUM + r')*)'
    r'(?P<of> of (?:this Regulation|this Directive|Directive 2006/112/EC|that Directive'
    r'|(?:Council |Commission )?(?:Implementing )?(?:Directive|Regulation|Decision)'
    r'(?: \((?:EU|EC|EEC)\))?(?: No)? \d+/\d+(?:/\w+)?))?'
)
EN_PARAGRAPH = re.compile(
    r'\b(?P<word>paragraphs?) (?P<list>\d+[a-z]?(?:(?:, | and | or | to )\d+[a-z]?)*)\b'
)
EN_ANNEX = re.compile(r'\b(?P<word>Annex) (?P<list>[IVX]+)\b')

EL_LETTERS = r'[α-ω]{1,4}'
EL_NUM = (
    r'\d+(?:' + EL_LETTERS + r')?\b'
    r'(?: παράγραφο[ςι]? \d+(?:' + EL_LETTERS + r')?\b)?'
    r'(?: (?:στοιχείο|σημείο) (?:' + EL_LETTERS + r'\)|\d+))?'
)
EL_ARTICLE = re.compile(
    r'(?P<pre>στοιχεί(?:ο|α|ου|ων) (?P<pnt>' + EL_LETTERS + r')\)'
    r'(?:(?:, | και | ή )' + EL_LETTERS + r'\))* (?:του|των|στο|στα) )?'
    r'(?P<word>άρθρ(?:ο|ου|α|ων)) '
    r'(?P<list>' + EL_NUM + r'(?:(?:, | και | ή | έως )' + EL_NUM + r')*)'
    r'(?P<of> (?:του παρόντος κανονισμού|της παρούσας οδηγίας|της οδηγίας 2006/112/ΕΚ'
    r'|της εν λόγω οδηγίας|της οδηγίας αυτής|του εν λόγω κανονισμού|της εν λόγω απόφασης'
    r'|(?:του|της) (?:εκτελεστικού )?(?:κανονισμού|οδηγίας|απόφασης)'
    r'(?: \((?:ΕΕ|ΕΚ|ΕΟΚ)\))?(?: αριθ\.)? \d+/\d+(?:/\w+)?))?'
)
EL_PARAGRAPH = re.compile(
    r'(?<!\d )\b(?P<word>παράγραφ(?:ο|ος|ου|οι|ους|ων)) '
    r'(?P<list>\d+(?:' + EL_LETTERS + r')?'
    r'(?:(?:, | και | ή | έως )\d+(?:' + EL_LETTERS + r')?)*)\b'
)
EL_ANNEX = re.compile(r'\b(?P<word>(?:Π|π)αράρτημ(?:α|ατος)) (?P<list>[IVXΙΧ]+)\b')

SEPARATORS = re.compile(r', | and | or | to | και | ή | έως ')
TAG_RE = re.compile(r'(<[^>]+>)')


def greek_number(letters):
    """«ιγ» → 13, «στ» → 6."""
    total = 0
    rest = letters
    while rest:
        for letters_value, value in GREEK_VALUES:
            if rest.startswith(letters_value):
                total += value
                rest = rest[len(letters_value):]
                break
        else:
            return 0
    return total


def latin_letter(letters):
    """Греческий буквенный номер → латинская буква того же места: «ιγ» → m."""
    number = greek_number(letters)
    if not 0 < number <= 26:
        return letters
    return chr(ord('a') + number - 1)


def el_token(value):
    """«24α» → 24a, «ζ)» → g, «9» → 9."""
    value = value.strip().rstrip(')')
    match = re.fullmatch(r'(\d*)([α-ω]*)', value)
    if not match:
        return value.lower()
    digits, letters = match.groups()
    return digits + (latin_letter(letters) if letters else '')


@dataclass
class Target:
    act: str
    eid: str


class Linker:
    def __init__(self, lang, own_uid, known):
        """known: uid акта → множество адресов (для проверки целей)."""
        self.lang = lang
        self.own_uid = own_uid
        self.known = known
        self.found = []

    def mark(self, paragraphs, own_eid, default_act=None):
        """Абзацы со ссылками. default_act — акт, к которому относятся ссылки без «of …»."""
        default_act = default_act or self.own_uid
        counter = [0]
        marked = []
        for paragraph in paragraphs:
            parts = TAG_RE.split(paragraph)
            depth_ref = 0
            for index, part in enumerate(parts):
                if index % 2 == 1:
                    if part.startswith('<fn') or part.startswith('<table'):
                        depth_ref += 1
                    elif part.startswith('</fn') or part.startswith('</table'):
                        depth_ref -= 1
                    continue
                if depth_ref > 0 or not part.strip():
                    continue
                parts[index] = self.mark_text(
                    part, own_eid, default_act, counter,
                )
            marked.append(''.join(parts))
        return marked

    def mark_text(self, text, own_eid, default_act, counter):
        if self.lang == 'el':
            article_re, paragraph_re, annex_re = EL_ARTICLE, EL_PARAGRAPH, EL_ANNEX
        else:
            article_re, paragraph_re, annex_re = EN_ARTICLE, EN_PARAGRAPH, EN_ANNEX
        spans = []
        for match in article_re.finditer(text):
            act = self.article_act(match.group('of'), default_act)
            if act is None:
                continue
            spans.extend(self.article_spans(match, act))
        taken = [(start, end) for start, end, _ in spans]
        # «paragraph 2» в статье изменяющего акта относится к изменяемой статье: не ставится
        article = own_article(own_eid) if default_act == self.own_uid else ''
        for match in paragraph_re.finditer(text):
            if overlaps(match.span(), taken) or not article:
                continue
            if self.paragraph_of_article(text, match):
                continue
            for index, (start, end, token) in enumerate(self.list_items(match)):
                if index == 0:
                    start = match.start()
                if self.lang == 'el':
                    token = el_token(token)
                eid = '%s.par_%s' % (article, token)
                spans.append((start, end, Target(default_act, eid)))
        for match in annex_re.finditer(text):
            if overlaps(match.span(), taken):
                continue
            token = match.group('list').translate(str.maketrans('ΙΧ', 'IX'))
            spans.append((match.start(), match.end(), Target(default_act, 'anx_%s' % token)))
        spans.sort(key=lambda span: span[0])
        result = []
        position = 0
        for start, end, target in spans:
            if start < position:
                continue
            resolved = self.resolve(target)
            if resolved is None or points_to_self(resolved, own_eid):
                continue
            counter[0] += 1
            result.append(text[position:start])
            result.append('<ref id="r%d" to="%s">%s</ref>' % (
                counter[0], resolved, text[start:end],
            ))
            self.found.append(resolved)
            position = end
        result.append(text[position:])
        return ''.join(result)

    def article_act(self, of, default_act):
        if not of:
            return default_act
        plain = strip_accents(of.lower())
        if 'παροντοσ' in plain or 'this regulation' in plain or 'παρουσασ' in plain:
            return default_act
        if 'this directive' in plain:
            return default_act
        for number, uid in EU_ACTS.items():
            if re.search(r'\b%s\b' % re.escape(number), plain):
                return uid
        if 'that directive' in plain:
            return DIRECTIVE
        if 'οδηγιασ' in plain and ('εν λογω' in plain or 'αυτησ' in plain):
            return DIRECTIVE
        return None

    def article_spans(self, match, act):
        """Каждый номер в «Articles 33, 34 and 37(2)» — своя ссылка; первая — со словом."""
        items = list(self.list_items(match))
        spans = []
        point = match.group('pnt')
        for index, (start, end, token) in enumerate(items):
            eid = self.article_eid(token)
            if index == 0:
                start = match.start()
                if point and len(items) == 1:
                    point_token = el_token(point) if self.lang == 'el' else point
                    eid = '%s.pnt_%s' % (eid, point_token)
            if index == len(items) - 1 and match.group('of'):
                end = match.end('of')
            spans.append((start, end, Target(act, eid)))
        return spans

    def list_items(self, match):
        """(начало, конец, номер) каждого номера в группе list."""
        text = match.group('list')
        offset = match.start('list')
        position = 0
        for separator in SEPARATORS.finditer(text):
            yield offset + position, offset + separator.start(), text[position:separator.start()]
            position = separator.end()
        yield offset + position, offset + len(text), text[position:]

    def article_eid(self, token):
        """«17(1)(g)» → art_17.par_1.pnt_g; «17 παράγραφος 1 στοιχείο ζ)» → то же."""
        if self.lang == 'el':
            parts = re.findall(r'\d+[α-ω]*|[α-ω]+\)', token)
            numbers = [el_token(part) for part in parts]
        else:
            head = re.match(r'\d+[a-z]*', token).group(0)
            numbers = [head] + re.findall(r'\((\w+)\)', token)
        eid = 'art_%s' % numbers[0]
        if len(numbers) > 1:
            eid += '.par_%s' % numbers[1]
        for number in numbers[2:]:
            eid += '.pnt_%s' % number
        return eid

    def paragraph_of_article(self, text, match):
        """«paragraph 2 of Article 5» — уже часть ссылки на статью или на чужой акт."""
        after = text[match.end():match.end() + 40]
        if self.lang == 'el':
            return bool(re.match(r' (?:του|της) (?:άρθρου|κανονισμού|οδηγίας)', after))
        return bool(re.match(r' of (?:Article|Directive|Regulation|Decision)', after))

    def resolve(self, target):
        """Адрес цели: внутри акта — без uid; при неточном адресе — ближайший предок."""
        eids = self.known.get(target.act)
        eid = target.eid
        if eids is not None:
            if eid not in eids:
                eid = hidden_levels(eid, eids) or eid
            while eid and eid not in eids:
                eid = eid.rpartition('.')[0]
            if not eid:
                return None
        if target.act == self.own_uid:
            return eid
        return '%s#%s' % (target.act, eid)


def collapse(eid, kinds):
    """art_284.par_3.sub_1.pnt_a без уровней kinds (sub) → art_284.par_3.pnt_a."""
    return '.'.join(part for part in eid.split('.') if part.split('_')[0] not in kinds)


def hidden_levels(eid, eids):
    """Адрес с пропущенными в ссылке уровнями: «point (a) of Article 284(3)» —
    art_284.par_3.sub_1.pnt_a, «point (b) of Article 214» — art_214.par_1.pnt_b.

    Сначала пропускаются только подабзацы, затем и абзацы; берётся единственный кандидат."""
    for kinds in (('sub',), ('sub', 'par')):
        found = [item for item in eids if item != eid and collapse(item, kinds) == eid]
        if len(found) == 1:
            return found[0]
        if found:
            return None
    return None


def points_to_self(resolved, own_eid):
    """Ссылка фрагмента на себя или на своего предка не ставится."""
    return resolved == own_eid or own_eid.startswith(resolved + '.')


def own_article(eid):
    """art_17.par_1.pnt_a → art_17; вне статьи (приложение) — пусто."""
    head = eid.split('.')[0]
    return head if head.startswith('art_') else ''


def overlaps(span, taken):
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in taken)
