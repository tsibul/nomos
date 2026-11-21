"""Formex 4 (CELLAR) → дерево фрагментов: сводная редакция (CONS.ACT) или акт OJ (ACT).

Адреса (eid) — по номерам самого текста: «Article 24a» → `art_24a`, «2.» → `par_2`,
«(a)» → `pnt_a`, «CHAPTER V» → `cpt_V`, «Section 2» → `sct_2`, «ANNEX I» → `anx_I`.
Абзацы без номера (ALINEA) — `sub_N`, если у владельца их больше одного; единственный
абзац — текст самого владельца. Текст после списка в том же абзаце начинает новый абзац
(«The values referred to in points (e) to (g) of the first subparagraph…»).

Метки изменений сводного текста — инструкции обработки `<?CLG.MDFO …?>` … `<?CLG.MDFC …?>`;
у фрагмента запоминаются метки, внутри которых он начинается, и метки в его тексте.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# заголовок раздела: «CHAPTER V», «Section 2a», «TITLE I», «PART A», «Subsection 1»
DIVISION_WORDS = {
    'title': 'tis',
    'chapter': 'cpt',
    'section': 'sct',
    'subsection': 'sbs',
    'part': 'prt',
    'τιτλοσ': 'tis',
    'κεφαλαιο': 'cpt',
    'τμημα': 'sct',
    'υποτμημα': 'sbs',
    'μεροσ': 'prt',
}
DIVISION_RE = re.compile(r'^\s*(\S+)\s+([0-9IVXLΙΧ]+[A-Za-zα-ω]?)\b')
ARTICLE_RE = re.compile(r'(\d+[a-z]*)\s*$', re.I)
ANNEX_RE = re.compile(r'([IVXLΙΧ]+[A-Za-z]?|\d+)\s*$')

# элементы-блоки: всё прочее внутри текста — строчная разметка
BLOCKS = {'ALINEA', 'P', 'LIST', 'DLIST', 'TBL', 'QUOT.S', 'PARAG', 'ITEM', 'NP', 'TXT', 'GR.SEQ'}
SKIP = {'TOC', 'INFO.CONSLEG', 'INFO.PROD', 'BIB.INSTANCE', 'CONS.DOC', 'PREAMBLE', 'FINAL'}
INLINE_TAGS = {
    'BOLD': 'strong',
    'ITALIC': 'em',
    'SUP': 'sup',
    'SUB': 'sub',
}
LATIN_FOR_GREEK = str.maketrans('ΙΧ', 'IX')


@dataclass
class Marker:
    """Метка изменения: CELEX изменяющего акта, его положение, действие."""

    id: str
    doc: str
    loc: str
    action: str
    level: str


@dataclass
class Node:
    kind: str
    token: str
    num: str = ''
    heading: str = ''
    parent: 'Node' = None
    children: list = field(default_factory=list)
    paragraphs: list = field(default_factory=list)
    # метки, внутри которых начинается фрагмент, и метки в его тексте
    markers: list = field(default_factory=list)
    # метки удаления (DELETED) среди прямого содержимого
    deleted: list = field(default_factory=list)

    @property
    def eid(self):
        own = '%s_%s' % (self.kind, self.token)
        if self.parent is None or self.parent.kind == 'act':
            return own
        if self.kind in ('art',) or self.parent.kind in CONTAINERS and self.kind == 'anx':
            return own
        return '%s.%s' % (self.parent.eid, own)

    @property
    def parent_eid(self):
        if self.parent is None or self.parent.kind == 'act':
            return ''
        return self.parent.eid

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()


CONTAINERS = {'tis', 'cpt', 'sct', 'sbs', 'prt'}


@dataclass
class Document:
    root: Node
    info: dict
    lang: str
    # метки по id: для отчёта
    markers: dict = field(default_factory=dict)
    report: list = field(default_factory=list)

    def nodes(self):
        return list(self.root.walk())

    def by_eid(self):
        return {node.eid: node for node in self.nodes()}


def parse(path, lang):
    builder = ET.TreeBuilder(insert_pis=True)
    parser = ET.XMLParser(target=builder)
    with open(path, 'rb') as stream:
        parser.feed(stream.read())
    tree = parser.close()
    return Reader(tree, lang).read()


def plain(element):
    """Текст элемента без разметки и без инструкций обработки."""
    parts = []
    if element.tag is ET.ProcessingInstruction:
        return element.tail or ''
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tag is not ET.ProcessingInstruction and child.tag != 'NOTE':
            parts.append(inner_plain(child))
        if child.tail:
            parts.append(child.tail)
    return ' '.join(''.join(parts).split())


def inner_plain(element):
    if element.tag is ET.ProcessingInstruction or element.tag == 'NOTE':
        return ''
    if element.tag in ('QUOT.START', 'QUOT.END'):
        return quote_char(element)
    parts = [element.text or '']
    for child in element:
        parts.append(inner_plain(child))
        parts.append(child.tail or '')
    return ''.join(parts)


def quote_char(element):
    code = element.get('CODE')
    return chr(int(code, 16)) if code else '"'


def pi_attrs(element):
    text = element.text or ''
    name, _, rest = text.partition(' ')
    return name, dict(re.findall(r'([A-Z.]+)="([^"]*)"', rest))


class Reader:
    def __init__(self, tree, lang):
        self.tree = tree
        self.lang = lang
        self.open = []
        self.markers = {}
        self.report = []
        self.footnotes = 0

    def read(self):
        info = {}
        consleg = self.tree.find('INFO.CONSLEG')
        if consleg is not None:
            info = dict(consleg.attrib)
        title = self.tree.find('.//TITLE/TI')
        if title is not None:
            info['title'] = ' '.join(plain(line) for line in title.findall('P'))
        root = Node('act', '')
        terms = self.tree.find('.//ENACTING.TERMS')
        if terms is None:
            raise ValueError('нет ENACTING.TERMS')
        self.container(terms, root)
        for annex in self.annexes():
            self.annex(annex, root)
        order_keys(root)
        document = Document(root, info, self.lang, self.markers, self.report)
        return document

    def annexes(self):
        """Приложения сводного текста (CONS.ANNEX) или акта OJ (ANNEX) по порядку."""
        quoted = set()
        for element in self.tree.iter('QUOT.S'):
            quoted.update(element.iter())
        found = []
        for element in self.tree.iter():
            if element.tag in ('CONS.ANNEX', 'ANNEX') and element not in quoted:
                found.append(element)
        return found

    # метки изменений

    def marker(self, element):
        """Инструкция обработки внутри обхода: открыть или закрыть метку."""
        name, attrs = pi_attrs(element)
        if name == 'CLG.MDFO':
            marker = Marker(
                id=attrs.get('ID', ''),
                doc=attrs.get('ACTIVE.DOC', ''),
                loc=attrs.get('ACTIVE.LOC', ''),
                action=attrs.get('ACTION', ''),
                level=attrs.get('LEVEL', ''),
            )
            self.markers[marker.id] = marker
            self.open.append(marker)
            return marker
        if name == 'CLG.MDFC':
            ref = attrs.get('IDREF')
            for index in range(len(self.open) - 1, -1, -1):
                if self.open[index].id == ref:
                    del self.open[index]
                    break
        return None

    def start(self, node, parent):
        node.parent = parent
        node.markers = list(self.open)
        parent.children.append(node)
        return node

    # структура

    def container(self, element, parent):
        """Содержимое ENACTING.TERMS или DIVISION: разделы и статьи."""
        for child in element:
            if child.tag is ET.ProcessingInstruction:
                opened = self.marker(child)
                if opened is not None and opened.action == 'DELETED':
                    parent.deleted.append(opened)
                continue
            if child.tag == 'DIVISION':
                self.division(child, parent)
            elif child.tag == 'ARTICLE':
                self.article(child, parent)
            elif child.tag == 'TITLE':
                continue
            else:
                self.report.append('%s: пропущен элемент %s' % (parent.eid or 'акт', child.tag))

    def division(self, element, parent):
        title = element.find('TITLE')
        label = ''
        heading = ''
        if title is not None and title.find('TI') is not None:
            label = plain(title.find('TI'))
        if title is not None and title.find('STI') is not None:
            heading = plain(title.find('STI'))
        match = DIVISION_RE.match(label)
        word = strip_accents(match.group(1).lower()) if match else ''
        kind = DIVISION_WORDS.get(word)
        if kind is None:
            self.report.append('%s: раздел без номера «%s»' % (parent.eid or 'акт', label))
            kind, token = 'sct', str(len(parent.children) + 1)
        else:
            token = match.group(2).translate(LATIN_FOR_GREEK)
        node = self.start(Node(kind, token, num=token), parent)
        node.heading = ' '.join(part for part in (label, heading) if part)
        self.container(element, node)

    def article(self, element, parent):
        label = plain(element.find('TI.ART')) if element.find('TI.ART') is not None else ''
        token = element.get('IDENTIFIER', '').lstrip('0').lower()
        if not token:
            match = ARTICLE_RE.search(label)
            token = match.group(1).lower() if match else str(len(parent.children) + 1)
            self.report.append('art %s: нет IDENTIFIER, заголовок «%s»' % (token, label))
        node = self.start(Node('art', token, num=token), parent)
        sti = element.find('STI.ART')
        if sti is not None:
            node.heading = plain(sti)
        parags = [child for child in element if child.tag == 'PARAG']
        if parags:
            for child in element:
                if child.tag is ET.ProcessingInstruction:
                    opened = self.marker(child)
                    if opened is not None and opened.action == 'DELETED':
                        node.deleted.append(opened)
                elif child.tag == 'PARAG':
                    self.parag(child, node)
                elif child.tag not in ('TI.ART', 'STI.ART'):
                    self.report.append('%s: пропущен элемент %s' % (node.eid, child.tag))
        else:
            self.content(node, [
                child for child in element if child.tag not in ('TI.ART', 'STI.ART')
            ])

    def parag(self, element, parent):
        number = element.find('NO.PARAG')
        num = plain(number) if number is not None else ''
        token = num.strip().rstrip('.').lower() or str(len(parent.children) + 1)
        node = self.start(Node('par', token, num=num.strip()), parent)
        self.content(node, [child for child in element if child.tag != 'NO.PARAG'])

    def annex(self, element, parent):
        for child in element:
            if child.tag is ET.ProcessingInstruction:
                self.marker(child)
                continue
            break
        title = element.find('TITLE')
        lines = [plain(p) for p in title.iter('P')] if title is not None else []
        label = lines[0] if lines else ''
        match = ANNEX_RE.search(label)
        token = match.group(1).translate(LATIN_FOR_GREEK) if match else str(len(parent.children))
        node = self.start(Node('anx', token, num=token), parent)
        node.heading = ' '.join(lines)
        contents = element.find('CONTENTS')
        if contents is not None:
            self.content(node, list(contents))

    # содержимое: абзацы, списки, таблицы

    def content(self, owner, elements):
        """Блоки владельца → его текст, абзацы sub_N, пункты списков."""
        segments = []
        for element in elements:
            if element.tag is ET.ProcessingInstruction:
                opened = self.marker(element)
                if opened is not None:
                    owner.markers.append(opened)
                    if opened.action == 'DELETED':
                        owner.deleted.append(opened)
                continue
            if element.tag == 'ALINEA':
                segments.append(Segment())
                self.alinea(element, segments)
            elif element.tag in ('P', 'LIST', 'DLIST', 'TBL', 'QUOT.S', 'GR.SEQ', 'TXT'):
                if not segments or (element.tag in ('P', 'TXT') and segments[-1].lists):
                    segments.append(Segment())
                self.block(element, segments)
            else:
                self.report.append('%s: пропущен элемент %s' % (owner.eid, element.tag))
        if len(segments) == 1:
            self.fill(owner, segments[0])
            return
        for index, segment in enumerate(segments, start=1):
            node = self.start(Node('sub', str(index)), owner)
            node.markers = owner.markers + segment.markers
            self.fill(node, segment)

    def alinea(self, element, segments):
        if not any(child.tag in BLOCKS for child in element):
            segments[-1].add_text(self.inline(element), self.markers_in(element))
            return
        if (element.text or '').strip():
            segments[-1].add_text(escape(element.text.strip()), [])
        for child in element:
            if child.tag is ET.ProcessingInstruction:
                opened = self.marker(child)
                if opened is not None:
                    segments[-1].markers.append(opened)
                continue
            if child.tag in ('P', 'TXT') and segments[-1].lists:
                segments.append(Segment())
            self.block(child, segments)

    def block(self, element, segments):
        segment = segments[-1]
        if element.tag in ('LIST', 'DLIST'):
            segment.lists.append(element)
        elif element.tag == 'TBL':
            segment.lists.append(element)
        elif element.tag == 'QUOT.S':
            segment.texts.extend(self.quoted(element))
        elif element.tag in ('P', 'TXT', 'GR.SEQ'):
            inner_blocks = [
                child for child in element if child.tag in ('LIST', 'DLIST', 'QUOT.S', 'TBL')
            ]
            if inner_blocks:
                self.mixed(element, segments)
            else:
                segment.add_text(self.inline(element), self.markers_in(element))
        else:
            segment.add_text(self.inline(element), self.markers_in(element))

    def mixed(self, element, segments):
        """P со списком или цитатой внутри: текст до блока, блок, текст после."""
        buffer = [escape(element.text or '')]
        for child in element:
            if child.tag in ('LIST', 'DLIST', 'TBL', 'QUOT.S'):
                text = ''.join(buffer).strip()
                if text:
                    segments[-1].add_text(text, [])
                buffer = []
                if child.tag == 'QUOT.S':
                    segments[-1].texts.extend(self.quoted(child))
                else:
                    segments[-1].lists.append(child)
            elif child.tag is ET.ProcessingInstruction:
                opened = self.marker(child)
                if opened is not None:
                    segments[-1].markers.append(opened)
            else:
                buffer.append(self.inline_element(child))
            buffer.append(escape(child.tail or ''))
        text = ' '.join(''.join(buffer).split())
        if text:
            if segments[-1].texts and text in (';', '.', ',', ':'):
                segments[-1].texts[-1] += text
            else:
                segments[-1].add_text(text, [])

    def fill(self, node, segment):
        node.paragraphs.extend(segment.texts)
        node.markers.extend(marker for marker in segment.markers if marker not in node.markers)
        for block in segment.lists:
            if block.tag == 'TBL':
                self.table(block, node)
            else:
                self.items(block, node)

    def items(self, element, owner):
        dash = element.get('TYPE') == 'DASH'
        position = 0
        for item in element:
            if item.tag is ET.ProcessingInstruction:
                opened = self.marker(item)
                if opened is not None and opened.action == 'DELETED':
                    owner.deleted.append(opened)
                continue
            if item.tag == 'DLIST.ITEM':
                position += 1
                self.definition(item, owner, position)
                continue
            if item.tag != 'ITEM':
                self.report.append('%s: в списке элемент %s' % (owner.eid, item.tag))
                continue
            position += 1
            np = item.find('NP')
            number = np.find('NO.P') if np is not None else None
            num = inner_plain(number).strip().strip('‘’“”"') if number is not None else ''
            if dash or not num:
                kind, token = ('ind' if dash else 'pnt'), str(position)
            else:
                kind, token = 'pnt', item_token(num)
            node = self.start(Node(kind, token, num=num), owner)
            if np is not None:
                blocks = [child for child in np if child.tag != 'NO.P']
            else:
                blocks = list(item)
            self.content(node, blocks)

    def definition(self, item, owner, position):
        """Пункт определения (DLIST.ITEM): «α)» + термин + определение."""
        prefix = item.find('PREFIX')
        num = inner_plain(prefix).strip() if prefix is not None else ''
        node = self.start(Node('pnt', str(position), num=num), owner)
        term = item.find('TERM')
        term_text = self.inline(term) if term is not None else ''
        definition = item.find('DEFINITION')
        if definition is None:
            node.paragraphs.append(term_text)
            return
        # определение — как абзац P: текст, список, хвост
        definition.tag = 'P'
        self.content(node, [definition])
        if node.paragraphs:
            node.paragraphs[0] = ' '.join(part for part in (term_text, node.paragraphs[0]) if part)
        else:
            node.paragraphs.append(term_text)

    def table(self, element, owner):
        number = element.get('NO.SEQ', '').lstrip('0') or str(len(owner.children) + 1)
        node = self.start(Node('tbl', number), owner)
        title = element.find('TITLE')
        if title is not None:
            node.heading = plain(title)
        rows = []
        for row in element.iter('ROW'):
            cells = []
            for cell in row.findall('CELL'):
                tag = 'th' if cell.get('TYPE') == 'HEADER' or row.get('TYPE') == 'HEADER' else 'td'
                span = ''
                if cell.get('COLSPAN'):
                    span += ' colspan="%s"' % cell.get('COLSPAN')
                if cell.get('ROWSPAN'):
                    span += ' rowspan="%s"' % cell.get('ROWSPAN')
                cells.append('<%s%s>%s</%s>' % (tag, span, self.cell(cell), tag))
            rows.append('<tr>%s</tr>' % ''.join(cells))
        node.paragraphs.append('<table>%s</table>' % ''.join(rows))

    def cell(self, cell):
        if any(child.tag in BLOCKS for child in cell):
            parts = []
            for child in cell:
                if child.tag in BLOCKS:
                    parts.append(self.inline(child) if child.tag != 'LIST' else ' '.join(
                        self.inline(item) for item in child.iter('NP')
                    ))
            return ' '.join(part for part in parts if part)
        return self.inline(cell)

    def quoted(self, element):
        """Цитата изменяющего акта (QUOT.S): абзацы `<q>…</q>` без деления на фрагменты."""
        lines = []
        for block in leaf_blocks(element):
            text = self.inline(block)
            if text:
                lines.append(text)
        return ['<q>%s</q>' % line for line in lines]

    # строчная разметка

    def markers_in(self, element):
        found = []
        for child in element.iter():
            if child.tag is ET.ProcessingInstruction:
                opened = self.marker(child)
                if opened is not None:
                    found.append(opened)
        return found

    def inline(self, element):
        parts = [escape(element.text or '')]
        for child in element:
            parts.append(self.inline_element(child))
            parts.append(escape(child.tail or ''))
        return ' '.join(''.join(parts).split())

    def inline_element(self, element):
        tag = element.tag
        if tag is ET.ProcessingInstruction:
            return ''
        if tag in ('QUOT.START', 'QUOT.END'):
            return quote_char(element)
        if tag == 'NOTE':
            self.footnotes += 1
            body = ' '.join(self.inline(child) for child in element if child.tag == 'P')
            return '<fn id="fn%d">%s</fn>' % (self.footnotes, body or self.inline(element))
        if tag == 'HT':
            html = INLINE_TAGS.get(element.get('TYPE', ''))
            inner = self.inline(element)
            return '<%s>%s</%s>' % (html, inner, html) if html else inner
        if tag in ('P', 'ALINEA', 'TXT'):
            return ' ' + self.inline(element) + ' '
        return self.inline(element)


@dataclass
class Segment:
    """Абзац без номера: тексты, затем списки и таблицы."""

    texts: list = field(default_factory=list)
    lists: list = field(default_factory=list)
    markers: list = field(default_factory=list)

    def add_text(self, text, markers):
        if text:
            self.texts.append(text)
        self.markers.extend(markers)


# элементы, которые в цитате делят текст на абзацы
QUOTED_BLOCKS = BLOCKS | {
    'ARTICLE',
    'DIVISION',
    'TITLE',
    'TI',
    'STI',
    'TI.ART',
    'STI.ART',
    'NO.PARAG',
    'NO.P',
    'CONTENTS',
    'ANNEX',
}


def leaf_blocks(element):
    """Блоки цитаты, внутри которых нет других блоков: по порядку документа."""
    found = []
    for child in element:
        if child.tag is ET.ProcessingInstruction or child.tag not in QUOTED_BLOCKS:
            continue
        if any(inner.tag in QUOTED_BLOCKS for inner in child):
            found.extend(leaf_blocks(child))
        else:
            found.append(child)
    return merge_numbers(found)


def merge_numbers(blocks):
    """«4.» перед абзацем — в один абзац с ним."""
    merged = []
    pending = None
    for block in blocks:
        if block.tag in ('NO.PARAG', 'NO.P'):
            pending = block
            continue
        if pending is not None:
            wrapper = ET.Element('P')
            wrapper.append(pending)
            pending.tail = ' '
            wrapper.append(block)
            merged.append(wrapper)
            pending = None
            continue
        merged.append(block)
    if pending is not None:
        merged.append(pending)
    return merged


def item_token(num):
    """«(a)» → a, «(iv)» → iv, «(1)» и «1.» → 1, «(10c)» → 10c."""
    token = num.strip().strip('()').rstrip('.').strip().lower()
    token = re.sub(r'\s+', '', token)
    return token or '0'


def strip_accents(word):
    import unicodedata
    plain_word = unicodedata.normalize('NFD', word)
    plain_word = ''.join(char for char in plain_word if not unicodedata.combining(char))
    return plain_word.replace('ς', 'σ')


def order_keys(node):
    for index, child in enumerate(node.children, start=1):
        child.order_key = '%04d' % index
        order_keys(child)
