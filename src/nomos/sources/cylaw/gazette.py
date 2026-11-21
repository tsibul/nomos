"""Изменяющий акт из Επίσημη Εφημερίδα: поля (маргиналии) и тело, статьи и их пункты.

Слева — колонка маргиналий (заголовок статьи), справа — текст. Цитаты «…» — вставляемый
или заменяющий текст; внутри цитаты нумерация не разбирается (это пункты другого акта).
"""

import re

from . import layout
from .tree import Node

DATE_RE = re.compile(r'^\s*\d{1,2}\.\d{1,2}\.\d{4}\.?\s*$')
LABEL_RE = re.compile(r'^(\(\w{1,5}\)|\d+\.)$')
GAP_RE = re.compile(r'\S(\s{3,})\S')
FIRST_ARTICLE_RE = re.compile(r'(?<!\S)(?P<num>[12])\.\s*(Ο|Η|Το|Οι)\s+παρ')
ARTICLE_RE = re.compile(r'^(?P<num>\d+)\.\s*(?:[-—–]\s*)?(?P<rest>.*)$')
POINT_RE = re.compile(r'^\((?P<num>[α-ω]{1,3})\)\s*(?P<rest>.*)$')
ITEM_RE = re.compile(r'^(«\s*)?(\(\w{1,5}\)|\d+[Α-Ω]{0,3}\.)')
END_RE = re.compile(r'^\s*(_{5,}|[TΤ]υπώθηκε)')
HEADER_RE = re.compile(r'^\s*(Ε\.Ε\.\s*Παρ\.|Αρ\.\s*\d+,|Αριθμός\s+\d+|\d{3,5}\s*$)')
# перенос слова в конце строки: «Τροπο- ποιητικός» → «Τροποποιητικός»
HYPHEN_RE = re.compile(r'([α-ρσ-ωάέήίόύώϊϋΐΰ])- ([α-ωάέήίόύώϊϋΐΰ])')
TITLE_RE = re.compile(r'ως (?:ο|η|το|οι) (περί .+? (?:του|των) \d{4})')


def split_line(line, column):
    """Строка → (поле, текст). Короткая строка без разрыва левее текста — поле."""
    if not line.strip():
        return '', ''
    if DATE_RE.match(line):
        return line.strip(), ''
    match = GAP_RE.search(line)
    if match and match.start(1) < column:
        left = line[:match.start(1) + 1].strip()
        right = line[match.end(1) - 1:].strip()
        if LABEL_RE.match(left):
            # «(γ)   με την …» — номер пункта, отбитый пробелами, а не поле
            return '', left + ' ' + right
        return left, right
    indent = len(line) - len(line.lstrip())
    if indent < column - 3 and len(line.rstrip()) < column:
        return line.strip(), ''
    return '', line.strip()


def body_column(lines):
    for line in lines:
        match = FIRST_ARTICLE_RE.search(line)
        if match:
            # 102/2026 и 103/2026 нумеруют статьи с 2: номер берётся как в акте
            return match.start(), int(match.group('num'))
    raise ValueError('не найдена статья 1')


class GazetteParser:
    def __init__(self, act_uid):
        self.act_uid = act_uid
        self.root = Node(kind='act')
        self.preamble = self.root.add(Node(kind='cit', token='cit_1'))
        self.article = None
        self.point = None
        self.heading_open = False
        self.quote_depth = 0
        self.paragraph = []
        self.expected = 1

    def parse(self, lines):
        column, self.expected = body_column(lines)
        for line in lines:
            if END_RE.match(line) and self.article is not None:
                break
            if HEADER_RE.match(line):
                continue
            margin, body = split_line(line, column)
            self.feed(margin, body)
        self.flush()
        return self.root

    def feed(self, margin, body):
        if body and self.quote_depth == 0:
            match = ARTICLE_RE.match(body)
            if match and match.group('num') == str(self.expected):
                self.open_article(match.group('num'), margin)
                body = match.group('rest')
                margin = ''
            point = POINT_RE.match(body) if self.article else None
            if point and self.ends_sentence():
                self.open_point(point.group('num'))
                body = point.group('rest')
        if margin and self.heading_open:
            self.add_heading(margin)
        if not body:
            return
        if self.paragraph and self.quote_depth > 0 and ITEM_RE.match(body):
            if self.ends_sentence():
                self.flush()
        self.paragraph.append(body)
        self.quote_depth += body.count('«') - body.count('»')
        if self.quote_depth < 0:
            self.quote_depth = 0
        if self.quote_depth == 0 and body.endswith(('».', '»:', '»·', '»;', '»∙')):
            self.flush()

    def ends_sentence(self):
        return not self.paragraph or layout.ends_sentence(self.paragraph[-1])

    def open_article(self, num, margin):
        self.flush()
        self.article = self.root.add(Node(kind='art', num=num))
        self.point = None
        self.expected += 1
        self.heading_open = True
        if margin:
            self.add_heading(margin)

    def add_heading(self, margin):
        heading = self.article.heading
        self.article.heading = (heading + ' ' + margin).strip()
        if self.article.heading.endswith('.'):
            self.heading_open = False

    def open_point(self, num):
        self.flush()
        self.point = self.article.add(Node(kind='pnt', num='(%s)' % num))

    def owner(self):
        if self.point is not None:
            return self.point
        if self.article is not None:
            return self.article
        return self.preamble

    def flush(self):
        if not self.paragraph:
            return
        text = HYPHEN_RE.sub(r'\1\2', layout.join_lines(self.paragraph))
        self.owner().paragraphs.append(text)
        self.paragraph = []


def parse(path, act_uid):
    """PDF изменяющего акта → (дерево, краткий заголовок из ст. 1)."""
    parser = GazetteParser(act_uid)
    root = parser.parse(layout.pdf_lines(path))
    title = ''
    articles = [node for node in root.children if node.kind == 'art']
    if articles:
        match = TITLE_RE.search(articles[0].text())
        if match:
            title = match.group(1)
    return root, title


QUOTE_RE = re.compile(r'«([^«»]*)»')


def quote_markup(markup):
    """«…» → «<q>…</q>» в уже экранированном тексте."""
    return QUOTE_RE.sub(lambda match: '«<q>%s</q>»' % match.group(1), markup)
