"""Разбор сводного текста кипрского закона с cylaw.org (PDF страницы full.html).

Иерархия строится по виду номера, а не по отступу: отступы в приложениях непостоянны.
Тело закона: статья `N.` → εδάφιο `(N)` = par → `(α)` = pnt → `(i)` = pnt.
Приложение: пункт `N.` = par → `(N)` = sub → `(α)` = pnt → `(i)` = pnt.
Абзац без номера после нумерованных детей — отдельный фрагмент `sub_K` (2.2 модели),
до детей — часть текста родителя.
"""

import re

from . import layout, numbering
from .tree import Node

HEADER_RE = re.compile(r'^\s*(https://www\.cylaw\.org/|\d+/\d+/\d+, \d+:\d+ [AP]M)')
LAW_NUMBER_RE = re.compile(r'^\d+(\([IΙ]\))?/\d{4}$')
CORRIGENDUM_RE = re.compile(r'ΔΙΟΡΘ\.[^0-9]*\d+')

TOP_RE = re.compile(
    r'^(?P<num>\d+(?:[Α-ΩA-Z]{0,3}|\([Α-Ω]\)))\s?(?:\.\s*(?:[-—–]+\s*)?|[-—–]\s*(?=\())'
    r'(?:\((?P<sub>\d+[Α-ΩA-Z]{0,3})\)\s*)?(?P<rest>.*)$'
)
ITEM_RES = [
    ('arabic', re.compile(r'^\((?P<num>\d+[Α-ΩA-Z]{0,3})\)\s*(?P<rest>.*)$')),
    ('greek', re.compile(r'^\((?P<num>[α-ω]{1,3})\)\s*(?P<rest>.*)$')),
    ('roman', re.compile(r'^\((?P<num>[ivx]{1,5})\)\s*(?P<rest>.*)$')),
]
LEVELS = {
    'top': 0,
    'arabic': 1,
    'greek': 2,
    'roman': 3,
}
BODY_KINDS = {
    'top': 'art',
    'arabic': 'par',
    'greek': 'pnt',
    'roman': 'pnt',
}
SCHEDULE_KINDS = {
    'top': 'par',
    'arabic': 'sub',
    'greek': 'pnt',
    'roman': 'pnt',
}

SCHEDULE_ORDINALS = [
    'ΠΡΩΤΟ',
    'ΔΕΥΤΕΡΟ',
    'ΤΡΙΤΟ',
    'ΤΕΤΑΡΤΟ',
    'ΠΕΜΠΤΟ',
    'ΕΚΤΟ',
    'ΕΒΔΟΜΟ',
    'ΟΓΔΟΟ',
    'ΕΝΑΤΟ',
    'ΔΕΚΑΤΟ',
    'ΕΝΔΕΚΑΤΟ',
    'ΔΩΔΕΚΑΤΟ',
    'ΔΕΚΑΤΟ ΤΡΙΤΟ',
    'ΔΕΚΑΤΟ ΤΕΤΑΡΤΟ',
    'ΔΕΚΑΤΟ ΠΕΜΠΤΟ',
]
SCHEDULE_RE = re.compile(
    r'^(?P<ord>(ΔΕΚΑΤΟ )?[Α-Ω]+) ΠΑΡΑ[Ρ]?Τ[Η]?ΜΑ$'
)
PART_RE = re.compile(r'^(ΜΕΡΟΣ|Μέρος)\s+(?P<num>[IVXΙΧΑ]+)\s*:?\s*(?P<rest>.*)$')
SECTION_RE = re.compile(r'^Τμήμα\s+(?P<num>\d+)\s*(?P<rest>.*)$')
TABLE_RE = re.compile(r'^Πίνακας\s+(?P<num>[Α-Ω])΄?\s*:\s*(?P<rest>.*)$')
REPEALED_RE = re.compile(r'^\[\s*Διαγράφηκε\s*\]\.?$')

HEADING_MAX = 110
EXCEPTION_START = 'Εξαιρούνται'
# колонка заголовков на полях
MARGIN = 8


def is_page_furniture(line):
    return bool(HEADER_RE.match(line))


def is_history(text):
    """Список изменяющих законов на полях статьи: «95(I)/2000 … 10(I)/2024»."""
    cleaned = CORRIGENDUM_RE.sub('', text).replace('Παρ. Ι(Ι), Ε.Ε.', '')
    tokens = cleaned.split()
    return bool(tokens) and all(LAW_NUMBER_RE.match(token) for token in tokens)


def is_group_heading(text):
    """Заголовок группы статей по центру: короткий, без конечной пунктуации."""
    if len(text) > HEADING_MAX or text[-1] in '.:;·∙˙,—-':
        return False
    return not TOP_RE.match(text) and not match_item(text)[0]


def opens_list(node):
    """Абзац, за которым следует его собственный список: кончается двоеточием или тире."""
    text = layout.NOTE_RE.sub('', node.text()).rstrip(' .')
    # «…τα ακόλουθα προϊόντα [Σ.Σ.: …].» — перечень без двоеточия
    return text.endswith((':', '—', '–', '-')) or 'ακόλουθα' in text[-40:]


def match_item(text):
    for name, regex in ITEM_RES:
        match = regex.match(text)
        if match:
            return name, match
    return None, None


def looks_like_heading(block, following):
    """Короткая строка на полях (левее текста) перед статьёй или пунктом приложения.

    Точка в конце допустима: заголовки кончаются сокращениями («κ.τ.λ.», «Φ.Π.Α.»)."""
    text = block.text
    if text[-1] in ':;·∙˙,—-' or text.startswith('Νοείται'):
        return False
    if following is not None and block.indent <= MARGIN and following.indent > MARGIN:
        return bool(TOP_RE.match(following.text))
    if len(text) > HEADING_MAX or text[-1] == '.':
        return False
    if match_item(text)[0] or TOP_RE.match(text):
        return False
    return following is not None and bool(TOP_RE.match(following.text))


class Parser:
    def __init__(self, act_uid):
        self.root = Node('act', token=act_uid)
        self.stack = []
        self.container = self.root
        self.pending_heading = []
        self.in_schedules = False
        self.schedule = None
        self.history = {}
        self.dropped = []

    def parse(self, lines):
        all_blocks = layout.blocks(lines, skip=is_page_furniture)
        start = next(
            index for index, block in enumerate(all_blocks) if block.text == 'Προοίμιο'
        )
        all_blocks = all_blocks[start + 1:]
        preamble = Node('cit', token='cit_1', heading='Προοίμιο')
        self.root.add(preamble)
        self.stack = [preamble]
        for index, block in enumerate(all_blocks):
            following = next(
                (later for later in all_blocks[index + 1:] if not is_history(later.text)),
                None,
            )
            self.feed(block, following)
        return self.root

    def feed(self, block, following):
        text = block.text
        if is_history(text):
            article = self.current_top()
            if article is not None:
                self.history[article.eid] = text.split()
            return
        if text == 'ΠΑΡΑΡΤΗΜΑΤΑ':
            self.in_schedules = True
            return
        if self.in_schedules and self.feed_schedule_heading(text, block):
            return
        if not self.in_schedules and block.indent >= 14 and PART_RE.match(text):
            self.open_part(text)
            return
        if not self.in_schedules and block.indent >= 25 and is_group_heading(text):
            self.open_group(text)
            return
        if looks_like_heading(block, following):
            self.pending_heading.append(text)
            return
        self.feed_text(block)

    def feed_schedule_heading(self, text, block):
        schedule = SCHEDULE_RE.match(text)
        if schedule and schedule.group('ord') in SCHEDULE_ORDINALS:
            number = SCHEDULE_ORDINALS.index(schedule.group('ord')) + 1
            if self.schedule is not None and self.schedule.num == str(number):
                return True
            self.schedule = Node(
                'sch',
                num=str(number),
                token='sch_%d' % number,
                heading=text,
                order_key='%04d' % number,
            )
            self.root.add(self.schedule)
            self.container = self.schedule
            self.stack = [self.schedule]
            self.pending_heading = []
            return True
        if self.schedule is None:
            return False
        part = PART_RE.match(text)
        if part and block.indent >= 14:
            node = Node('prt', num=part.group('num'), heading=text)
            self.open_container(node, self.schedule)
            return True
        section = SECTION_RE.match(text)
        if section and block.indent >= 14:
            parent = self.nearest('prt') or self.schedule
            if section.group('num') == '1':
                # «Τμήμα 1» введено заменой заголовка Μέρους (104(I)/2025, ст. 3(α), (γ)):
                # это часть заголовка Μέρους, пункты остаются его детьми.
                parent.heading = '%s %s' % (parent.heading, text)
                self.container = parent
                self.stack = [parent]
                return True
            node = Node('sct', num=section.group('num'), heading=text)
            self.open_container(node, parent)
            return True
        table = TABLE_RE.match(text)
        if table:
            node = Node('tbl', num=table.group('num'), heading=text)
            self.open_container(node, self.schedule)
            return True
        if block.indent >= 25 and not TOP_RE.match(text) and not self.container_has_items():
            self.container.heading = '%s %s' % (self.container.heading, text)
            return True
        if block.indent >= 25 and not TOP_RE.match(text) and len(text) <= HEADING_MAX:
            self.pending_heading.append(text)
            return True
        return False

    def container_has_items(self):
        return any(child.kind not in ('prt', 'sct', 'tbl') for child in self.container.children)

    def open_container(self, node, parent):
        parent.add(node)
        self.container = node
        self.stack = [node]
        self.pending_heading = []

    def open_part(self, text):
        part = PART_RE.match(text)
        node = Node('prt', num=part.group('num'), heading=text)
        self.root.add(node)
        self.container = node
        self.stack = [node]

    def open_group(self, text):
        part = self.nearest_in_root('prt')
        parent = part or self.root
        node = Node('sct', unnumbered=True, heading=text)
        parent.add(node)
        self.container = node
        self.stack = [node]

    def nearest(self, kind):
        node = self.container
        while node is not None:
            if node.kind == kind:
                return node
            node = node.parent
        return None

    def nearest_in_root(self, kind):
        for node in reversed(self.root.children):
            if node.kind == kind:
                return node
        return None

    def current_top(self):
        for node in reversed(self.stack):
            if node.kind == 'art':
                return node
        return None

    def kinds(self):
        return SCHEDULE_KINDS if self.in_schedules else BODY_KINDS

    def feed_text(self, block):
        text = block.text
        top = TOP_RE.match(text)
        if top and block.indent <= 14:
            node = self.open_item('top', top.group('num'), block)
            if top.group('sub'):
                node = self.open_item('arabic', '(%s)' % top.group('sub'), block)
            self.set_text(node, top.group('rest'))
            return
        if text.startswith('[Σ.Σ.') and len(self.stack) > 1:
            # отдельный абзац примечания источника относится к последнему пункту
            self.stack[-1].paragraphs.append(text)
            return
        name, match = match_item(text)
        if name == 'arabic' and self.continues_schedule_numbering(match.group('num')):
            # «(25)» вместо «25.» в Έκτο Παράρτημα: продолжение нумерации пунктов
            node = self.open_item('top', match.group('num'), block)
            name, match = match_item(match.group('rest'))
            if name:
                node = self.open_item(name, '(%s)' % match.group('num'), block)
                self.set_text(node, match.group('rest'))
            return
        if name:
            num = match.group('num')
            if name == 'greek' and self.is_roman_in_greek(num):
                name = 'roman'
                num = num.replace('ι', 'i')
            node = self.open_item(name, '(%s)' % num, block)
            rest = match.group('rest')
            # «10.(α)» и «(1)(α)»: сразу следующий уровень в той же строке
            inner_name, inner = match_item(rest)
            if inner_name and LEVELS[inner_name] > LEVELS[name]:
                node = self.open_item(inner_name, '(%s)' % inner.group('num'), block)
                rest = inner.group('rest')
            self.set_text(node, rest)
            return
        self.add_unnumbered(block)

    def is_roman_in_greek(self, num):
        """«(ι)», «(ιι)» греческой йотой вместо латинской «i», если перед ним не было «(θ)»."""
        if set(num) - set('ιvx'):
            return False
        siblings = [node.num_plain() for node in self.stack[-1].children]
        if len(self.stack) > 1 and self.stack[-1].level >= LEVELS['greek']:
            siblings = [node.num_plain() for node in self.stack[-2].children]
        return 'θ' not in siblings

    def continues_schedule_numbering(self, num):
        if not self.in_schedules or not num.isdigit():
            return False
        tops = [node for node in self.container.children if node.kind == 'par']
        if not tops or tops[-1].num_plain() != str(int(num) - 1):
            return False
        return not any(node.num_plain() == str(int(num) - 1) for node in tops[-1].children)

    def drop_shown_deletion(self, parent, node):
        """Удалённый пункт, оставленный в сводном тексте с примечанием «Σ.Σ.: … διαγραφή»,
        уступает номер новому пункту с тем же номером; его текст берётся из изменяющего акта."""
        for sibling in list(parent.children):
            if sibling.num_plain() == node.num_plain() and sibling.text().startswith('Σ.Σ.'):
                parent.children.remove(sibling)
                self.dropped.append(sibling)

    def open_item(self, name, num, block):
        level = LEVELS[name]
        if (
            len(self.stack) > 1
            and self.stack[-1].unnumbered
            and not opens_list(self.stack[-1])
        ):
            # пункт после безномерного абзаца продолжает список родителя
            self.stack.pop()
        while len(self.stack) > 1 and self.stack[-1].level >= level:
            if self.stack[-1].unnumbered and opens_list(self.stack[-1]):
                # «Νοείται περαιτέρω ότι …:» и пункты под ним: список внутри абзаца
                break
            self.stack.pop()
        parent = self.stack[-1]
        if name == 'top':
            parent = self.container
            self.stack = [self.container]
        node = Node(
            self.kinds()[name],
            num=num,
            indent=block.indent,
            level=level,
            heading=' '.join(self.pending_heading) if name == 'top' else '',
        )
        self.pending_heading = []
        self.drop_shown_deletion(parent, node)
        parent.add(node)
        self.stack.append(node)
        return node

    def set_text(self, node, text):
        if REPEALED_RE.match(text.strip()):
            node.repealed = True
        if text.strip():
            node.paragraphs.append(text.strip())

    def add_unnumbered(self, block):
        if self.pending_heading:
            # заголовок, за которым не последовал пункт, — обычный текст
            for text in self.pending_heading:
                self.attach_paragraph(text, block.indent)
            self.pending_heading = []
        self.attach_paragraph(block.text, block.indent)

    def attach_paragraph(self, text, indent):
        if self.in_schedules and text.startswith(EXCEPTION_START) and len(self.stack) > 1:
            # каждая оговорка «Εξαιρούνται…» вводилась и истекала отдельно — свой фрагмент
            del self.stack[2:]
            self.open_unnumbered(self.stack[1], text, indent)
            return
        while len(self.stack) > 1 and self.stack[-1].indent > indent:
            self.stack.pop()
        while (
            len(self.stack) > 1
            and self.stack[-1].unnumbered
            and not (
                # перечень без номеров внутри оговорки приложения («Εξαιρούνται … τα ακόλουθα»)
                self.in_schedules
                and not self.stack[-1].parent.unnumbered
                and opens_list(self.stack[-1])
                and all(child.unnumbered for child in self.stack[-1].children)
            )
        ):
            self.stack.pop()
        owner = self.stack[-1]
        if not owner.children and not owner.paragraphs:
            # вводный абзац — текст самого пункта
            owner.paragraphs.append(text)
            return
        # каждый следующий абзац без номера — свой фрагмент (ответ координатора на вопрос 1)
        self.open_unnumbered(owner, text, indent)

    def open_unnumbered(self, owner, text, indent):
        node = Node(
            'sub',
            unnumbered=True,
            indent=indent,
            level=owner.level + 0.5,
        )
        owner.add(node)
        node.paragraphs.append(text)
        self.stack.append(node)


TERM_RE = re.compile(r'^(?:"|\'\'|«|“)(?P<term>[^"«»“”\']+)(?:"|\'\'|»|”)')
LIST_END_RE = re.compile(r'(\sκαι|\sή)$')


def mark_definitions(article):
    """Определения ст. 2(1): абзац с термином — `def_<slug>`, оговорки и продолжения —
    его потомки `sub_K`; абзацы после перечня — `sub_K` самого пункта."""
    owner = next(node for node in article.children if node.token == 'par_1')
    paragraphs = owner.children
    owner.children = []
    current = None
    closing = None
    for node in paragraphs:
        text = node.text()
        match = TERM_RE.match(text)
        if match:
            node.kind = 'def'
            node.unnumbered = False
            node.token = 'def_' + numbering.slug(match.group('term'))
            owner.add(node)
            current = node
            continue
        if closing is not None and LIST_END_RE.search(closing.paragraphs[-1]):
            # «… Μέρος ή» / «Παράρτημα …» — одно предложение, разорванное на две строки
            closing.paragraphs[-1] += ' ' + text
            continue
        after_list = current is not None and current.text().rstrip().endswith('και')
        if current is None or after_list or closing is not None:
            node.token = ''
            node.order_key = ''
            closing = owner.add(node)
            continue
        node.token = ''
        node.order_key = ''
        current.add(node)


def parse(path, act_uid):
    parser = Parser(act_uid)
    root = parser.parse(layout.pdf_lines(path))
    article = next(node for node in root.walk() if node.eid == 'art_2')
    mark_definitions(article)
    return root, parser.history
